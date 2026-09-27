from datetime import date,datetime,timedelta
from pathlib import Path
from dataclasses import replace
import json
import os
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch

from back.daily_store import DailyStore
from back.daily_experiment import create_experiment,load_experiment
from back.daily_forward import *
from back.daily_forward_client import ForwardBudget,DailyJevClient
from back.daily_forward_service import DailyForwardService
from back.daily_forward_view import forward_view
from back.daily_incremental import append_batch,windows
from back.daily_jev import SCHEMA as JEV_SCHEMA
from back.daily_jev_client import validate_daily_response,CAMPAIGN
from back.daily_sources import parse_finmind,CALENDAR,INSTITUTIONAL,MARGIN
from back.daily_models import Logistic
from back.daily_sync import no_jev
from back.evolution_run import ForbiddenClient
from back.experiment import ActivityGate
from back.db_writer import DBWriter
from back.fugle import CandleBatch
from back.twse import CorpBatch
from back.http_client import ClientError
from back.jevcast import _AUTH_DISABLED
from tests.daily_fixture import seed_daily
from tests.helpers import FakeServer,KEY
from tests.test_daily_jev import response

DAY='2026-09-29'
def moment(s):return parsed(s+'+08:00')


def fixture(store):
    first=seed_daily(store,350)
    # Synthetic closed days provide the declared 60-session April warmup.
    for table in ('d_calendar','d_bars','d_institutional','d_margin'):
        store.db.executemany(f'DELETE FROM {table} WHERE day=?',((d,) for d in first[25:30]))
    store.db.commit()
    seed_daily(store,5,start=date(2024,7,22))
    tail=seed_daily(store,105,start=date(2026,6,1))
    for table in ('d_calendar','d_bars','d_institutional','d_margin'):
        store.db.executemany(f'DELETE FROM {table} WHERE day=?',(('2026-09-25',),('2026-09-28',)))
    store._log('calendar','2330',first[0],tail[-1],len(first)+len(tail),'fixture');store.db.commit()
    with patch('back.daily_experiment.now_string',return_value='2026-09-27T13:25:45+08:00'),patch('back.daily_experiment.development_thresholds',return_value={h:dict(k_numerator=1,k_denominator=100) for h in HORIZONS}):
        row=create_experiment(store)
    store.db.executescript(JEV_SCHEMA)
    plan=dict(model=MODEL,questions=questions(row['config']),experiment_data_digest=row['data_digest'],chip_normalization=NORMALIZATION)
    store.db.execute('INSERT INTO d_jev_plan VALUES (4,?,?)',(sha(plan),canonical(plan)));store.db.commit()
    ensure_schema(store);bundle=build_model(store);install_model(store,bundle)
    return row,bundle


class ForwardFixture(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.template=DailyStore(':memory:');cls.row,cls.bundle=fixture(cls.template)
    @classmethod
    def tearDownClass(cls):cls.template.close()
    def setUp(self):
        self.s=DailyStore(':memory:');self.template.db.backup(self.s.db);self.addCleanup(self.s.close)
        self.now=moment(DAY+'T17:00:00')
    def prepared(self):return prepare_day(self.s,self.row,self.bundle,DAY)
    def save(self):save_baselines(self.s,self.prepared(),self.now)


class ForwardCoreTests(ForwardFixture):
    def test_future_and_same_day_chips_cannot_change_input_answers_or_predictability(self):
        before=self.prepared()
        self.s.db.execute("UPDATE d_bars SET close='999999',open='999999',high='999999',low='999999' WHERE day>?",(DAY,))
        self.s.db.execute('UPDATE d_institutional SET buy=987654321 WHERE day>=?',(DAY,))
        self.s.db.execute('UPDATE d_margin SET margin_balance=999999 WHERE day>=?',(DAY,))
        self.s.db.execute('INSERT INTO d_corp_events VALUES (?,?,?,?,?)',('2330','2026-09-30','100','50','fixture'))
        self.s.db.commit();self.assertEqual(self.prepared(),before)
        self.s.db.execute("UPDATE d_bars SET volume='999999' WHERE day=?",(DAY,));self.s.db.commit()
        self.assertNotEqual(self.prepared()['input_hash'],before['input_hash'])
    def test_frozen_model_uses_all_dev_and_only_dev(self):
        for H,m in self.bundle['model']['horizons'].items():
            self.assertGreater(m['train_n'],250)
            self.assertLessEqual(m['logit']['train_last_end'],DEV_END)
            self.assertEqual(m['logit']['train_n'],m['train_n'])
        with patch('back.daily_forward.prepare_development',side_effect=AssertionError('no retraining')):
            self.prepared()
        self.s.db.execute("UPDATE d_bars SET volume='456' WHERE day>'2024-07-25'");self.s.db.commit()
        self.assertEqual(build_model(self.s),self.bundle)
    def test_changed_methods_prompt_and_model_rejected(self):
        with patch.dict(PROTOCOL,unexpected=True):
            with self.assertRaises(DataError):load_model(self.s)
        self.s.db.execute('DROP TRIGGER d_forward_model_no_delete')
        self.s.db.execute('DELETE FROM d_forward_model');self.s.db.commit()
        with patch('back.daily_forward.questions',return_value={}):
            with self.assertRaises(DataError):load_model(self.s)
        install_model(self.s,self.bundle)
        self.s.db.execute('DROP TRIGGER d_forward_model_immutable')
        self.s.db.execute("UPDATE d_forward_model SET model_json='{}'");self.s.db.commit()
        with self.assertRaisesRegex(DataError,'frozen_settings'):load_model(self.s)
    def test_candidates_freeze_holiday_and_1630_boundary(self):
        self.assertEqual(candidates(self.s,self.row,moment('2026-09-27T18:00:00')),())
        self.assertEqual(candidates(self.s,self.row,moment('2026-09-28T18:00:00')),())
        self.assertEqual(candidates(self.s,self.row,moment(DAY+'T16:29:59')),())
        self.assertEqual(candidates(self.s,self.row,moment(DAY+'T16:30:00')),(DAY,))
        with self.assertRaisesRegex(DataError,'before_freeze'):prepare_day(self.s,self.row,self.bundle,'2026-09-24')
    def test_original_recorded_at_next_session_strict_boundary_and_unknown(self):
        for clock,expected in [('08:59:59.999999','ontime'),('09:00:00','backfill'),('09:00:00.000001','backfill')]:
            self.assertEqual(timing(self.s,DAY,'2026-09-30T'+clock+'+08:00'),expected)
        self.s.db.execute('DELETE FROM d_calendar WHERE day>?',(DAY,));self.s.db.commit()
        self.save();self.assertEqual(self.s.db.execute('SELECT DISTINCT timing FROM d_forward_predictions').fetchone()[0],'unconfirmed')
        original=self.s.db.execute('SELECT recorded_at FROM d_forward_predictions').fetchone()[0]
        self.s.db.execute('INSERT INTO d_calendar VALUES (?,?)',('2026-09-30','fixture'));self.s.db.commit()
        settle(self.s,moment('2026-10-01T18:00:00'))
        self.assertEqual(self.s.db.execute('SELECT DISTINCT timing FROM d_forward_predictions').fetchone()[0],'ontime')
        self.assertEqual(self.s.db.execute('SELECT recorded_at FROM d_forward_predictions').fetchone()[0],original)
    def test_record_timestamp_taken_after_validation_not_before_writer_wait(self):
        self.save();claim_request(self.s,DAY)
        self.now=moment('2026-09-30T08:59:59')
        real=load_model
        def slow(store):
            value=real(store);self.now=moment('2026-09-30T09:00:00');return value
        with patch('back.daily_forward.load_model',side_effect=slow):
            save_jev(self.s,DAY,validate_daily_response(response()),lambda:self.now)
        values=self.s.db.execute("SELECT DISTINCT timing,recorded_at FROM d_forward_predictions WHERE method='jev_ind'").fetchall()
        self.assertEqual(len(values),1);self.assertEqual(values[0]['timing'],'backfill')
        self.assertEqual(values[0]['recorded_at'],stamp(self.now))
        view=forward_view(self.s,dict(op='daily_forward'))
        self.assertEqual(view['cohorts'][0]['methods'][-1]['coverage'],0)
        self.assertEqual(next(m for m in view['cohorts'][1]['methods'] if m['method']=='jev_ind')['recorded'],1)

    def test_reservation_atomic_three_answers_and_commit_rollback(self):
        self.save();self.assertEqual(self.s.db.execute('SELECT count(*) FROM d_forward_predictions').fetchone()[0],9)
        with self.assertRaisesRegex(DataError,'not_reserved'):save_jev(self.s,DAY,validate_daily_response(response()),self.now)
        raw=claim_request(self.s,DAY);self.assertEqual(raw,self.prepared()['body_json'].encode())
        self.assertIsNone(claim_request(self.s,DAY))
        self.s.db.execute("CREATE TRIGGER fail_jev BEFORE INSERT ON d_forward_predictions WHEN NEW.method='jev_ind' AND NEW.H=7 BEGIN SELECT RAISE(ABORT,'fixture_abort'); END;")
        with self.assertRaises(sqlite3.IntegrityError):save_jev(self.s,DAY,validate_daily_response(response()),self.now)
        self.assertEqual(self.s.db.execute("SELECT count(*) FROM d_forward_predictions WHERE method='jev_ind'").fetchone()[0],0)
        self.assertEqual(self.s.db.execute('SELECT jev_state FROM d_forward_days').fetchone()[0],'reserved')
        self.s.db.execute('DROP TRIGGER fail_jev');save_jev(self.s,DAY,validate_daily_response(response()),self.now)
        self.assertEqual(self.s.db.execute('SELECT count(*) FROM d_forward_predictions').fetchone()[0],12)
        self.assertIsNone(claim_request(self.s,DAY))
    def test_exact_maturity_and_score_only_post_freeze(self):
        self.save();claim_request(self.s,DAY);save_jev(self.s,DAY,validate_daily_response(response()),self.now)
        end=self.s.db.execute('SELECT day FROM d_calendar WHERE day>? ORDER BY day LIMIT 1 OFFSET 2',(DAY,)).fetchone()[0]
        self.assertEqual(settle(self.s,moment(end+'T16:29:59')),0)
        self.assertEqual(settle(self.s,moment(end+'T16:30:00')),1)
        outcome=self.s.db.execute('SELECT * FROM d_forward_outcomes').fetchone()
        result=DailyReplay(self.s).outcome(DAY,3,end_limit=end)
        self.assertEqual(outcome['label'],label(result.adjusted_return,Fraction(1,100)))
        self.assertEqual(Fraction(int(outcome['return_numerator']),int(outcome['return_denominator'])),result.adjusted_return)
        self.assertEqual(settle(self.s,moment('2026-10-23T17:00:00')),2)
        self.assertEqual(settle(self.s,moment('2026-10-23T18:00:00')),0)
        view=forward_view(self.s,dict(op='daily_forward',H=3))
        self.assertEqual([m['n'] for m in view['cohorts'][0]['methods'] if m['method'] in METHODS],[1]*4)
        comp=view['cohorts'][0]['comparison'];self.assertEqual(comp['verdict'],'結果不完整，不下結論');self.assertTrue(comp['small_sample'])
        p=next(m for m in view['cohorts'][0]['methods'] if m['method']=='jev_ind');self.assertAlmostEqual(p['brier'],sum((v-int(k==outcome['label']))**2 for k,v in dict(up=.2,flat=.6,down=.2).items()))
        self.assertEqual(self.s.db.execute('SELECT count(*) FROM d_outcomes').fetchone()[0],0)
    def test_view_empty_forbidden_inputs_and_missing_coverage(self):
        result=forward_view(self.s,dict(op='daily_forward'))
        self.assertEqual(result['message'],EMPTY);self.assertIsNone(result['latest'])
        for params in (dict(experiment_id=1),dict(split='holdout'),dict(date='2022-01-03'),dict(H=30)):
            with self.assertRaises(DataError):forward_view(self.s,dict(op='daily_forward',**params))
        self.save();result=forward_view(self.s,dict(op='daily_forward'))
        self.assertEqual(result['pending_total'],3);self.assertEqual(result['missing_total'],1)
        jev=result['cohorts'][0]['methods'][-1];self.assertEqual(jev['missing'],1);self.assertEqual(jev['coverage'],0)
        self.assertEqual(result['disclaimer'],DISCLAIMER)
    def test_incremental_append_keeps_frozen_digest_and_rejects_revision(self):
        floor='2024-07-26';before=load_experiment(self.s)['dev_digest']
        jobs=windows(self.s,self.row['config'],'2026-11-03');self.assertTrue(all(a>=floor for _,a,b in jobs))
        raw=parse_finmind(dict(status=200,msg='success',data=[dict(date='2026-11-03')]),CALENDAR,'2330','2026-11-03','2026-11-03')
        self.assertEqual(append_batch(self.s,'calendar',raw,floor),1);self.assertEqual(append_batch(self.s,'calendar',raw,floor),0)
        self.assertEqual(load_experiment(self.s)['dev_digest'],before)
        with self.assertRaisesRegex(DataError,'frozen_range'):append_batch(self.s,'calendar',replace(raw,start='2024-07-25'),floor)
        rows=tuple(dict(r) for r in self.s.db.execute('SELECT * FROM d_bars WHERE day=?',(DAY,)))
        batch=CandleBatch('2330',DAY,DAY,'D',200,rows)
        self.assertEqual(append_batch(self.s,'bars',batch,floor),0)
        changed=replace(batch,rows=[dict(rows[0],close='999')])
        with self.assertRaises(DataError):append_batch(self.s,'bars',changed,floor)
        with self.assertRaisesRegex(DataError,'revision'):append_batch(self.s,'corp',CorpBatch('2330',DAY,DAY,[dict(symbol='2330',day=DAY,prev_close='100',ref_price='90',source='TWSE:TWT49U')]),floor)


class ForwardRetryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.path=Path(self.temp.name)/'ledger.sqlite3'
        self.now=moment(DAY+'T18:00:00');self.deadline=moment('2026-09-30T09:00:00')
        self.budget=ForwardBudget(path=self.path,now=lambda:self.now,deadline=lambda _:self.deadline)
        _AUTH_DISABLED.clear();self.addCleanup(_AUTH_DISABLED.clear)
    def used(self):
        with sqlite3.connect(self.path) as db:return db.execute('SELECT used FROM budget').fetchone()[0]
    def test_only_429_529_three_across_restart_and_all_charged(self):
        for status in (429,529,429):
            budget=ForwardBudget(path=self.path,now=lambda:self.now,deadline=lambda _:self.deadline)
            seq=budget.reserve(DAY);budget.finish(seq,status=status)
        with self.assertRaisesRegex(ClientError,'exhausted'):budget.reserve(DAY)
        self.assertEqual(self.used(),3)
        seq=budget.reserve('2026-10-01');budget.finish(seq,status=500)
        with self.assertRaisesRegex(ClientError,'uncertain'):budget.reserve('2026-10-01')
        seq=budget.reserve('2026-10-02')
        with self.assertRaisesRegex(ClientError,'uncertain'):budget.reserve('2026-10-02')
        self.assertEqual(self.used(),5)
    def test_deadline_strict_and_unknown_does_not_guess_holiday(self):
        seq=self.budget.reserve(DAY);self.budget.finish(seq,status=429)
        self.now=self.deadline-timedelta(microseconds=1);self.assertEqual(self.budget.retry_status(DAY),'retry')
        self.now=self.deadline
        with self.assertRaisesRegex(ClientError,'expired'):self.budget.reserve(DAY)
        self.assertEqual(self.used(),1)
        self.deadline=None;self.assertEqual(self.budget.retry_status(DAY),'wait_calendar')
        self.deadline=moment('2026-10-01T09:00:00');self.assertEqual(self.budget.retry_status(DAY),'retry')
    def test_transport_rechecks_deadline_after_sleep_and_payload_private(self):
        server=FakeServer();self.addCleanup(server.close)
        with patch.dict(os.environ,TYPESAFE_API_KEY=KEY):
            server.queue(status=429)
            def sleep(_):self.now=self.deadline
            client=DailyJevClient(self.budget,opener=server,sleep=sleep)
            body=canonical(dict(model=MODEL,questions={f'direction_{h}d':{} for h in HORIZONS},state={})).encode()
            with self.assertRaisesRegex(ClientError,'expired'):client.predict(DAY,body)
            self.assertEqual(len(server.bodies),1);self.assertEqual(self.used(),1)
    def test_restart_transport_backoff_then_success_all_attempts_charged(self):
        from back.daily_forward_client import ForwardClient
        server=FakeServer();self.addCleanup(server.close)
        seq=self.budget.reserve(DAY);self.budget.finish(seq,status=429)
        sleeps=[]
        def sleep(n):sleeps.append(n);self.now+=timedelta(seconds=n)
        resumed=ForwardBudget(path=self.path,now=lambda:self.now,deadline=lambda _:self.deadline)
        client=ForwardClient(resumed,opener=server,sleep=sleep)
        body=canonical(dict(model=MODEL,questions={f'direction_{h}d':{} for h in HORIZONS},state={})).encode()
        server.queue(status=529);server.queue(response())
        with patch.dict(os.environ,TYPESAFE_API_KEY=KEY):client.predict(DAY,body)
        self.assertEqual(sleeps,[.5,.5]);self.assertEqual(self.used(),3)
        with self.assertRaises(ClientError):resumed.reserve(DAY)

    def test_campaign_cap_and_no_reimbursement(self):
        with sqlite3.connect(self.path) as db:db.execute('UPDATE budget SET used=3000')
        with self.assertRaisesRegex(ClientError,'budget_exhausted'):self.budget.reserve(DAY)
        self.assertEqual(self.used(),3000)


class ForwardServiceTests(ForwardFixture):
    def setup_service(self,client=None,budget=None):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);path=Path(temp.name)/'test.sqlite3'
        with DailyStore(path) as target:self.s.db.backup(target.db)
        writer=DBWriter(path);writer.ready.result(3)
        service=DailyForwardService(writer,ActivityGate(),now=lambda:self.now,client=client,budget=budget,poll_seconds=3600)
        def close():service.close();service.worker.join(3);writer.close();writer.thread.join(3)
        self.addCleanup(close);self.path=path;return service
    def test_service_full_fixture_flow_restart_zero_duplicate_and_no_real_http(self):
        class Fake:
            calls=0
            def predict(s,day,body,**kw):
                s.calls+=1
                self.assertNotIn(day,body.decode());self.assertNotIn('2330',body.decode())
                self.assertNotIn('2026-',body.decode());self.assertEqual(set(json.loads(body)['questions']),{'direction_3d','direction_7d','direction_14d'})
                with DailyStore(self.path,readonly=True) as db:self.assertEqual(db.db.execute('SELECT jev_state FROM d_forward_days WHERE day=?',(day,)).fetchone()[0],'reserved')
                return validate_daily_response(response())
        client=Fake();service=self.setup_service(client)
        with ForbiddenClient().guard():
            self.now=moment('2026-09-28T18:00:00');service.cycle();self.assertEqual(client.calls,0)
            self.now=moment(DAY+'T18:00:00');service.cycle();service.cycle();self.assertEqual(client.calls,1)
            # Advance maturity directly; no need to predict other origins to settle.
            service._write(lambda s:settle(s,moment('2026-10-23T18:00:00')))
            with DailyStore(self.path,readonly=True) as db:
                self.assertEqual(db.db.execute('SELECT count(*) FROM d_forward_predictions').fetchone()[0],12)
                self.assertEqual(db.db.execute('SELECT count(*) FROM d_forward_outcomes').fetchone()[0],3)
                self.assertEqual(db.db.execute('SELECT count(*) FROM d_outcomes').fetchone()[0],0)
    def test_reserved_429_recovers_one_http_with_actual_private_payload(self):
        self.save();claim_request(self.s,DAY)
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        budget=ForwardBudget(path=Path(temp.name)/'ledger.sqlite3',now=lambda:self.now,deadline=lambda _:moment('2026-09-30T09:00:00'))
        seq=budget.reserve(DAY);budget.finish(seq,status=429)
        server=FakeServer();self.addCleanup(server.close);server.queue(response())
        client=DailyJevClient(budget,opener=server,sleep=lambda _:None)
        service=self.setup_service(client,budget)
        with patch.dict(os.environ,TYPESAFE_API_KEY=KEY):service.cycle();service.cycle()
        self.assertEqual(len(server.bodies),1);self.assertEqual(budget.last(DAY)['attempt'],2)
        payload=server.bodies[0].decode();self.assertEqual(payload,self.prepared()['body_json'])
        self.assertNotIn(DAY,payload);self.assertNotIn('2330',payload)
        chips=json.loads(payload)['state']['indicators']
        for name in ('foreign_net','trust_net','margin_chg'):self.assertRegex(chips[name],r'^(?:-?\d+\.\d{2}%|本期無此資料)$')

    def test_incremental_sync_then_auto_generate_with_fake_sources(self):
        source=DailyStore(':memory:');self.s.db.backup(source.db);self.addCleanup(source.close)
        for table in ('d_calendar','d_bars','d_institutional','d_margin'):
            self.s.db.execute(f"DELETE FROM {table} WHERE day>'2026-09-24'")
        self.s.db.execute("UPDATE d_corp_coverage SET end_day='2026-09-24' WHERE start_day>'2024-07-25'")
        self.s.db.commit();calls=[]
        class Sources:
            def candles(_,symbol,start,end,timeframe):
                self.assertEqual(timeframe,'D');calls.append('bars')
                return CandleBatch(symbol,start,end,'D',200,[dict(r) for r in source.db.execute('SELECT * FROM d_bars WHERE day BETWEEN ? AND ?',(start,end))])
            def events(_,symbol,start,end):calls.append('corp');return CorpBatch(symbol,start,end,[])
            def fetch(_,dataset,start,end,symbol):
                calls.append(dataset);table={CALENDAR:'d_calendar',INSTITUTIONAL:'d_institutional',MARGIN:'d_margin'}[dataset];raw=[]
                for r in source.db.execute(f'SELECT * FROM {table} WHERE day BETWEEN ? AND ?',(start,end)):
                    v=dict(date=r['day'])
                    if dataset!=CALENDAR:v['stock_id']=symbol
                    if dataset==INSTITUTIONAL:v.update(name=r['name'],buy=r['buy'],sell=r['sell'])
                    if dataset==MARGIN:v.update(MarginPurchaseTodayBalance=r['margin_balance'],ShortSaleTodayBalance=r['short_balance'])
                    raw.append(v)
                return parse_finmind(dict(status=200,msg='success',data=raw),dataset,symbol,start,end)
        class Client:
            calls=0
            def predict(self,*a,**kw):self.calls+=1;return validate_daily_response(response())
        client=Client();service=self.setup_service(client);service.fugle=service.twse=service.finmind=Sources()
        with ForbiddenClient().guard(),patch.dict(os.environ,FUGLE_API_KEY=KEY):service.cycle(sync=True)
        self.assertEqual(set(calls),{'bars','corp',CALENDAR,INSTITUTIONAL,MARGIN});self.assertEqual(client.calls,1)
        with DailyStore(self.path,readonly=True) as db:
            self.assertEqual(db.db.execute('SELECT max(day) FROM d_bars').fetchone()[0],DAY)
            self.assertEqual(db.db.execute('SELECT count(*) FROM d_forward_predictions').fetchone()[0],12)
            self.assertEqual(load_experiment(db)['dev_digest'],self.row['dev_digest'])

    def test_uncertain_restart_does_not_send_and_failure_persists(self):
        self.save();claim_request(self.s,DAY)
        class No:
            def predict(self,*a,**kw):raise AssertionError('must not resend')
        service=self.setup_service(No())
        with ForbiddenClient().guard():service.cycle();service.cycle()
        with DailyStore(self.path,readonly=True) as db:self.assertEqual(db.db.execute('SELECT jev_state FROM d_forward_days').fetchone()[0],'failed')

class ForwardRuntimeTests(unittest.TestCase):
    def test_startup_and_each_terminal_sync_schedule_worker(self):
        from back.runtime import Application
        from tests.test_runtime import Sink
        with tempfile.TemporaryDirectory() as tmp,patch('back.runtime.DailyForwardService') as Worker,patch.dict(os.environ,{},clear=True):
            writer=DBWriter(Path(tmp)/'test.sqlite3');writer.ready.result(3)
            app=Application(writer,Sink(),1)
            try:
                app.start();Worker.return_value.trigger.assert_called_once_with()
                for i,status in enumerate(('complete','partial','failed'),1):
                    app._on_sync(dict(generation=i,status=status))
                self.assertEqual(Worker.return_value.trigger.call_count,4)
                Worker.return_value.trigger.assert_called_with(sync=True)
            finally:app.close();writer.thread.join(3);app.reader.join(3)
            Worker.return_value.close.assert_called_once_with()

