from contextlib import contextmanager
import io
import json
import multiprocessing
import os
from pathlib import Path
import plistlib
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from back.daily_lock import forward_lock
from back.daily_forward_service import DailyForwardService
from back.daily_forward_client import ForwardClient,ForwardBudget,ledger_usage
from back.daily_forward import claim_request,save_baselines,prepare_day
from back.daily_store import DailyStore
from back.db_writer import DBWriter
from back.data import DataError
from back.experiment import ActivityGate
from back.evolution_run import ForbiddenClient
from scripts.daily_forward import main,run_once
from tests.test_daily_forward import fixture,moment,DAY
from tests.test_daily_jev import response
from tests.helpers import FakeServer,KEY

ROOT=Path(__file__).resolve().parents[1]


def shell_process(path,ledger,entered,release,result):
    """Actual separate shell-style worker: hold a real fake-server HTTP in flight."""
    os.environ['TYPESAFE_API_KEY']=KEY
    server=FakeServer();server.queue(response());writer=DBWriter(path);writer.ready.result(5)
    class BlockingOpener:
        def open(self,request,timeout):
            entered.set()
            if not release.wait(10):raise RuntimeError('fixture_timeout')
            return server.open(request,timeout)
    now=lambda:moment(DAY+'T17:00:00')
    budget=ForwardBudget(path=ledger,now=now)
    service=DailyForwardService(writer,ActivityGate(),autostart=False,now=now,
        budget=budget,client=ForwardClient(budget,opener=BlockingOpener()))
    try:result.put(service.cycle())
    finally:service.close();writer.close();writer.thread.join(5);server.close()


def hold_lock_process(path,ready):
    with forward_lock(path):
        ready.set()
        import time
        time.sleep(30)


class DailyLockTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/'db.sqlite3';self.path.touch()
    def test_nonblocking_exclusion_symlink_alias_release_and_stale_lease(self):
        alias=self.path.parent/'alias.sqlite3';alias.symlink_to(self.path)
        with forward_lock(self.path) as lease:
            with self.assertRaisesRegex(DataError,'busy'):
                with forward_lock(alias):pass
            with self.assertRaisesRegex(DataError,'required'):lease.require(self.path.parent/'another.sqlite3')
        with self.assertRaisesRegex(DataError,'required'):lease.require(self.path)
        lockfile=self.path.with_name(self.path.name+'.daily-forward.lock');inode=lockfile.stat().st_ino
        with forward_lock(alias):pass
        self.assertEqual(lockfile.stat().st_ino,inode)
        self.assertEqual(lockfile.stat().st_mode&0o777,0o600)
    def test_process_death_releases_lock_without_unlink(self):
        ctx=multiprocessing.get_context('spawn');ready=ctx.Event();p=ctx.Process(target=hold_lock_process,args=(self.path,ready))
        p.start()
        try:
            self.assertTrue(ready.wait(5))
            with self.assertRaisesRegex(DataError,'busy'):
                with forward_lock(self.path):pass
        finally:p.terminate();p.join(5)
        with forward_lock(self.path):pass


class DailyCliTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.template=DailyStore(':memory:');cls.row,cls.bundle=fixture(cls.template)
    @classmethod
    def tearDownClass(cls):cls.template.close()
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/'db.sqlite3';self.ledger=Path(self.temp.name)/'ledger.sqlite3'
        with DailyStore(self.path) as s:self.template.db.backup(s.db)
        env=patch.dict(os.environ,FUGLE_API_KEY=KEY,TYPESAFE_API_KEY=KEY);env.start();self.addCleanup(env.stop)
    def factory(self,*,now=None,client=None,budget=None,sync=None):
        def make(writer,gate,**kwargs):
            self.assertIs(kwargs['autostart'],False)
            service=DailyForwardService(writer,gate,now=now or (lambda:moment(DAY+'T17:00:00')),
                client=client,budget=budget,**kwargs)
            service._sync=sync or (lambda row:None)
            return service
        return make
    def test_cli_and_shell_share_process_lock_and_persistent_reservation(self):
        ctx=multiprocessing.get_context('spawn');entered=ctx.Event();release=ctx.Event();result=ctx.Queue()
        p=ctx.Process(target=shell_process,args=(self.path,self.ledger,entered,release,result));p.start()
        try:
            self.assertTrue(entered.wait(10))
            with DailyStore(self.path,readonly=True) as s:
                self.assertEqual(s.db.execute('SELECT jev_state FROM d_forward_days').fetchone()[0],'reserved')
            with patch('scripts.daily_forward.DBWriter',side_effect=AssertionError('must lock before writer')):
                blocked=run_once(self.path,ledger_path=self.ledger)
            self.assertEqual(blocked['status'],'busy');self.assertEqual(blocked['jev_http_calls'],0)
            release.set();finished=result.get(timeout=10)
            self.assertEqual((finished['new_predictions'],finished['jev_http_calls'],finished['ledger_used']),(12,1,1))
        finally:release.set();p.join(10);result.close()
        self.assertEqual(p.exitcode,0)
        with ForbiddenClient().guard():
            rerun=run_once(self.path,ledger_path=self.ledger,service_factory=self.factory())
        self.assertEqual(rerun['status'],'complete');self.assertEqual(rerun['new_predictions'],0)
        self.assertEqual(rerun['jev_http_calls'],0);self.assertEqual(rerun['ledger_used'],1)
    def test_shell_respects_cli_lock_before_any_db_work(self):
        writer=DBWriter(self.path);writer.ready.result(5)
        service=DailyForwardService(writer,ActivityGate(),autostart=False,ledger_path=self.ledger)
        try:
            with forward_lock(self.path),patch.object(service,'_cycle',side_effect=AssertionError('must not enter')):
                value=service.cycle(sync=True)
            self.assertEqual(value['status'],'busy');self.assertEqual(value['new_predictions'],0)
        finally:service.close();writer.close();writer.thread.join(5)
    def test_no_new_day_zero_jev_shared_sync_happens_first(self):
        seen=[]
        def sync(row):seen.append('sync')
        def factory(writer,gate,**kw):
            service=self.factory(now=lambda:moment('2026-09-27T17:00:00'),sync=sync)(writer,gate,**kw)
            original=service._write
            def write(fn):
                value=original(fn)
                if getattr(fn,'__name__','')=='<lambda>':seen.append('write')
                return value
            service._write=write
            return service
        with ForbiddenClient().guard():value=run_once(self.path,ledger_path=self.ledger,service_factory=factory)
        self.assertEqual(value['status'],'complete');self.assertEqual(seen[0],'sync')
        self.assertEqual(value['new_predictions'],0);self.assertEqual(value['scored_outcomes'],0)
        self.assertEqual(value['jev_http_calls'],0);self.assertEqual(value['ledger_used'],0)
        self.assertFalse(self.ledger.exists())
    def test_missing_each_key_no_writer_no_network_no_traceback(self):
        for missing in ('FUGLE_API_KEY','TYPESAFE_API_KEY'):
            env={k:KEY for k in ('FUGLE_API_KEY','TYPESAFE_API_KEY') if k!=missing}
            with patch.dict(os.environ,env,clear=True),patch('scripts.daily_forward.DBWriter',side_effect=AssertionError('no writer')),ForbiddenClient().guard():
                output=io.StringIO()
                rc=main(['--db',str(self.path)],stdout=output,runner=lambda p:run_once(p,ledger_path=self.ledger))
            self.assertEqual(rc,0);self.assertEqual(len(output.getvalue().splitlines()),1)
            data=json.loads(output.getvalue());self.assertEqual(data['status'],'missing_keys');self.assertEqual(data['missing_keys'],[missing])
            self.assertNotIn(KEY,output.getvalue());self.assertEqual(data['jev_http_calls'],0)
    def test_record_reservation_survives_process_failure_without_http(self):
        with DailyStore(self.path) as s:
            save_baselines(s,prepare_day(s,self.row,self.bundle,DAY),moment(DAY+'T17:00:00'));claim_request(s,DAY)
        with ForbiddenClient().guard():value=run_once(self.path,ledger_path=self.ledger,service_factory=self.factory())
        self.assertEqual(value['jev_http_calls'],0);self.assertEqual(value['status'],'partial')
        self.assertEqual(value['missing_jev_days'],1)
    def test_exact_summary_counts_include_committed_predictions_and_maturity(self):
        server=FakeServer();self.addCleanup(server.close);server.queue(response())
        budget=ForwardBudget(path=self.ledger,now=lambda:moment(DAY+'T17:00:00'))
        client=ForwardClient(budget,opener=server)
        value=run_once(self.path,ledger_path=self.ledger,service_factory=self.factory(client=client,budget=budget))
        self.assertEqual((value['new_predictions'],value['scored_outcomes'],value['jev_http_calls'],value['ledger_used']),(12,0,1,1))
        with DailyStore(self.path) as s:
            # Keep later bars for maturity; don't create additional origins in this fixture.
            original_days=s.db.execute('SELECT day FROM d_calendar WHERE day>?',(DAY,)).fetchall()
        from back.daily_forward import candidates
        def only_recorded(s,row,now):return (DAY,)
        with patch('back.daily_forward_service.candidates',side_effect=only_recorded),ForbiddenClient().guard():
            value=run_once(self.path,ledger_path=self.ledger,service_factory=self.factory(now=lambda:moment('2026-10-23T17:00:00')))
        self.assertEqual((value['new_predictions'],value['scored_outcomes'],value['jev_http_calls'],value['ledger_used']),(0,3,0,1))
    def test_failure_safe_one_line_and_lock_released(self):
        def fail(_):raise RuntimeError('raw upstream '+KEY)
        out=io.StringIO();rc=main(['--db',str(self.path)],stdout=out,runner=lambda p:run_once(p,ledger_path=self.ledger,service_factory=self.factory(sync=fail)))
        self.assertEqual(rc,1);self.assertEqual(len(out.getvalue().splitlines()),1)
        self.assertNotIn(KEY,out.getvalue());self.assertNotIn('raw upstream',out.getvalue())
        self.assertEqual(json.loads(out.getvalue())['status'],'error')
        with forward_lock(self.path):pass
    def test_missing_db_not_created_and_oneshot_no_background_thread(self):
        missing=self.path.parent/'missing.sqlite3'
        value=run_once(missing,ledger_path=self.ledger);self.assertEqual(value['error'],'database_missing');self.assertFalse(missing.exists())
        writer=DBWriter(self.path);writer.ready.result(5)
        try:
            with patch('back.daily_forward_service.threading.Thread',side_effect=AssertionError('one shot')):
                service=DailyForwardService(writer,ActivityGate(),autostart=False)
                self.assertIsNone(service.worker);service.close()
        finally:writer.close();writer.thread.join(5)


class LaunchdTemplateTests(unittest.TestCase):
    def test_exact_schedule_direct_python_no_key_no_auto_install(self):
        path=ROOT/'docs/launchd/com.gatewen.trendcast.daily.plist.example'
        data=plistlib.loads(path.read_bytes());times=data['StartCalendarInterval']
        self.assertEqual({(r['Weekday'],r['Hour'],r['Minute']) for r in times},{(d,h,m) for d in range(1,6) for h,m in ((16,45),(17,30),(20,30))})
        self.assertEqual(len(times),15);self.assertFalse(data.get('KeepAlive'));self.assertFalse(data.get('RunAtLoad'))
        self.assertEqual(data['ProgramArguments'][0],'/usr/local/bin/python3')
        self.assertTrue(data['ProgramArguments'][1].endswith('/scripts/daily_forward.py'))
        self.assertNotIn('FUGLE_API_KEY',data['EnvironmentVariables']);self.assertNotIn('TYPESAFE_API_KEY',data['EnvironmentVariables'])
        self.assertNotIn('.zshrc',path.read_text())
