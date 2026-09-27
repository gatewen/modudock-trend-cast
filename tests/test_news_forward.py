import copy
from datetime import date,timedelta
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from back import news_digest as news,news_forward as p5
from back.daily_forward import save_baselines,prepare_day,settle,load_model
from back.daily_forward_client import ForwardBudget,ForwardClient,ledger_usage
from back.daily_forward_service import DailyForwardService
from back.daily_forward_view import forward_view,news_comparisons
from back.daily_store import DailyStore
from back.db_writer import DBWriter
from back.experiment import ActivityGate,canonical
from back.evolution_run import ForbiddenClient
from back.http_client import ClientError
from back.data import DataError
from back.jevcast import _AUTH_DISABLED
from back.score import ScoredPoint
from tests.test_daily_forward import ForwardFixture,moment,DAY
from tests.test_news_digest import payload
from tests.test_daily_jev import response
from back.daily_jev_client import validate_daily_response
from tests.helpers import FakeServer,KEY


class NewsForwardTests(ForwardFixture):
    def ingest(self,clock='13:20:00',received=None,body=None):
        value=news.validate(body or payload(DAY+'T'+clock+'+08:00'),moment(DAY+'T'+(received or clock)))
        self.assertIsNotNone(value);news.receive(self.s,value)
    def record(self):
        self.save();p5.record_input(self.s,DAY,self.now)
        return self.s.db.execute('SELECT * FROM news_forward_requests WHERE day=?',(DAY,)).fetchone()
    def test_complete_p6_projection_same_day_only_and_no_identifiers(self):
        self.ingest();base=self.prepared();before=json.loads(base['body_json'])
        expected=news.snapshot(self.s,DAY)
        self.ingest('16:00:00',body=dict(payload(DAY+'T16:00:00+08:00'),source_count=999))
        with ForbiddenClient().guard():row=self.record();body=p5.claim_request(self.s,DAY)
        out=json.loads(body)
        self.assertTrue(news.JEV_NEWS_ENABLED)
        self.assertEqual({k:v for k,v in out['state'].items() if k!='news'},before['state'])
        self.assertEqual(out['state']['news'],{k:expected[k] for k in p5.NEWS_FIELDS})
        self.assertEqual(set(out['state']['news']),{'signal_counts','top_themes','window_hours'})
        for key,q in out['questions'].items():
            self.assertTrue(q['instructions'].startswith(before['questions'][key]['instructions']))
            self.assertIn('不是這檔股票專屬新聞',q['instructions']);self.assertIn('不可信',q['instructions'])
            self.assertEqual(q['criteria'],before['questions'][key]['criteria'])
        for value in ('2330',DAY,'2026-','source_count','received_at','"schema"'):
            self.assertNotIn(value,body.decode())
        self.assertEqual(self.s.db.execute('SELECT body_json FROM d_forward_days').fetchone()[0],base['body_json'])
        self.assertEqual(canonical(load_model(self.s)[1]),canonical(self.bundle))
        self.assertIsNone(p5.claim_request(self.s,DAY))
    def test_free_text_numeric_identifiers_dates_prices_redacted(self):
        body=payload();body['top_themes'][0]['name']='2330 2026-09-29 股價999999.50元 半導體'
        self.ingest(body=body);self.record();sent=json.loads(p5.claim_request(self.s,DAY))
        name=sent['state']['news']['top_themes'][0]['name']
        self.assertEqual(name,'〔數字略〕 〔數字略〕 股價〔數字略〕元 半導體')
        self.assertEqual(sent['state']['news']['signal_counts'],body['signal_counts'])
        self.assertEqual(news.snapshot(self.s,DAY)['top_themes'][0]['name'],body['top_themes'][0]['name'])
    def test_no_snapshot_or_late_receipt_no_claim_and_off_switch(self):
        self.ingest('13:00:00','13:30:00.000001')
        self.assertIsNone(news.snapshot(self.s,DAY))
        row=self.record();self.assertEqual(row['jev_state'],'no_news');self.assertIsNone(row['body_json'])
        with ForbiddenClient().guard():self.assertIsNone(p5.claim_request(self.s,DAY))
        # A different same-day fixture tests the explicit switch, no production mutation.
        with self.s.transaction():self.s.db.execute('DELETE FROM news_forward_requests')
        self.ingest('17:00:00');self.assertIsNone(news.snapshot(self.s,DAY))
        with patch('back.news_digest.JEV_NEWS_ENABLED',False):self.assertIsNone(p5.claim_request(self.s,DAY))
    def test_forward_only_frozen_policy_and_input_reject_tampering(self):
        self.ingest();self.record()
        with self.assertRaisesRegex(DataError,'forward_only'):p5.record_input(self.s,'2021-12-31',self.now)
        with patch.object(p5,'INSTRUCTIONS','changed'):
            with self.assertRaisesRegex(DataError,'policy_changed'):p5.claim_request(self.s,DAY)
        with self.assertRaises(sqlite3.IntegrityError):self.s.db.execute("UPDATE news_forward_requests SET body_json='{}'")
        self.s.db.rollback();self.s.db.execute('DROP TRIGGER news_forward_input_immutable')
        self.s.db.execute("UPDATE news_forward_requests SET body_json='{}'");self.s.db.commit()
        with self.assertRaisesRegex(DataError,'input_changed'):p5.claim_request(self.s,DAY)
    def test_snapshot_source_change_rejected(self):
        self.ingest();self.record()
        # Change timing metadata only: p5 payload stays identical but provenance changes.
        self.s.db.execute('UPDATE news_snapshots SET payload_json=?',(canonical(dict(payload(),source_count=99)),));self.s.db.commit()
        with self.assertRaisesRegex(DataError,'input_changed'):p5.claim_request(self.s,DAY)
    def test_atomic_three_answers_reserved_before_commit_and_maturity(self):
        self.ingest();self.record();answer=validate_daily_response(response())
        with self.assertRaisesRegex(DataError,'not_reserved'):p5.save_jev(self.s,DAY,answer,self.now)
        p5.claim_request(self.s,DAY)
        self.s.db.execute("CREATE TRIGGER reject_news BEFORE INSERT ON d_forward_predictions WHEN NEW.method='jev_news' AND NEW.H=7 BEGIN SELECT RAISE(ABORT,'fixture'); END;")
        with self.assertRaises(sqlite3.IntegrityError):p5.save_jev(self.s,DAY,answer,self.now)
        self.assertEqual(self.s.db.execute("SELECT count(*) FROM d_forward_predictions WHERE method='jev_news'").fetchone()[0],0)
        self.assertEqual(self.s.db.execute('SELECT jev_state FROM news_forward_requests').fetchone()[0],'reserved')
        self.s.db.execute('DROP TRIGGER reject_news');p5.save_jev(self.s,DAY,answer,self.now)
        self.assertIsNone(p5.claim_request(self.s,DAY))
        self.assertEqual(self.s.db.execute("SELECT count(*) FROM d_forward_predictions WHERE method='jev_news'").fetchone()[0],3)
        settle(self.s,moment('2026-10-23T18:00:00'))
        view=forward_view(self.s,dict(op='daily_forward',H=3))
        self.assertEqual(view['latest']['news_state'],'done')
        score=next(r for r in view['cohorts'][0]['methods'] if r['method']=='jev_news')
        self.assertEqual(score['n'],1)
        self.assertEqual(self.s.db.execute('SELECT count(*) FROM d_outcomes').fetchone()[0],0)
    def make_service(self,*,snapshot=True,server=None,budget=None):
        if snapshot:self.ingest()
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        path=Path(temp.name)/'db';ledger=Path(temp.name)/'ledger'
        with DailyStore(path) as s:self.s.db.backup(s.db)
        writer=DBWriter(path);writer.ready.result(5)
        class Market:
            calls=0
            def predict(s,*args,**kw):s.calls+=1;return validate_daily_response(response())
        market=Market()
        budget=budget or ForwardBudget(path=ledger,method='jev_news',now=lambda:self.now,deadline=lambda _:moment('2026-09-30T09:00:00'))
        client=ForwardClient(budget,opener=server,sleep=lambda _:None) if server else None
        service=DailyForwardService(writer,ActivityGate(),now=lambda:self.now,autostart=False,client=market,
            ledger_path=budget.path,news_client=client,news_budget=budget)
        def close():service.close();writer.close();writer.thread.join(5)
        self.addCleanup(close)
        return service,path,market,budget
    def test_actual_http_payload_one_day_once_and_restart(self):
        server=FakeServer();self.addCleanup(server.close);server.queue(response())
        service,path,market,budget=self.make_service(server=server)
        with patch.dict(os.environ,TYPESAFE_API_KEY=KEY):
            result=service.cycle();again=service.cycle()
        self.assertEqual((market.calls,len(server.bodies),result['new_predictions'],result['jev_http_calls']),(1,1,15,1))
        self.assertEqual((again['new_predictions'],again['jev_http_calls']),(0,0))
        body=json.loads(server.bodies[0]);self.assertEqual(set(body['state']),{'daily','indicators','news'})
        for text in (DAY,'2330','source_count','received_at'):self.assertNotIn(text,server.bodies[0].decode())
        with DailyStore(path,readonly=True) as s:
            self.assertEqual(s.db.execute('SELECT jev_state FROM news_forward_requests').fetchone()[0],'done')
            self.assertEqual(forward_view(s,dict(op='daily_forward'))['latest']['news_state'],'done')
        resumed=ForwardBudget(path=budget.path,method='jev_news',now=lambda:self.now)
        self.assertEqual(resumed.last(DAY)['status'],200)
        with self.assertRaisesRegex(ClientError,'uncertain'):resumed.reserve(DAY)
    def test_no_news_0_http_even_with_key_and_transport(self):
        server=FakeServer();self.addCleanup(server.close)
        service,path,market,budget=self.make_service(snapshot=False,server=server)
        with patch.dict(os.environ,TYPESAFE_API_KEY=KEY):result=service.cycle();service.cycle()
        self.assertEqual((market.calls,len(server.bodies),budget.calls,result['new_predictions']),(1,0,0,12))
        self.assertEqual(result['status'],'complete')
        with DailyStore(path,readonly=True) as s:
            self.assertEqual(forward_view(s,dict(op='daily_forward'))['latest']['news_state'],'no_news')
    def test_news_retries_separate_from_p6_and_all_charged(self):
        server=FakeServer();self.addCleanup(server.close)
        for status in (429,529):server.queue(status=status)
        server.queue(response())
        service,path,market,budget=self.make_service(server=server)
        p6=ForwardBudget(path=budget.path,now=lambda:self.now);seq=p6.reserve(DAY);p6.finish(seq,status=200)
        with patch.dict(os.environ,TYPESAFE_API_KEY=KEY):result=service.cycle();service.cycle()
        self.assertEqual((result['jev_http_calls'],budget.calls,len(server.bodies)),(3,3,3))
        self.assertEqual(ledger_usage(budget.path),4)
        self.assertEqual(budget.last(DAY)['attempt'],3);self.assertEqual(p6.last(DAY)['attempt'],1)
    def test_news_retry_restart_deadline_and_uncertain_failure(self):
        server=FakeServer();self.addCleanup(server.close)
        service,path,market,budget=self.make_service(server=server)
        service._write(lambda s:save_baselines(s,prepare_day(s,self.row,self.bundle,DAY),self.now))
        service._write(lambda s:p5.record_input(s,DAY,self.now));service._write(lambda s:p5.claim_request(s,DAY))
        seq=budget.reserve(DAY);budget.finish(seq,status=429)
        self.now=moment('2026-09-30T09:00:00')
        with patch.dict(os.environ,TYPESAFE_API_KEY=KEY):service.cycle()
        self.assertEqual(len(server.bodies),0)
        with DailyStore(path,readonly=True) as s:self.assertEqual(s.db.execute('SELECT jev_state FROM news_forward_requests').fetchone()[0],'failed')
    def test_no_news_module_no_new_day_and_disabled_flag_no_request(self):
        server=FakeServer();self.addCleanup(server.close)
        service,path,market,budget=self.make_service(server=server)
        with patch.dict(os.environ,TYPESAFE_API_KEY=KEY),patch.object(news,'JEV_NEWS_ENABLED',False):
            result=service.cycle()
        self.assertEqual((len(server.bodies),budget.calls,result['new_predictions']),(0,0,12))


class NewsComparisonTests(unittest.TestCase):
    def test_both_intersections_20_day_blocks_60_matured_minimum(self):
        days=[(date(2026,9,29)+timedelta(days=i)).isoformat() for i in range(61)]
        def points(n):return {d:ScoredPoint(d,'flat','flat',{'up':0.,'flat':1.,'down':0.}) for d in days[:n]}
        rows={'jev_news':points(61),'jev_ind':points(59),'majority':points(60)}
        result=news_comparisons(rows,days,'ontime')
        self.assertEqual(set(result),{'jev_ind','majority'})
        self.assertEqual(result['jev_ind']['n'],59);self.assertEqual(result['jev_ind']['verdict'],'結果不完整，不下結論')
        self.assertNotEqual(result['majority']['verdict'],'結果不完整，不下結論')
        self.assertEqual((result['majority']['block_sessions'],result['majority']['repetitions'],result['majority']['seed']),(20,2000,20260927))
        result=news_comparisons(rows,days,'unconfirmed')
        self.assertEqual(result['majority']['verdict'],'結果不完整，不下結論')
