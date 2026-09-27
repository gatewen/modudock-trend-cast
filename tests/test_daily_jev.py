from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from decimal import Decimal
import hashlib
import io
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch

from back.daily_experiment import create_experiment
from back.daily_indicators import frames
from back.daily_jev import freeze_plan,run_plan,commit_answer,stored_complete,report
from back.daily_jev_client import RoundBudget,DailyJevClient,DailyAnswer,validate_daily_response,CAMPAIGN,SCOPE
from back.daily_prompt import payload_bytes
from back.daily_run import run_horizon
from back.daily_store import DailyStore
from back.data import DataError
from back.experiment import MODEL,canonical
from back.http_client import ClientError
from back.jevcast import _AUTH_DISABLED
from scripts.run_daily_jev import main
from tests.daily_fixture import seed_daily
from tests.helpers import FakeServer,KEY
from tests.test_daily_prompt import config


def response():
    return dict(model=MODEL,answers={f'direction_{h}d':dict(choice='flat',probabilities=dict(up=.2,flat=.6,down=.2)) for h in (3,7,14)})


class DailyClientTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/'ledger.sqlite3';self.budget=RoundBudget('plan',path=self.path)
        self.store=DailyStore(':memory:');self.addCleanup(self.store.close)
        self.days=seed_daily(self.store,130);self.body=payload_bytes(self.store,config(self.days),self.days[100])
        self.server=FakeServer();self.addCleanup(self.server.close)
        _AUTH_DISABLED.clear();self.addCleanup(_AUTH_DISABLED.clear)
        env=patch.dict(os.environ,TYPESAFE_API_KEY=KEY);env.start();self.addCleanup(env.stop)
    def test_actual_http_three_questions_and_no_absolute_chip_units(self):
        self.server.queue(response());client=DailyJevClient(self.budget,opener=self.server)
        answer=client.predict(self.days[100],self.body)
        self.assertEqual(set(answer.answers),{3,7,14});self.assertEqual(self.server.bodies,[self.body])
        body=json.loads(self.server.bodies[0]);self.assertEqual(len(body['questions']),3)
        for name in ('foreign_net','trust_net','margin_chg'):
            self.assertRegex(body['state']['indicators'][name],r'^(?:-?\d+\.\d{2}%|本期無此資料)$')
        self.assertNotIn(KEY.encode(),self.body);self.assertNotIn(self.days[100].encode(),self.body)
        summary=self.budget.summary();self.assertEqual(summary['http_calls_round'],1);self.assertEqual(summary['campaign_used'],1)
    def test_retry_quota_durable_and_no_double_campaign_charge(self):
        self.server.queue(status=429);self.server.queue(status=529);self.server.queue(response())
        sleeps=[];c=DailyJevClient(self.budget,opener=self.server,sleep=sleeps.append)
        c.predict(self.days[100],self.body)
        self.assertEqual(sleeps,[.5,1.]);self.assertEqual(len(self.server.bodies),3)
        self.assertEqual(self.budget.summary()['campaign_used'],3)
        self.assertEqual(self.budget.summary()['retries_round'],2)
        resumed=RoundBudget('plan',path=self.path,max_calls=1)
        self.server.queue(response());DailyJevClient(resumed,opener=self.server).predict(self.days[100],self.body)
        with self.assertRaises(ClientError):DailyJevClient(resumed,opener=self.server).predict(self.days[101],self.body)
        self.assertEqual(resumed.summary()['http_calls_round'],4)
        with self.assertRaises(DataError):RoundBudget('different',path=self.path)
    def test_650_round_and_3000_campaign_limits_are_atomic(self):
        with sqlite3.connect(self.path) as db:
            db.executemany('INSERT INTO p6_attempts(scope,seq,day,retry) VALUES (?,?,?,0)',((SCOPE,i,'fixture') for i in range(1,650)))
            db.execute('UPDATE budget SET used=649')
        def consume(_):
            try:return self.budget.reserve('final')
            except ClientError:return None
        with ThreadPoolExecutor(max_workers=6) as pool:values=list(pool.map(consume,range(6)))
        self.assertEqual(sum(v is not None for v in values),1)
        resumed=RoundBudget('plan',path=self.path)
        with self.assertRaises(ClientError):resumed.reserve('after_restart')
        self.assertEqual(resumed.summary()['http_calls_round'],650)
        other=RoundBudget('other',path=Path(self.temp.name)/'other.sqlite3')
        with sqlite3.connect(other.path) as db:db.execute('UPDATE budget SET used=3000')
        with self.assertRaises(ClientError):other.reserve('limit')
        self.assertEqual(other.summary()['http_calls_round'],0)
        with self.assertRaises(DataError):RoundBudget('plan',path=self.path,max_calls=651)
    def test_auth_closes_client_cancel_zero_http_invalid_answer_not_complete(self):
        for status in (401,403):
            _AUTH_DISABLED.clear();self.server.queue(status=status)
            c=DailyJevClient(self.budget,opener=self.server)
            with self.assertRaises(ClientError):c.predict(self.days[100],self.body)
            count=len(self.server.bodies)
            with self.assertRaises(ClientError):DailyJevClient(self.budget,opener=self.server).predict(self.days[101],self.body)
            self.assertEqual(count,len(self.server.bodies))
        _AUTH_DISABLED.clear();cancel=threading.Event();cancel.set()
        with self.assertRaises(ClientError):c.predict(self.days[100],self.body,cancel=cancel)
        self.assertEqual(len(self.server.bodies),2)
        bad=response();del bad['answers']['direction_7d'];self.server.queue(bad)
        with self.assertRaises(ClientError):c.predict(self.days[100],self.body)
    def test_all_three_validate_model_shape_probabilities_choice_and_sum(self):
        self.assertEqual(validate_daily_response(response()).model_reported,MODEL)
        for h in (3,7,14):
            for change in ('missing','bad_sum','bool','wrong_choice','extra'):
                bad=response();q=bad['answers'][f'direction_{h}d']
                if change=='missing':del bad['answers'][f'direction_{h}d']
                if change=='bad_sum':q['probabilities']['up']=.9
                if change=='bool':q['probabilities']['up']=True
                if change=='wrong_choice':q['choice']='up'
                if change=='extra':q['probabilities']['other']=0
                with self.subTest(h=h,change=change),self.assertRaises(ClientError):validate_daily_response(bad)
        for change in (dict(response(),model='wrong'),dict(response(),answers=dict(response()['answers'],extra={}))):
            with self.assertRaises(ClientError):validate_daily_response(change)


class FakeBudget:
    def exhausted(self):return False
    def summary(self):return {}


class FakeClient:
    def __init__(self):self.calls=0;self.budget=FakeBudget()
    def enabled(self):return True
    def predict(self,day,body,*,cancel=None):self.calls+=1;return validate_daily_response(response())


class DailyRunnerTests(unittest.TestCase):
    def setUp(self):
        self.store=DailyStore(':memory:');self.addCleanup(self.store.close)
        self.days=seed_daily(self.store,3900)
        k={h:dict(k_numerator=1,k_denominator=100) for h in (3,7,14)}
        with patch('back.daily_experiment.development_thresholds',return_value=k):self.row=create_experiment(self.store)
        points=[f for f in frames(self.store,end='2010-09-30') if f.predictable and f.day>='2010-04-01'][::5][:3]
        self.points=points
        with patch('back.daily_run.prepare_development',return_value=points):
            for H in (3,7,14):run_horizon(self.store,H=H)
        self.sample=dict(days=tuple(p.day for p in points),rule='fixture')
        patcher=patch('back.daily_jev.sample_dates',return_value=self.sample);patcher.start();self.addCleanup(patcher.stop)
        self.plan=freeze_plan(self.store)
    def test_atomic_three_answer_commit_cancel_and_rollback(self):
        d=self.points[0].day;answer=validate_daily_response(response())
        self.store.db.execute("CREATE TRIGGER break_h7 BEFORE INSERT ON d_predictions WHEN NEW.method='jev_ind' AND NEW.H=7 BEGIN SELECT RAISE(ABORT,'fixture');END")
        with self.assertRaises(sqlite3.IntegrityError):commit_answer(self.store,d,answer,threading.Event())
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM d_predictions WHERE method='jev_ind'").fetchone()[0],0)
        self.assertFalse(stored_complete(self.store,d))
        self.store.db.execute('DROP TRIGGER break_h7')
        cancel=threading.Event();cancel.set()
        with self.assertRaises(ClientError):commit_answer(self.store,d,answer,cancel)
        commit_answer(self.store,d,answer,threading.Event())
        self.assertTrue(stored_complete(self.store,d))
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM d_predictions WHERE method='jev_ind'").fetchone()[0],3)
    def test_run_resume_zero_http_and_baseline_runner_unchanged(self):
        client=FakeClient();r=run_plan(self.store,client,self.plan)
        self.assertEqual((r['missing'],client.calls),(0,3))
        again=freeze_plan(self.store);r=run_plan(self.store,client,again)
        self.assertEqual((r['new_ok'],r['skipped'],client.calls),(0,3,3))
        with patch('back.daily_run.prepare_development',return_value=self.points):
            for H in (3,7,14):self.assertEqual(run_horizon(self.store,H=H)['new_predictions'],0)
        scored=report(self.store);self.assertTrue(scored['complete'])
        for r in scored['horizons'].values():
            self.assertEqual(r['n'],3);self.assertEqual(r['choices'],dict(up=0,flat=3,down=0))
            self.assertEqual(set(r['comparisons']),{'majority','ind_logit'})
            self.assertEqual(r['comparisons']['majority']['block_sessions'],20)
    def test_plan_body_is_immutable_and_only_dev(self):
        for split in ('holdout','forward','exposed'):
            with self.assertRaises(DataError):freeze_plan(self.store,split=split)
        with self.assertRaises(DataError):freeze_plan(self.store,experiment_id=1)
        with self.assertRaises(sqlite3.IntegrityError):self.store.db.execute("UPDATE d_jev_requests SET body_json='{}'")
        self.store.db.rollback()
        with self.assertRaises(sqlite3.IntegrityError):self.store.db.execute("UPDATE d_jev_plan SET config_json='{}'")
        self.store.db.rollback()
        with patch('back.daily_prompt.COMMON_INSTRUCTIONS','changed'):
            with self.assertRaises(DataError):freeze_plan(self.store)
    def test_missing_answer_stops_first_probe_and_report_is_incomplete(self):
        class Bad(FakeClient):
            def predict(self,*a,**kw):self.calls+=1;raise ClientError('invalid_daily_answers')
        client=Bad();result=run_plan(self.store,client,self.plan)
        self.assertEqual(result['missing'],3);self.assertEqual(client.calls,1)
        score=report(self.store);self.assertFalse(score['complete']);self.assertEqual(score['missing_answers'],9)
    def test_cli_no_execute_never_opens_database_or_network(self):
        with tempfile.TemporaryDirectory() as d,patch('sys.stdout',new=io.StringIO()),patch('scripts.run_daily_jev.DailyJevClient',side_effect=AssertionError('network')):
            path=Path(d)/'missing.sqlite3';self.assertEqual(main(['--db',str(path)]),0);self.assertFalse(path.exists())
            with self.assertRaises(DataError):main(['--max-calls','651'])

if __name__=='__main__':unittest.main()
