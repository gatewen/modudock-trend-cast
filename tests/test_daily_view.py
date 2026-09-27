from datetime import date
from fractions import Fraction
import json
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import patch

from back.daily_config import METHODS, INDICATORS
from back.daily_experiment import create_experiment
from back.daily_indicators import frames
from back.daily_run import run_horizon
from back.daily_prompt import sample_dates
from back.daily_score import development_report
from back.daily_store import DailyStore
from back.daily_view import DailyViews, OPS, HOLD_MESSAGE
from back.daily_replay import DailyReplay, DEV_END
from back.data import DataError
from back.evolution_run import ForbiddenClient
from back.experiment import canonical
from back.protocol import Outbox
from back.runtime import Application
from back.db_writer import DBWriter
from tests.daily_fixture import seed_daily
from tests.test_runtime import Sink


def fixture(store):
    days=seed_daily(store,3900)
    # Synthetic weekday calendar needs exchange holidays so session 61 is in April.
    holidays=set(days[25:30])
    for table in ('d_calendar','d_bars','d_institutional','d_margin'):
        store.db.executemany(f'DELETE FROM {table} WHERE day=?',((d,) for d in holidays))
    store.db.commit();days=[d for d in days if d not in holidays]
    prev=store.db.execute('SELECT close FROM d_bars WHERE day=?',(days[79],)).fetchone()[0]
    store.db.execute('INSERT INTO d_corp_events VALUES (?,?,?,?,?)',('2330',days[80],prev,str(float(prev)/2),'fixture'))
    store.db.commit()
    k={h:dict(k_numerator=1,k_denominator=100) for h in (3,7,14)}
    with patch('back.daily_experiment.development_thresholds',return_value=k): row=create_experiment(store)
    sample=sample_dates(store,row['config'])['days'][:4]
    points=[f for f in frames(store,end=DEV_END) if f.day in sample]
    with patch('back.daily_run.prepare_development',return_value=points):
        for H in (3,7,14):run_horizon(store,H=H)
    for H in (3,7,14):
        for i,p in enumerate(points):
            probs=dict(up=.8,flat=.1,down=.1) if i%2==0 else dict(up=.1,flat=.8,down=.1)
            store.db.execute('INSERT INTO d_predictions VALUES (?,?,?,?,?,?,?,?)',
                (4,H,'jev_ind',p.day,'up' if i%2==0 else 'flat',canonical(probs),None,p.digest))
    store.db.commit()
    return row,days,points


class DailyViewTests(unittest.TestCase):
    def setUp(self):
        self.store=DailyStore(':memory:');self.addCleanup(self.store.close)
        self.row,self.days,self.points=fixture(self.store);self.views=DailyViews()
    def get(self,op,**kw):return self.views.handle(self.store,dict({'op':op,'H':7},**kw))
    def assert_private(self,value):
        dates=re.findall(r'\d{4}-\d{2}-\d{2}',json.dumps(value,ensure_ascii=False))
        self.assertTrue(all(d<=DEV_END for d in dates),dates[-10:])
    def test_all_exits_bounded_readonly_no_network_no_config_leak(self):
        changes=self.store.db.total_changes
        with ForbiddenClient().guard():
            for H in (3,7,14):
                for op,fields in [('daily_status',{}),('daily_chart',{'range':'all'}),
                    ('daily_indicators',{'date':self.points[2].day}),('daily_report',{})]:
                    result=self.views.handle(self.store,dict(op=op,H=H,**fields))
                    self.assert_private(result);self.assertNotIn('config',result)
                    self.assertLess(len(Outbox.encode(dict(t='msg',seq=1,body=result))),900*1024)
        self.assertEqual(changes,self.store.db.total_changes)
        self.assertEqual(self.get('daily_status')['holdout'],dict(state='unused',message='未使用（沒有入圍者，保留給未來）'))
    def test_reject_holdout_exposed_dates_invalid_horizon_and_write_parameters(self):
        for day in ('2022-01-03','2024-07-26','2099-01-01','2010-01-04'):
            with self.subTest(day=day),self.assertRaisesRegex(DataError,'daily_view_dev_only'):self.get('daily_indicators',date=day)
        for fields in (dict(H=30),dict(H=True),dict(experiment_id=1),dict(experiment_id=4.0),dict(split='holdout'),dict(confirmed=True)):
            with self.subTest(fields=fields),self.assertRaises(DataError):self.get('daily_report',**fields)
        for op in ('daily_run','daily_reveal'):
            with self.assertRaises(DataError):self.get(op)
    def test_adjusted_chart_reference_and_ranges_end_at_development(self):
        whole=self.get('daily_chart',range='all');engine=DailyReplay(self.store)
        a,b=whole['prices'][:2]
        self.assertEqual(a['close'],100.)
        previous=engine.bar(a['day']);bar=engine.bar(b['day'])
        self.assertAlmostEqual(b['close']/a['close'],float(Fraction(bar['close'])/engine.reference(b['day'],previous)))
        prices={p['day']:p['close'] for p in whole['prices']}
        self.assertAlmostEqual(prices[self.days[80]]/prices[self.days[79]],float(Fraction(engine.bar(self.days[80])['close'])/engine.reference(self.days[80],engine.bar(self.days[79]))))
        self.assertEqual(whole['prices'][-1]['day'],DEV_END)
        half=self.get('daily_chart',range='6m');year=self.get('daily_chart',range='1y')
        self.assertEqual(half['prices'],[p for p in whole['prices'] if p['day']>='2021-06-30'])
        self.assertEqual(year['prices'],[p for p in whole['prices'] if p['day']>='2020-12-31'])
        self.assertTrue(len(half['prices'])<len(year['prices'])<len(whole['prices']))
        with self.assertRaises(DataError):self.get('daily_chart',range='holdout')
    def test_markers_only_sampled_two_methods_with_boolean_correctness(self):
        chart=self.get('daily_chart',range='all')
        self.assertEqual(len(chart['points']),8)
        self.assertEqual({p['day'] for p in chart['points']},{p.day for p in self.points})
        self.assertEqual({p['method'] for p in chart['points']},{'jev_ind','ind_logit'})
        for p in chart['points']:
            truth=self.store.db.execute('SELECT label FROM d_outcomes WHERE H=7 AND day=?',(p['day'],)).fetchone()[0]
            self.assertIs(p['correct'],p['choice']==truth)
    def test_maturity_outside_dev_is_not_scored_or_colored_and_future_rows_are_inert(self):
        self.store.db.execute('DROP TRIGGER d_outcomes_dev_update')
        self.store.db.execute("UPDATE d_outcomes SET end_day='2022-01-03' WHERE day=?",(self.points[0].day,))
        self.store.db.commit()
        before=self.get('daily_report');chart=self.get('daily_chart',range='all')
        self.assertTrue(all(p['day']!=self.points[0].day for p in chart['points']))
        self.assertTrue(all(m['n']==3 for m in before['methods']))
        for table in ('d_predictions','d_outcomes'):
            self.store.db.execute(f'DROP TRIGGER {table}_dev_insert')
        for day in ('2022-01-03','2024-07-26'):
            self.store.db.execute('INSERT INTO d_predictions VALUES (4,7,?,?,?, ?,NULL,?)',
                ('PRIVATE_METHOD',day,'up','{"up":1,"flat":0,"down":0}','private'))
            self.store.db.execute('INSERT INTO d_outcomes VALUES (4,7,?,?,?, ?,?)',(day,day,'down','999','1'))
        self.store.db.execute("UPDATE d_bars SET close='999999999' WHERE day>'2024-07-25'")
        self.store.db.commit()
        self.assertEqual(before,self.get('daily_report'));self.assertEqual(chart,self.get('daily_chart',range='all'))
        for op in OPS:
            kw=dict(date=self.points[1].day) if op=='daily_indicators' else {}
            self.assert_private(self.get(op,**kw))
    def test_eighteen_methods_comparison_matches_existing_report_and_small_samples(self):
        with patch('back.daily_score.prepare_development',return_value=self.points):
            expected=development_report(self.store)['horizons']['7']
        actual=self.get('daily_report')
        self.assertEqual([r['method'] for r in actual['methods']],[*METHODS,'jev_ind'])
        for r in actual['methods'][:-1]:
            old=expected['methods'][r['method']]
            self.assertEqual(r['accuracy'],old['accuracy']);self.assertEqual(r['brier'],old['brier'])
            self.assertEqual(r['difference'],old['brier_vs_majority']['difference'])
            self.assertEqual(r['ci95'],old['brier_vs_majority']['ci95'])
        self.assertTrue(all(r['small_sample'] for r in actual['claims']))
        self.assertTrue(any(r['n']>0 for r in actual['claims']))
    def test_indicator_all_eleven_lagged_chips_and_maturity_bias(self):
        day=self.points[2].day;result=self.get('daily_indicators',date=day)
        self.assertEqual([r['name'] for r in result['indicators']],list(INDICATORS))
        kd=next(r for r in result['indicators'] if r['name']=='kd')
        self.assertIn(f'{self.points[2].values["kd_k"]:.1f}',kd['detail'])
        for r in result['indicators']:
            if r['name'] in ('foreign_net','trust_net','margin_chg'):self.assertRegex(r['detail'],r'^-?\d+\.\d{2}%$')
        with self.assertRaises(DataError):self.get('daily_indicators',date='2010-04-03')
    def test_cache_rechecks_source_digest_and_prediction_changes(self):
        before=self.get('daily_report')
        self.store.db.execute("UPDATE d_predictions SET probabilities_json='{\"up\":0.333,\"flat\":0.334,\"down\":0.333}',choice='flat' WHERE method='jev_ind'")
        self.store.db.commit();after=self.get('daily_report')
        self.assertNotEqual(before['methods'][-1]['brier'],after['methods'][-1]['brier'])
        self.store.db.execute('DROP TRIGGER d_bars_freeze_update')
        self.store.db.execute("UPDATE d_bars SET close='99' WHERE day=?",(self.days[0],));self.store.db.commit()
        with self.assertRaises(DataError):self.get('daily_status')


class DailyRuntimeTests(unittest.TestCase):
    def test_actual_read_worker_routes_all_daily_exits_and_zero_http(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'fixture.sqlite3'
            with DailyStore(path) as store:row,days,points=fixture(store)
            sink=Sink();writer=DBWriter(path);writer.ready.result(3)
            app=Application(writer,sink,42)
            try:
                with ForbiddenClient().guard():
                    for i,op in enumerate(OPS):
                        body=dict(op=op,H=7,request_id=i)
                        if op=='daily_indicators':body['date']=points[0].day
                        app.request(body)
                        result=sink.wait(lambda v:v.get('request_id')==i,timeout=10)
                        self.assertEqual(result['op'],op)
                        self.assertFalse(any(d>DEV_END for d in re.findall(r'\d{4}-\d{2}-\d{2}',json.dumps(result))))
                    app.request(dict(op='daily_indicators',H=7,date='2024-07-26',request_id=99))
                    error=sink.wait(lambda v:v.get('request_id')==99)
                    self.assertEqual(error['code'],'daily_view_dev_only');self.assertNotIn('2024',json.dumps(error))
            finally:
                app.close();writer.thread.join(3);app.reader.join(3)
