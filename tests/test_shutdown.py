from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from back.data import DataError
from back.db_lifecycle import DatabaseGate
from back.db_writer import DBWriter
from back.daily_forward_client import ForwardBudget
from back.daily_forward_service import DailyForwardService
from back.daily_store import DailyStore
from back.experiment import ActivityGate
from back.runtime import Application
from back.store import Store
from tests.test_runtime import Sink
from tests.test_news_digest import payload
from tests.test_daily_forward import moment,DAY


class ShutdownTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/'db.sqlite3'
        with Store(self.path) as s:
            s.db.execute('CREATE TABLE shutdown_probe(n INTEGER)');s.db.commit()
        self.writer=DBWriter(self.path);self.writer.ready.result(3)
        self.app=Application(self.writer,Sink(),1)
        self.addCleanup(self.cleanup)
    def cleanup(self):
        self.app.close();self.assertTrue(self.app.wait_closed(3))
    def count(self):
        with Store(self.path,readonly=True) as s:return s.db.execute('SELECT count(*) FROM shutdown_probe').fetchone()[0]

    def test_close_aborts_queued_news_and_running_job_cannot_write_after_release(self):
        entered,release=threading.Event(),threading.Event()
        def running(s):
            entered.set();release.wait(3)
            s.db.execute('INSERT INTO shutdown_probe VALUES (1)');s.db.commit()
        work=self.writer.submit(running);self.assertTrue(entered.wait(2))
        queued=self.writer.submit(lambda s:s.db.execute('INSERT INTO shutdown_probe VALUES (2)'))
        with patch('back.news_digest.now',return_value=moment(DAY+'T13:20:00')):
            self.app.event('news.market_digest',payload())
        try:
            started=time.monotonic();self.app.close();self.assertLess(time.monotonic()-started,.1)
            self.assertTrue(queued.done());self.assertIsNotNone(queued.exception())
        finally:release.set()
        self.assertTrue(self.app.wait_closed(3));self.assertIsNotNone(work.exception())
        self.assertEqual(self.count(),0)
        with Store(self.path,readonly=True) as s:
            self.assertIsNone(s.db.execute("SELECT 1 FROM sqlite_master WHERE name='news_digests'").fetchone())
        self.assertIsNotNone(self.writer.submit(lambda s:None).exception())

    def test_inflight_uncommitted_transaction_is_rolled_back_on_close(self):
        entered,release=threading.Event(),threading.Event()
        def write(s):
            with s.transaction():
                s.db.execute('INSERT INTO shutdown_probe VALUES (1)');entered.set();release.wait(3)
        future=self.writer.submit(write);self.assertTrue(entered.wait(2))
        self.app.close();release.set();self.assertTrue(self.app.wait_closed(3))
        self.assertIsNotNone(future.exception());self.assertEqual(self.count(),0)

    def test_sqlite_external_lock_shutdown_has_no_late_commit(self):
        locker=sqlite3.connect(self.path);self.addCleanup(locker.close);locker.execute('BEGIN IMMEDIATE')
        entered=threading.Event()
        def write(s):
            entered.set()
            with s.transaction():s.db.execute('INSERT INTO shutdown_probe VALUES (1)')
        future=self.writer.submit(write);self.assertTrue(entered.wait(2))
        started=time.monotonic();self.app.close()
        self.assertTrue(self.app.wait_database_closed(.6));self.assertLess(time.monotonic()-started,.7)
        locker.rollback();self.assertTrue(self.app.wait_closed(3));self.assertIsNotNone(future.exception())
        self.assertEqual(self.count(),0)

    def test_cancelled_daily_summary_does_not_reopen_database_and_worker_is_joined(self):
        entered,release=threading.Event(),threading.Event();opens=[]
        original=self.app.daily_forward._cycle
        def gated(*args,**kwargs):entered.set();release.wait(3);return 'no_experiment'
        def opening(*args,**kwargs):opens.append(threading.current_thread().name);return DailyStore(*args,**kwargs)
        with patch.object(self.app.daily_forward,'_cycle',side_effect=gated),patch('back.daily_forward_service.DailyStore',side_effect=opening):
            self.app._on_sync({'generation':10,'status':'complete'});self.assertTrue(entered.wait(2))
            before=len(opens);self.app.close();release.set();self.assertTrue(self.app.wait_closed(3))
            self.assertEqual(len(opens),before)
            self.assertFalse(self.app.daily_forward.worker.is_alive())
        self.temp.cleanup() # Must be safe immediately, not after a retry/sleep/GC.
        self.assertFalse(self.path.parent.exists())

    def test_read_constructor_and_connection_close_are_part_of_shutdown_barrier(self):
        entered,release=threading.Event(),threading.Event()
        class BlockedStore(Store):
            def __init__(self,*a,**kw):entered.set();release.wait(3);super().__init__(*a,**kw)
        with patch('back.runtime.Store',BlockedStore):
            self.app.request({'op':'news_status'});self.assertTrue(entered.wait(2))
            self.app.close();self.assertFalse(self.app.wait_database_closed(.02))
            release.set();self.assertTrue(self.app.wait_closed(3))
        self.assertEqual(self.app.database.active,0)
        with self.assertRaisesRegex(DataError,'cancelled'):
            with self.app.database.store(Store,self.path,readonly=True):pass
        with patch('back.daily_forward_service.DailyStore',side_effect=AssertionError('no new connection')):
            with self.assertRaisesRegex(DataError,'cancelled'):self.app.daily_forward._read(lambda s:None)

    def test_closed_budget_rejects_late_network_receipt_and_preserves_reserved_attempt(self):
        path=Path(self.temp.name)/'budget.sqlite3'
        budget=ForwardBudget(path=path,now=lambda:moment(DAY+'T17:00:00'))
        seq=budget.reserve(DAY);self.app.daily_forward.budget=budget
        self.app.close();self.assertTrue(self.app.wait_closed(3))
        with self.assertRaisesRegex(DataError,'cancelled'):budget.finish(seq,status=200)
        with self.assertRaisesRegex(DataError,'cancelled'):budget.reserve(DAY)
        with sqlite3.connect(path) as db:
            self.assertEqual(db.execute('SELECT used FROM budget').fetchone()[0],1)
            self.assertIsNone(db.execute('SELECT status FROM daily_forward_http').fetchone()[0])
