from copy import deepcopy
from dataclasses import replace
from datetime import date,timedelta
from fractions import Fraction
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from back.daily_indicators import Frame,frames
from back.daily_experiment import SCHEMA as ORIGINAL_SCHEMA
from back.daily_models import Record,Logistic,matured,probabilities
from back.daily_replay import DEV_START,DEV_END
from back.daily_run import run_horizon as run_original
from back.daily_store import DailyStore
from back.data import DataError
from back.evolution_run import ForbiddenClient
from back.experiment import canonical
from back.market_config import INDICATORS,NUMERIC,STATES,METHODS,PROTOCOL,MARKET_INDICATORS
from back.market_features import MarketHistory,features,trend,returns
from back.market_models import MarketLogistic,WalkForwardMarket,design_row,boundaries_for,state
from back.market_sources import positive,parse,MarketClient,MarketBatch,SOURCES,START
from back.market_store import ensure_schema,write_batches,freeze,load_snapshot,source_summary
from back.market_run import run_horizon,development_report
from tests.daily_fixture import seed_daily


def weekdays(start,count):
    d=date.fromisoformat(start);result=[]
    while len(result)<count:
        if d.weekday()<5:result.append(d.isoformat())
        d+=timedelta(days=1)
    return result


def source_rows(days):
    return [dict(series=s,day=d,close=str(100+i) if s!='usd_twd' else None,
                 spot_buy='29' if s=='usd_twd' else None,spot_sell='31' if s=='usd_twd' else None)
            for s in SOURCES for i,d in enumerate(days)]


def frame(day,index=0):
    return Frame(day,index,True,{n:(index%17-8)/100 for n in NUMERIC},
                 {n:STATES[n][index%len(STATES[n])] for n in INDICATORS})


class MarketFeatureTests(unittest.TestCase):
    def setUp(self):
        self.days=weekdays('2010-01-04',120)
        self.rows=source_rows(self.days)
        self.point=frame(self.days[90],90)

    def calculate(self,rows):return features(self.point,MarketHistory(rows),'600')[0]

    def test_future_us_and_same_day_fx_invariant_past_changes(self):
        before=self.calculate(self.rows)
        changed=deepcopy(self.rows)
        for r in changed:
            if r['day']>=self.point.day and r['series'] in ('tsm','sox','usd_twd'):
                r.update(close='999999',spot_buy='100',spot_sell='102')
        self.assertEqual(before.serialized(),self.calculate(changed).serialized())
        for series,names in (('tsm',('adr_premium',)),('usd_twd',('adr_premium',)),
                             ('sox',('sox_ret1','sox_trend')),('taiex',('mkt_trend','mkt_ret5'))):
            changed=deepcopy(self.rows)
            latest=self.point.day if series=='taiex' else self.days[89]
            for r in changed:
                if r['series']==series and r['day']==latest:r.update(close='1000',spot_buy='40',spot_sell='42')
            after=self.calculate(changed)
            for name in names:self.assertNotEqual(before.values[name],after.values[name])

    def test_age_anchored_at_prediction_day_five_included_six_missing(self):
        self.point=frame('2010-06-14') # Monday: prior Tuesday is six calendar days old.
        for age in (3,5,6):
            day=(date.fromisoformat(self.point.day)-timedelta(days=age)).isoformat()
            rows=source_rows([day])
            p,info=features(self.point,MarketHistory(rows),'600')
            self.assertEqual(p.values['adr_premium'] is None,age>5)
            self.assertEqual(info['tsm']['age'],age)
            for s in SOURCES:self.assertEqual(bool(MarketHistory(rows).available(s,self.point.day)[0]),age<=5)
        with self.assertRaises(DataError):features(replace(self.point,day='2022-01-03'),MarketHistory([]),'600')

    def test_missing_latest_never_backfills(self):
        for code in (None,'-1','-99','0','NaN','--'):
            for series,names in (('tsm',('adr_premium',)),('usd_twd',('adr_premium',)),
                                 ('sox',('sox_ret1','sox_trend')),('taiex',('mkt_ret5','mkt_trend'))):
                rows=deepcopy(self.rows);latest=self.point.day if series=='taiex' else self.days[89]
                for r in rows:
                    if r['series']==series and r['day']==latest:r['spot_buy' if series=='usd_twd' else 'close']=code
                p=self.calculate(rows)
                for name in names:self.assertIsNone(p.values[name]);self.assertEqual(p.states[name],'missing')

    def test_adr_midpoint_one_to_five_raw_close(self):
        self.point=frame('2010-06-14')
        rows=source_rows(['2010-06-11']) # TSM=100, FX=(29+31)/2, 2330=600.
        self.assertEqual(self.calculate(rows).values['adr_premium'],0.)
        self.assertEqual(features(self.point,MarketHistory(rows),'300')[0].values['adr_premium'],1.)
        for r in rows:
            if r['series']=='usd_twd':r['spot_sell']='33'
        self.assertAlmostEqual(self.calculate(rows).values['adr_premium'],1/30)
        self.assertIsNone(features(self.point,MarketHistory(rows),None)[0].values['adr_premium'])

    def test_trend_windows_include_latest_and_equality_neutral(self):
        for n in (20,60):
            rows=[{'close':'100'} for _ in range(n)]
            self.assertEqual(trend(rows,n),(0.,'neutral'))
            rows[-1]['close']='200'
            value,level=trend(rows,n)
            self.assertAlmostEqual(value,200/((100*(n-1)+200)/n)-1);self.assertEqual(level,'bull')
            rows[-1]['close']='50';self.assertEqual(trend(rows,n)[1],'bear')
            self.assertEqual(trend(rows[:-1],n),(None,'missing'))
            rows[3]['close']=None;self.assertEqual(trend(rows,n),(None,'missing'))
        rows=[{'close':str(x)} for x in (100,110,120,130,140,150)]
        self.assertEqual(returns(rows,5),.5);self.assertAlmostEqual(returns(rows,1),150/140-1)
        self.assertIsNone(returns(rows[:-1],5))
        rows[2]['close']='-99';self.assertIsNone(returns(rows,5))
        p=self.calculate(self.rows)
        self.assertAlmostEqual(p.values['mkt_trend'],190/160.5-1)
        self.assertAlmostEqual(p.values['sox_trend'],189/179.5-1)
        self.assertAlmostEqual(p.values['mkt_ret5'],190/185-1)
        self.assertAlmostEqual(p.values['sox_ret1'],189/188-1)


class MarketSourceTests(unittest.TestCase):
    def test_missing_codes_and_close_not_adjusted_close(self):
        for value in (None,True,False,0,-1,-99,'NaN','Infinity','-Infinity','--',''):
            self.assertIsNone(positive(value))
        self.assertEqual(positive('30.000'),'30')
        b=parse(dict(status=200,msg='success',data=[dict(date='2009-01-02',stock_id='TSM',Close='100',Adj_Close='20')]),'tsm')
        self.assertEqual(b.rows[0]['close'],'100')
        b=parse(dict(status=200,msg='success',data=[dict(date='2009-01-02',currency='USD',spot_buy=-1,spot_sell=31)]),'usd_twd')
        self.assertIsNone(b.rows[0]['spot_buy']);self.assertEqual(b.rows[0]['spot_sell'],'31')

    def test_reject_invalid_response_and_holdout_before_network(self):
        good=dict(date='2009-01-02',stock_id='TSM',Close=100)
        for payload in ({'status':402,'msg':'success','data':[]},{'status':200,'msg':'wrong','data':[]},
                        dict(status=200,msg='success',data=[good,good]),
                        dict(status=200,msg='success',data=[dict(good,date='2022-01-03')]),
                        dict(status=200,msg='success',data=[dict(good,stock_id='AAPL')])):
            with self.assertRaises(DataError):parse(payload,'tsm')
        client=MarketClient()
        with patch.object(client.http,'get') as http:
            for end in ('2022-01-03','2024-07-25'):
                with self.assertRaises(DataError):client.fetch('tsm',end=end)
            http.assert_not_called()

    def test_cli_preview_has_no_side_effects(self):
        from scripts.sync_market import main as sync
        from scripts.run_market_dev import main as run
        with tempfile.TemporaryDirectory() as tmp,patch('sys.stdout',new=io.StringIO()):
            path=Path(tmp)/'absent.sqlite3';guard=ForbiddenClient()
            with guard.guard():
                self.assertEqual(sync(['--db',str(path)]),0)
                self.assertEqual(run(['--db',str(path)]),0)
            self.assertFalse(path.exists());self.assertEqual(guard.calls,0)


class MarketModelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.points=tuple(frame(d,i) for i,d in enumerate(weekdays('2010-04-01',330)))
        cls.records=tuple(Record(p,3,cls.points[i+3].day,('up','flat','down')[i%3]) for i,p in enumerate(cls.points[:-3]))

    def test_future_labels_features_do_not_change_fitted_model(self):
        point=self.points[280]
        changed=tuple(replace(r,truth='up',frame=replace(r.frame,values={k:9999. for k in NUMERIC},
            states={k:'missing' for k in INDICATORS})) if r.end_day>point.day else r for r in self.records)
        models=[];outputs=[]
        for records in (self.records,changed):
            model=WalkForwardMarket(3,records,point.index)
            outputs.append(model.predict(point));models.append(model.model)
        self.assertEqual(outputs[0],outputs[1]);self.assertEqual(models[0],models[1])
        self.assertEqual(models[0].train_n,278)
        self.assertLessEqual(models[0].train_last_end,point.day)

    def test_all_horizon_maturity_and_single_minimum_thirty(self):
        for H in (3,7,14):
            records=tuple(Record(p,H,self.points[i+H].day,'up' if i<30 else 'down') for i,p in enumerate(self.points[:-H]))
            point=self.points[50+H]
            records=tuple(replace(r,frame=replace(r.frame,states=dict(r.frame.states,mkt_trend='bull' if r.frame.index<30 else 'bear'))) for r in records)
            point=replace(point,states=dict(point.states,mkt_trend='bull'))
            model=WalkForwardMarket(H,records,point.index)
            result,_,ref=model.predict(point)
            self.assertEqual(result['ind_mkt_trend'].probabilities,dict(up=31/33,flat=1/33,down=1/33))
            self.assertEqual(ref.probabilities,probabilities(r.truth for r in records[:51]))
            fewer=tuple(replace(r,frame=replace(r.frame,states=dict(r.frame.states,mkt_trend='bear'))) if r.frame.index==29 else r for r in records)
            self.assertEqual(WalkForwardMarket(H,fewer,point.index).predict(point)[0]['ind_mkt_trend'].probabilities,ref.probabilities)
            missing=replace(point,states=dict(point.states,mkt_trend='missing'))
            self.assertEqual(WalkForwardMarket(H,records,point.index).predict(missing)[0]['ind_mkt_trend'].probabilities,ref.probabilities)

    def test_refit_clock_minimum_training_and_order_guards(self):
        model=WalkForwardMarket(3,self.records,self.points[0].index)
        with patch.object(MarketLogistic,'fit',wraps=MarketLogistic.fit) as fit:
            for i in (240,252,259):
                result,_,ref=model.predict(self.points[i]);self.assertEqual(result['mkt_logit'].probabilities,ref.probabilities)
            fit.assert_not_called()
            model.predict(self.points[260]);self.assertEqual(fit.call_count,1)
            old=model.model;model.predict(self.points[270]);model.predict(self.points[279]);self.assertIs(old,model.model)
            model.predict(self.points[280]);self.assertEqual(fit.call_count,2)
        with self.assertRaises(DataError):model.predict(self.points[280])
        with self.assertRaises(DataError):model.predict(replace(self.points[300],day='2022-01-03'))
        with self.assertRaises(DataError):MarketLogistic.fit(self.records[:249],self.points[280])

    def test_train_only_terciles_lower_ties_and_missing_design(self):
        train=self.records[:7];bounds=boundaries_for(train)
        for name in ('bias20','mkt_ret5','adr_premium','sox_ret1'):
            self.assertEqual(bounds[name],(-.06,-.04))
            for value,level in ((-.06,'low'),(-.04,'middle'),(0.,'high'),(None,'missing')):
                p=replace(self.points[280],values={name:value})
                self.assertEqual(state(p,name,bounds),level)
        p=replace(self.points[280],values={},states={})
        row=design_row(p,(100.,)*len(NUMERIC),(2.,)*len(NUMERIC),bounds)
        self.assertEqual(row[0],1.);self.assertEqual(row[1:1+len(NUMERIC)],(0.,)*len(NUMERIC))
        pos=1+len(NUMERIC)
        for name in INDICATORS:
            width=len(STATES[name])+1
            self.assertEqual(row[pos:pos+width],(0.,)*(width-1)+(1.,));pos+=width
        self.assertEqual(pos,len(row));self.assertEqual(len(NUMERIC),20);self.assertEqual(len(INDICATORS),16)

    def test_optimizer_exactly_matches_original_with_original_columns(self):
        # A reduced schema makes the new implementation numerically identical to the old one.
        from back.daily_config import NUMERIC as old_numeric,INDICATORS as old_indicators
        train=self.records[:260];point=self.points[280]
        expected=Logistic.fit(train,point)
        with patch('back.market_models.NUMERIC',old_numeric),patch('back.market_models.INDICATORS',old_indicators):
            actual=MarketLogistic.fit(train,point)
            self.assertEqual(actual.weights,expected.weights)
            self.assertEqual(actual.means,expected.means);self.assertEqual(actual.scales,expected.scales)
            self.assertEqual(actual.predict(point).probabilities,expected.predict(point).probabilities)
        self.assertEqual(actual.boundaries['bias20'],expected.boundaries)


class MarketPersistenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Small synthetic development fixture; original experiment verifier is tested separately.
        cls.source=DailyStore(':memory:');seed_daily(cls.source,200)
        cls.source.db.executescript(ORIGINAL_SCHEMA)
        cls.points=tuple(p for p in frames(cls.source,end='2010-09-30') if p.predictable and p.day>=DEV_START)[:45]
        cls.original=dict(data_digest='original-full',dev_digest='original-dev',config=dict(
            start='2010-01-04',dev_start=DEV_START,dev_end=DEV_END,
            thresholds={str(H):dict(numerator=1,denominator=100) for H in (3,7,14)}))
        with patch('back.daily_run.load_experiment',return_value=cls.original),patch('back.daily_run.prepare_development',return_value=cls.points):
            for H in (3,7,14):run_original(cls.source,H=H)
        ensure_schema(cls.source)
        rows=source_rows(weekdays('2009-01-01',500))
        cls.batches=tuple(MarketBatch(s,START,DEV_END,tuple(r for r in rows if r['series']==s),'payload-'+s) for s in SOURCES)
        write_batches(cls.source,cls.batches)

    @classmethod
    def tearDownClass(cls):cls.source.close()

    def setUp(self):
        self.s=DailyStore(':memory:');self.source.db.backup(self.s.db);self.addCleanup(self.s.close)
        for name,value in (('back.market_store.load_experiment',self.original),('back.market_run.prepare_development',self.points)):
            p=patch(name,return_value=value);p.start();self.addCleanup(p.stop)

    def test_freeze_preserves_original_and_rejects_source_or_policy_mutations(self):
        snap=freeze(self.s,PROTOCOL)
        self.assertEqual(snap['original_data_digest'],self.original['data_digest'])
        self.assertEqual(snap['original_dev_digest'],self.original['dev_digest'])
        self.assertNotEqual(snap['market_digest'],self.original['dev_digest'])
        for sql in ("UPDATE market_rows SET close='999'",'DELETE FROM market_rows',
                    "INSERT INTO market_rows VALUES ('tsm','2015-01-02','10',NULL,NULL)",
                    "UPDATE market_rows SET day='2022-01-03'",'DELETE FROM market_fetches',
                    "UPDATE market_fetches SET start_day='2030-01-01'",'DELETE FROM market_experiments',
                    "UPDATE market_experiments SET market_digest='x'",
                    'INSERT OR REPLACE INTO market_experiments SELECT * FROM market_experiments'):
            with self.assertRaises(sqlite3.IntegrityError):self.s.db.execute(sql)
            self.s.db.rollback()
        with self.assertRaises(DataError):write_batches(self.s,self.batches)
        with self.assertRaises(DataError):load_snapshot(self.s,dict(PROTOCOL,adr_ratio=1))
        with patch('back.market_store.load_experiment',return_value=dict(self.original,dev_digest='changed')):
            with self.assertRaises(DataError):load_snapshot(self.s,PROTOCOL)
        self.s.db.execute('DROP TRIGGER market_rows_frozen_update')
        self.s.db.execute("UPDATE market_rows SET close='999' WHERE series='tsm'");self.s.db.commit()
        with self.assertRaisesRegex(DataError,'digest_changed'):load_snapshot(self.s,PROTOCOL)

    def test_atomic_four_sources_and_no_holdout_rows(self):
        before=source_summary(self.s)
        with self.assertRaises(DataError):write_batches(self.s,self.batches[:3])
        bad=replace(self.batches[0],rows=(dict(self.batches[0].rows[0],day='2022-01-03'),))
        with self.assertRaises(DataError):write_batches(self.s,(bad,*self.batches[1:]))
        self.assertEqual(before,source_summary(self.s))
        with self.assertRaises(sqlite3.IntegrityError):self.s.db.execute("INSERT INTO market_rows VALUES ('tsm','2022-01-03','100',NULL,NULL)")

    def run_all(self):
        freeze(self.s,PROTOCOL)
        for H in (3,7,14):run_horizon(self.s,H)

    def test_offline_roundtrip_idempotence_old_results_unchanged_and_dev_guards(self):
        old=tuple(tuple(r) for r in self.s.db.execute('SELECT * FROM d_predictions'))
        guard=ForbiddenClient()
        with guard.guard():
            self.run_all();report=development_report(self.s)
            self.assertEqual(run_horizon(self.s,3)['new_predictions'],0)
        self.assertEqual(guard.calls,0)
        self.assertEqual(old,tuple(tuple(r) for r in self.s.db.execute('SELECT * FROM d_predictions')))
        self.assertEqual(report['comparisons'],18);self.assertEqual(report['expected_lucky'],.45)
        self.assertEqual(sum(len(v) for v in report['horizons'].values()),18)
        for results in report['horizons'].values():
            for p in results.values():
                self.assertTrue(p['complete']);self.assertEqual(p['n'],45)
                self.assertAlmostEqual(p['brier_vs_majority']['difference'],p['candidate']['brier']-p['majority']['brier'])
                self.assertEqual(p['brier_vs_majority']['seed'],20260927);self.assertEqual(p['brier_vs_majority']['repetitions'],2000)
        for call in (lambda:run_horizon(self.s,3,split='holdout'),lambda:development_report(self.s,split='holdout')):
            with self.assertRaises(DataError):call()
        with self.assertRaises(sqlite3.IntegrityError):self.s.db.execute("UPDATE market_predictions SET day='2022-01-03'")

    def test_missing_forecast_reference_outcome_never_qualifies(self):
        self.run_all()
        self.s.db.execute("DELETE FROM market_predictions WHERE H=3 AND day=?",(self.points[0].day,))
        self.s.db.execute("DELETE FROM d_predictions WHERE H=7 AND method='majority' AND day=?",(self.points[0].day,))
        self.s.db.execute("DELETE FROM d_outcomes WHERE H=14 AND day=?",(self.points[0].day,));self.s.db.commit()
        with patch('back.market_run.paired_blocks',return_value=dict(ci95=[-.2,-.1],difference=-.15)):
            report=development_report(self.s)
        for results in report['horizons'].values():
            for p in results.values():
                self.assertEqual(p['n'],44);self.assertFalse(p['complete']);self.assertFalse(p['shortlisted'])

    def test_interval_strict_negative_and_tampering_rejected(self):
        self.run_all()
        for ci,yes in (([-.2,-.1],True),([-.2,0.],False),([-.2,.1],False)):
            with patch('back.market_run.paired_blocks',return_value=dict(ci95=ci,difference=-.1)):
                report=development_report(self.s)
            for results in report['horizons'].values():
                for p in results.values():self.assertEqual(p['shortlisted'],yes)
        self.s.db.execute("UPDATE market_features SET alignment_json='{}'");self.s.db.commit()
        with self.assertRaisesRegex(DataError,'feature_mismatch'):development_report(self.s)


if __name__=='__main__':unittest.main()
