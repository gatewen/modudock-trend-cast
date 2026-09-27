import copy
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from concurrent.futures import Future
from unittest.mock import patch,Mock

from back import news_digest as news
from back.daily_store import DailyStore
from back.store import Store
from back.db_writer import DBWriter
from back.daily_forward_service import DailyForwardService
from back.experiment import ActivityGate
from back.evolution_run import ForbiddenClient
from tests.test_daily_forward import fixture,moment,DAY
from tests.test_protocol import Child


def payload(at=DAY+'T13:00:00+08:00',**kw):
    return dict(schema=1,at=at,window_hours=24,signal_counts=dict(bullish=2,mixed=1,unrelated=0,bearish=1),
                top_themes=[dict(name='半導體需求',events=2,direction='bullish')],source_count=4,**kw)


class NewsValidationTests(unittest.TestCase):
    def test_strict_schema_types_nested_allowlist_and_no_links(self):
        good=payload();clock=moment(DAY+'T13:30:00')
        self.assertIsNotNone(news.validate(good,clock))
        cases=[dict(good,schema=True),dict(good,schema=2),dict(good,extra='x'),dict(good,source_count=-1),dict(good,source_count=True),
            dict(good,window_hours=0),dict(good,window_hours=169),dict(good,window_hours=2.5),dict(good,at=DAY),
            dict(good,at=DAY+'T13:00:00'),dict(good,at=DAY+'T13:30:01+08:00'),dict(good,at='2026-02-30T13:00:00Z'),
            dict(good,signal_counts=dict(bullish=1)),dict(good,signal_counts=dict(good['signal_counts'],bullish=True)),
            dict(good,top_themes=[dict(name='theme'+str(i),events=1,direction='bullish') for i in range(11)]),dict(good,top_themes={}),
            dict(good,source_count=float('nan'))]
        for field,value in [('name','https://example.com'),('name','example.com'),('name','<x>\nsecret'),('name','x'*81),
            ('name',' x'),('events',0),('events',True),('direction','up'),('direction',[])]:
            theme=dict(good['top_themes'][0]);theme[field]=value;cases.append(dict(good,top_themes=[theme]))
        cases.append(dict(good,top_themes=[dict(good['top_themes'][0],url='x')]))
        cases.append(dict(good,top_themes=good['top_themes']*2))
        for value in cases:
            with self.subTest(value=value):self.assertIsNone(news.validate(value,clock))
        self.assertIsNone(news.validate(good,clock.replace(tzinfo=None)))

    def test_wire_utf8_size_includes_whitespace_escapes_and_exact_limit(self):
        packet=dict(t='event',seq=1,topic=news.TOPIC,body=payload())
        for escaped in (False,True):
            raw=json.dumps(packet,ensure_ascii=escaped).replace('"schema": 1','"schema":'+' '*8100+'1').encode()
            parsed,size=news.decode_packet(raw);self.assertGreater(size,8192)
            self.assertIsNone(news.validate(parsed['body'],moment(DAY+'T13:30:00'),wire_size=size))
        self.assertIsNotNone(news.validate(payload(),moment(DAY+'T13:30:00'),wire_size=8192))
        self.assertIsNone(news.validate(payload(),moment(DAY+'T13:30:00'),wire_size=8193))
        small=json.dumps(packet,ensure_ascii=False);parsed,size=news.decode_packet(small)
        self.assertEqual(size,len((' '+json.dumps(packet['body'],ensure_ascii=False)).encode()))
        self.assertEqual(parsed,packet)
        self.assertIsNone(news.validate(dict(payload(),extra='大'*3000),moment(DAY+'T13:30:00')))

    def test_duplicate_keys_and_nonfinite_rejected(self):
        for raw in ('{"t":"event","body":{"schema":1,"schema":2}}','{"t":"event","body":{},"body":{}}','{"body":NaN}','{"body":Infinity}'):
            with self.subTest(raw=raw),self.assertRaises(ValueError):news.decode_packet(raw)


class NewsSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.s=DailyStore(':memory:');self.addCleanup(self.s.close)
        self.s.db.execute('INSERT INTO d_calendar VALUES (?,?)',(DAY,'fixture'));self.s.db.commit()
    def ingest(self,at,received=None,**changes):
        body=payload(DAY+'T'+at+'+08:00');body.update(changes)
        value=news.validate(body,moment(DAY+'T'+(received or at)))
        self.assertIsNotNone(value);return news.receive(self.s,value)
    def record(self,day=DAY):
        self.s.db.execute('CREATE TABLE IF NOT EXISTS d_forward_days(day TEXT PRIMARY KEY,body_json TEXT)')
        self.s.db.execute('INSERT OR IGNORE INTO d_forward_days VALUES (?,?)',(day,json.dumps(dict(state=dict(daily=[{'relative_close':.01}],indicators={'must':'not copy p6'})))))
        self.s.db.commit();news.record_input(self.s,day,moment(day+'T17:00:00'))
        return dict(self.s.db.execute('SELECT * FROM news_forward_inputs WHERE day=?',(day,)).fetchone())
    def test_last_cutoff_snapshot_survives_afternoon_and_restart_latest_singleton(self):
        self.ingest('12:50:00');self.ingest('13:20:00','13:25:00')
        self.ingest('13:30:00','13:30:00',source_count=9)
        saved=dict(self.s.db.execute('SELECT * FROM news_snapshots').fetchone())
        self.ingest('13:30:01');self.ingest('18:00:00')
        self.assertEqual(dict(self.s.db.execute('SELECT * FROM news_snapshots').fetchone()),saved)
        self.assertEqual(news.snapshot(self.s,DAY)['source_count'],9)
        self.assertEqual(self.s.db.execute('SELECT count(*) FROM news_digests').fetchone()[0],1)
        self.assertIn('18:00',news.status_view(self.s)['received_at'])
        with DailyStore(':memory:') as reopened:
            self.s.db.backup(reopened.db);self.assertEqual(news.snapshot(reopened,DAY)['source_count'],9)
    def test_late_receipt_prior_day_and_other_day_are_not_eligible(self):
        self.ingest('13:00:00','13:30:00.000001')
        self.assertIsNone(news.snapshot(self.s,DAY))
        self.assertEqual(self.s.db.execute('SELECT count(*) FROM news_snapshots').fetchone()[0],0)
        prior=payload('2026-09-28T13:00:00+08:00');news.receive(self.s,news.validate(prior,moment(DAY+'T12:00:00')))
        self.assertIsNone(news.snapshot(self.s,DAY))
        self.assertIsNone(news.snapshot(self.s,'2026-09-30'))
        row=self.record();self.assertEqual(row['status'],'no_news');self.assertEqual(row['message'],'無新聞資料');self.assertIsNone(row['state_json'])
    def test_same_day_required_and_utc_cutoff_is_converted_to_taipei(self):
        prior=payload('2026-09-28T13:00:00+08:00')
        news.receive(self.s,news.validate(prior,moment(DAY+'T12:00:00')))
        self.assertIsNone(news.snapshot(self.s,DAY))
        self.assertEqual(self.s.db.execute('SELECT count(*) FROM news_snapshots').fetchone()[0],0)
        utc=payload(DAY+'T05:30:00Z')
        news.receive(self.s,news.validate(utc,moment(DAY+'T13:30:00')))
        self.assertEqual(news.snapshot(self.s,DAY)['at'],utc['at'])
        utc['at']=DAY+'T05:30:00.000001Z'
        news.receive(self.s,news.validate(utc,moment(DAY+'T13:30:00.000001')))
        self.assertEqual(news.snapshot(self.s,DAY)['at'],DAY+'T05:30:00Z')
    def test_replay_duplicate_and_out_of_order_do_not_change_receipt(self):
        self.ingest('13:10:00');before=dict(self.s.db.execute('SELECT * FROM news_digests').fetchone())
        self.assertFalse(self.ingest('13:10:00','13:20:00',source_count=99))
        self.assertFalse(self.ingest('13:09:00','13:21:00'))
        self.assertEqual(dict(self.s.db.execute('SELECT * FROM news_digests').fetchone()),before)
    def test_unknown_calendar_candidate_promoted_only_when_confirmed(self):
        self.s.db.execute('DELETE FROM d_calendar');self.s.db.commit()
        self.ingest('13:00:00');self.ingest('13:20:00');self.ingest('16:00:00')
        self.assertIsNone(news.snapshot(self.s,DAY))
        self.assertEqual(self.s.db.execute('SELECT count(*) FROM news_pending_snapshots').fetchone()[0],1)
        self.s.db.execute('INSERT INTO d_calendar VALUES (?,?)',(DAY,'fixture'));self.s.db.commit()
        result=self.record();self.assertEqual(result['status'],'disabled')
        self.assertIn('13:20',news.snapshot(self.s,DAY)['at'])
        self.assertEqual(self.s.db.execute('SELECT count(*) FROM news_pending_snapshots').fetchone()[0],0)
    def test_p5_disabled_preview_is_separate_immutable_and_contains_only_daily_and_snapshot(self):
        self.ingest('13:00:00')
        with ForbiddenClient().guard():result=self.record()
        self.assertEqual((result['method'],result['prompt_version'],result['status']),('jev_news','p5','disabled'))
        self.assertFalse(news.JEV_NEWS_ENABLED)
        state=json.loads(result['state_json']);self.assertEqual(set(state),{'daily','news'})
        self.assertEqual(state['daily'],[{'relative_close':.01}]);self.assertNotIn('at',state['news'])
        self.assertNotIn(DAY,result['state_json']);self.assertNotIn('2330',result['state_json'])
        self.ingest('17:00:00',source_count=999)
        self.assertEqual(self.record(),result)
        with self.assertRaises(sqlite3.IntegrityError):self.s.db.execute("UPDATE news_forward_inputs SET status='no_news'")
        self.s.db.rollback()
    def test_no_news_no_tables_is_normal_no_transport(self):
        self.assertEqual(news.status_view(self.s),dict(status='ok',received_at=None,jev_news_enabled=False))
        with ForbiddenClient().guard():row=self.record()
        self.assertEqual(row['message'],'無新聞資料')


class NewsServiceTests(unittest.TestCase):
    def test_existing_forward_service_records_missing_or_ready_news_without_extra_jev(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'db';ledger=Path(temp)/'ledger'
            with DailyStore(path) as s:fixture(s)
            writer=DBWriter(path);writer.ready.result(5)
            service=DailyForwardService(writer,ActivityGate(),autostart=False,now=lambda:moment(DAY+'T17:00:00'),ledger_path=ledger)
            try:
                with patch.dict(os.environ,{},clear=True),ForbiddenClient().guard():result=service.cycle()
                self.assertEqual((result['new_predictions'],result['jev_http_calls']),(9,0))
                with DailyStore(path,readonly=True) as s:
                    row=dict(s.db.execute('SELECT * FROM news_forward_inputs').fetchone())
                    self.assertEqual(row['message'],'無新聞資料')
                    self.assertEqual(s.db.execute('SELECT count(*) FROM d_forward_predictions WHERE method=\'jev_news\'').fetchone()[0],0)
            finally:service.close();writer.close();writer.thread.join(5)


class NewsProtocolTests(unittest.TestCase):
    def test_topic_and_validation_before_queue_receipt_is_local_and_queue_bounded(self):
        from back.runtime import Application
        app=Application.__new__(Application);app.closed=False
        app._news_slots=threading.BoundedSemaphore(64);app.writer=Mock();app.writer.submit.side_effect=lambda fn:Future()
        with patch('back.news_digest.now',return_value=moment(DAY+'T13:21:00')):
            app.event('wrong.topic',payload());app.event(news.TOPIC,dict(payload(),schema=2))
            app.event(news.TOPIC,payload(),wire_size=8193);app.writer.submit.assert_not_called()
            app.event(news.TOPIC,payload());self.assertEqual(app.writer.submit.call_count,1)
            operation=app.writer.submit.call_args.args[0]
            with DailyStore(':memory:') as s:
                operation(s);self.assertIn('13:21:00',news.status_view(s)['received_at'])
            for _ in range(100):app.event(news.TOPIC,payload())
            self.assertEqual(app.writer.submit.call_count,64)
            app.closed=True;app.event(news.TOPIC,payload());self.assertEqual(app.writer.submit.call_count,64)

    def test_real_ndjson_subscription_route_drop_invalid_and_keep_running_without_news(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'db'
            with Store(path):pass
            child=Child(path,Path(temp)/'gates');self.addCleanup(child.close)
            child.ready();child.send('up');child.mark('up-handled')
            def query(n):
                child.send('msg',body=dict(op='news_status',request_id=n))
                return child.get(lambda p:p.get('body',{}).get('request_id')==n)['body']
            self.assertIsNone(query(1)['received_at'])
            old=payload('2026-01-01T12:00:00+08:00')
            child.send('event',topic=news.TOPIC,body=dict(old,schema=2))
            child.send('event',topic='other.topic',body=old)
            child.send('event',seq=child.seq+1,topic=news.TOPIC,body=old)
            oversized=json.dumps(dict(t='event',seq=child.seq,topic=news.TOPIC,body=old)).replace('"schema": 1','"schema":'+' '*8193+'1')
            child.proc.stdin.write((oversized+'\n').encode());child.proc.stdin.flush()
            self.assertIsNone(query(2)['received_at'])
            child.send('event',topic=news.TOPIC,body=old)
            child.get(lambda p:p.get('body',{}).get('op')=='news_changed')
            self.assertIsNotNone(query(3)['received_at'])
            child.finish();self.assertEqual(child.proc.returncode,0)
            with Store(path,readonly=True) as s:self.assertEqual(s.db.execute('SELECT count(*) FROM news_digests').fetchone()[0],1)
