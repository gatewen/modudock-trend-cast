from contextlib import contextmanager
from dataclasses import replace
from fractions import Fraction
import hashlib
import io
import json
import math
import random
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from back.daily_config import METHODS, INDICATORS, NUMERIC, PROTOCOL, settings
from back.daily_experiment import create_experiment, load_experiment, source_summary, digest, ensure_schema
from back.daily_indicators import frames, chip_values, Frame
from back.daily_models import (Record, matured, probabilities, cuts, bucket, conditional,
                               prediction, Logistic, WalkForwardDaily, design_row)
from back.daily_replay import DailyReplay, DEV_END, HOLD_END
from back.daily_run import run_horizon, development_records
from back.daily_score import paired_blocks, development_report
from back.daily_store import DailyStore
from back.data import DataError
from back.evolution_run import ForbiddenClient
from back.experiment import canonical
from back.score import ScoredPoint
from scripts.run_daily_dev import main
from tests.daily_fixture import seed_daily


def frame(i, *, day=None, value=0., state='bull'):
    return Frame(day or f'2010-05-{i+1:02}',i,True,
        {name:value for name in NUMERIC},
        {name:state for name in INDICATORS if name!='bias20'},value,
        {3:Fraction(1,100),7:Fraction(-1,100),14:Fraction(0)})


class IndicatorTests(unittest.TestCase):
    def setUp(self):
        self.store=DailyStore(':memory:');self.days=seed_daily(self.store,410)
    def tearDown(self): self.store.close()
    def test_future_price_corp_chips_and_same_day_chips_do_not_change_prefix(self):
        t=self.days[200];before=frames(self.store,end=self.days[-1]);rng=random.Random(42)
        with self.store.transaction():
            for day in self.days[201:]:
                v=str(rng.randint(200,400))
                self.store.db.execute('UPDATE d_bars SET open=?,high=?,low=?,close=?,volume=? WHERE day=?',(v,v,v,v,str(rng.randint(1,99999)),day))
            self.store.db.execute('UPDATE d_institutional SET buy=buy*99 WHERE day>=?',(t,))
            self.store.db.execute('UPDATE d_margin SET margin_balance=margin_balance*99 WHERE day>=?',(t,))
            self.store.db.execute('INSERT INTO d_corp_events VALUES (?,?,?,?,?)',('2330',self.days[201],'123','111','fixture'))
            self.store.db.execute('UPDATE d_corp_coverage SET end_day=?',(t,))
        after=frames(self.store,end=self.days[-1])
        self.assertTrue(before[200].predictable)
        self.assertEqual([p.serialized() for p in before[:201]],[p.serialized() for p in after[:201]])
        self.assertFalse(after[201].predictable)
        self.assertEqual(before[200],frames(self.store,end=t)[-1])
    def test_chip_lag_window_missing_and_category_transition(self):
        days=self.days
        inst={d:{'Foreign_Investor':i,'Investment_Trust':i*2} for i,d in enumerate(days)}
        margin={d:i*10 for i,d in enumerate(days)}
        result=chip_values(days,100,inst,margin)
        self.assertEqual(result,dict(foreign_net=97+98+99,trust_net=(97+98+99)*2,margin_chg=50))
        inst[days[100]]={'Foreign_Investor':999999,'Investment_Trust':999999}
        self.assertEqual(result,chip_values(days,100,inst,margin))
        del inst[days[99]]['Foreign_Investor']
        self.assertIsNone(chip_values(days,100,inst,margin)['foreign_net'])
        later=['2018-01-01','2018-01-02','2018-01-03','2018-01-04']
        groups={d:{'Foreign_Investor':4,'Foreign_Dealer_Self':2,'Investment_Trust':1} for d in later}
        self.assertEqual(chip_values(later,3,groups,{})['foreign_net'],18)
        del groups[later[0]]['Foreign_Dealer_Self']
        self.assertIsNone(chip_values(later,3,groups,{})['foreign_net'])
    def test_flat_indicators_and_zero_volume(self):
        with self.store.transaction(): self.store.db.execute("UPDATE d_bars SET open='100',close='100',high='101',low='99',volume='100'")
        p=frames(self.store,end=self.days[-1])[-1]
        self.assertEqual(p.values['rsi14'],50.)
        self.assertEqual((p.values['kd_k'],p.values['kd_d']),(50.,50.))
        self.assertEqual(p.values['macd_hist'],0.)
        self.assertEqual(p.states['ma_trend'],'neutral');self.assertEqual(p.states['ma_cross'],'neutral')
        self.assertEqual(p.states['vol_price'],'flat_high')
        with self.store.transaction(): self.store.db.execute("UPDATE d_bars SET volume='0'")
        self.assertEqual(frames(self.store,end=self.days[-1])[-1].states['vol_price'],'missing')
    def test_recursive_indicator_regression_and_latest_cross(self):
        p=frames(self.store,end=self.days[-1])[200]
        expected=dict(rsi14=51.524577989151,kd_k=58.71617302939225,kd_d=57.033467977533974,
                      macd_dif=.005602755965683964,macd_signal=.0019105325547334512,macd_hist=.0036922234109505133)
        for name,value in expected.items():self.assertAlmostEqual(p.values[name],value,places=11)
        self.assertEqual(p.states['ma_cross'],'bull')
    def test_adjusted_indicator_reference_and_exact_momentum(self):
        i=150
        with self.store.transaction():
            self.store.db.execute("UPDATE d_bars SET open='61.4',high='62',low='61',close='61.4' WHERE day=?",(self.days[i-1],))
            self.store.db.execute("UPDATE d_bars SET open='59',high='60',low='58',close='59.9' WHERE day=?",(self.days[i],))
            self.store.db.execute('INSERT INTO d_corp_events VALUES (?,?,?,?,?)',('2330',self.days[i],'61.4','58.4','fixture'))
        f=frames(self.store,end=self.days[i+3])
        self.assertAlmostEqual(f[i].values['vol_price_return'],float(Fraction('59.9')/Fraction('58.4')-1))
        self.assertEqual(f[i+3].past_returns[3],DailyReplay(self.store).outcome(self.days[i],3).adjusted_return)
        self.assertEqual(f[i].past_returns[3],DailyReplay(self.store).outcome(self.days[i-3],3).adjusted_return)
    def test_trend_wilder_kd_macd_and_inclusive_volume_mean(self):
        with self.store.transaction():
            for i,d in enumerate(self.days):
                c=100+i
                self.store.db.execute('UPDATE d_bars SET open=?,high=?,low=?,close=?,volume=? WHERE day=?',(str(c),str(c+1),str(c-1),str(c),str(1000+i),d))
        f=frames(self.store,end=self.days[-1]);p=f[100]
        self.assertAlmostEqual(p.values['ma_cross'],198/190.5-1)
        self.assertAlmostEqual(p.values['ma_trend'],200/170.5-1)
        self.assertAlmostEqual(p.values['vol_price_ratio'],1100/1090.5)
        self.assertEqual(p.values['rsi14'],100.)
        self.assertEqual(p.states['ma_trend'],'bull')
        self.assertAlmostEqual(p.values['kd_k'],90.,places=10)
        self.assertGreater(p.values['macd_dif'],0)
        self.assertEqual(p.states['vol_price'],'up_high')
        self.assertFalse(f[59].predictable);self.assertTrue(f[60].predictable)
        self.assertAlmostEqual(p.volatility,math.sqrt(sum((1/(100+j)-sum(1/(100+k) for k in range(80,100))/20)**2 for j in range(80,100))/20))
    def test_missing_calendar_bar_and_unknown_corp_refuse_prefix(self):
        with self.store.transaction(): self.store.db.execute('DELETE FROM d_calendar WHERE day=?',(self.days[150],))
        self.assertFalse(frames(self.store,end=self.days[160])[-1].predictable)
        with self.store.transaction(): self.store.db.execute('DELETE FROM d_bars WHERE day=?',(self.days[140],))
        self.assertFalse(frames(self.store,end=self.days[149])[-1].predictable)


class DailyModelTests(unittest.TestCase):
    def test_maturity_boundary_future_truth_and_features_invariant(self):
        p=frame(10,day='2010-05-20',value=2)
        known=Record(frame(7,day='2010-05-10',value=1),3,p.day,'down')
        future=Record(frame(8,day='2010-05-11',value=100),3,'2010-05-21','up')
        self.assertEqual(matured([known,future],p,3),(known,))
        poisoned=replace(future,truth='poison',frame=replace(future.frame,values={k:-9999 for k in NUMERIC},volatility=9999))
        a=WalkForwardDaily(3,Fraction(1,100),[known,future],10).predict(p)
        b=WalkForwardDaily(3,Fraction(1,100),[known,poisoned],10).predict(p)
        self.assertEqual(a,b);self.assertEqual(a[0]['majority'].probabilities,dict(up=.25,flat=.25,down=.5))
        self.assertEqual(matured([replace(known,end_day='2010-05-21')],p,3),())
    def test_smoothing_fallback_missing_and_30_sample_boundary(self):
        p=frame(100,day='2010-10-10')
        r=[Record(frame(i,day=f'2010-04-{i+1:02}',state='bull'),3,'2010-06-01','up') for i in range(30)]
        majority=dict(up=.1,flat=.8,down=.1)
        self.assertEqual(conditional('ind_ma_cross',p,r[:29],majority)[0].probabilities,majority)
        self.assertEqual(conditional('ind_ma_cross',p,r,majority)[0].probabilities,dict(up=31/33,flat=1/33,down=1/33))
        missing=replace(p,states=dict(p.states,ma_cross='missing'))
        self.assertEqual(conditional('ind_ma_cross',missing,r,majority)[0].probabilities,majority)
        self.assertEqual(prediction('majority',probabilities([])).answer,'flat')
        self.assertEqual(prediction('majority',dict(up=.4,flat=.2,down=.4)).answer,'up')
    def test_tercile_interpolation_tie_lower_and_matured_only(self):
        self.assertEqual(cuts([0,3,6,9]),(3,6))
        self.assertEqual(bucket(3,(3,6)),'low');self.assertEqual(bucket(6,(3,6)),'middle')
        self.assertEqual(cuts([0,9]),(3,6));self.assertEqual(bucket(None,(3,6)),'missing')
        p=frame(200,day='2011-01-01',value=0)
        r=[Record(frame(i,day=f'2010-05-{i%28+1:02}',value=float(i)),3,'2010-12-01','up' if i<30 else 'down') for i in range(90)]
        base=probabilities(x.truth for x in r)
        for method in ('vol_prior_d','ind_bias20'):
            got,state=conditional(method,p,r,base)
            self.assertEqual(state,'low');self.assertEqual(got.probabilities['up'],31/33)
    def test_exact_momentum_reversal_and_no_holdout_prediction(self):
        p=frame(100,day='2010-08-01')
        results,_=WalkForwardDaily(3,Fraction(1,100),[],100).predict(p)
        self.assertEqual(results['momentum_H'].answer,'up');self.assertEqual(results['reversal_H'].answer,'down')
        self.assertEqual(results['always_flat'].probabilities,dict(up=0,flat=1,down=0))
        for day in ('2022-01-03','2024-07-26'):
            with self.assertRaises(DataError):WalkForwardDaily(3,Fraction(1,100),[],100).predict(replace(p,day=day))
    def training(self):
        store=DailyStore(':memory:');days=seed_daily(store,410)
        fs=[f for f in frames(store,end=days[-1]) if f.predictable and f.day>='2010-04-01']
        records=[Record(f,3,days[f.index+3],('up','flat','down')[i%3]) for i,f in enumerate(fs[:-3])]
        store.close();return fs,records
    def test_logit_train_only_scaler_cuts_and_future_labels(self):
        fs,records=self.training();point=fs[280]
        train=matured(records,point,3);a=Logistic.fit(train,point)
        future=[replace(r,truth='poison',frame=replace(r.frame,values={k:99999 for k in NUMERIC})) if r.end_day>point.day else r for r in records]
        b=Logistic.fit(matured(future,point,3),point)
        self.assertEqual(a,b)
        expected=sum(r.frame.values['ma_cross'] for r in train)/len(train)
        self.assertAlmostEqual(a.means[0],expected)
        self.assertEqual(a.boundaries,cuts(r.frame.values['bias20'] for r in train))
        self.assertLessEqual(a.train_last_end,point.day)
        self.assertEqual(a.predict(point),b.predict(point))
        self.assertAlmostEqual(sum(a.predict(point).probabilities.values()),1)
        with self.assertRaises(DataError):a.predict(fs[100])
        with self.assertRaises(DataError):Logistic.fit(train[:249],point)
        for name,value in dict(up=.33349017281732235,flat=.3282775617010275,down=.3382322654816502).items():
            self.assertAlmostEqual(a.predict(point).probabilities[name],value,places=11)
    def test_all_learners_future_records_randomized_after_sufficient_history(self):
        fs,records=self.training();p=fs[310]
        future=[replace(r,truth='down',frame=replace(r.frame,values={k:999999 for k in NUMERIC},
            states={k:'missing' for k in INDICATORS},volatility=99999)) if r.end_day>p.day else r for r in records]
        a=WalkForwardDaily(3,Fraction(1,100),records,p.index).predict(p)
        b=WalkForwardDaily(3,Fraction(1,100),future,p.index).predict(p)
        self.assertEqual(a,b)
    def test_logit_missing_training_imputation_onehot_and_standardization(self):
        fs,records=self.training();point=fs[280]
        records=[replace(r,frame=replace(r.frame,values=dict(r.frame.values,foreign_net=None),states=dict(r.frame.states,foreign_net='missing'))) for r in records]
        train=matured(records,point,3);m=Logistic.fit(train,point)
        pos=NUMERIC.index('foreign_net')
        self.assertEqual(m.means[pos],0);self.assertEqual(m.scales[pos],1)
        v=design_row(train[0].frame,m.means,m.scales,m.boundaries)
        self.assertEqual(v[1+pos],0)
        self.assertTrue(all(x in (0,1) for x in v[1+len(NUMERIC):]))
        self.assertEqual(sum(v[1+len(NUMERIC):]),len(INDICATORS))
        standardized=[design_row(r.frame,m.means,m.scales,m.boundaries)[1] for r in train]
        self.assertAlmostEqual(sum(standardized)/len(standardized),0)
        self.assertAlmostEqual(sum(x*x for x in standardized)/len(standardized),1)
    def test_logit_refit_every_20_sessions_and_initial_majority(self):
        fs,records=self.training();walker=WalkForwardDaily(3,Fraction(1,100),records,fs[0].index)
        fits=[]
        class Fake:
            def predict(self,p):return prediction('ind_logit',dict(up=.8,flat=.1,down=.1))
        def fitted(train,p): fits.append((p.index,len(train)));return Fake()
        with patch('back.daily_models.Logistic.fit',side_effect=fitted):
            for i,p in enumerate(fs[:305]):
                predictions,_=walker.predict(p)
                if i<260:self.assertEqual(predictions['ind_logit'].probabilities,predictions['majority'].probabilities)
        self.assertEqual([index-fs[0].index for index,n in fits],[260,280,300])
        self.assertTrue(all(n>=250 for index,n in fits))
    def test_protocol_exact_roster_and_parameters(self):
        self.assertEqual(len(METHODS),17);self.assertEqual(len(INDICATORS),11)
        self.assertEqual(PROTOCOL['logit']['steps'],300);self.assertEqual(PROTOCOL['logit']['lambda'],1)
        self.assertEqual(PROTOCOL['logit']['learning_rate'],.1);self.assertEqual(PROTOCOL['logit']['refit_sessions'],20)


class DailyExperimentTests(unittest.TestCase):
    def setUp(self):
        self.store=DailyStore(':memory:');self.days=seed_daily(self.store,3900)
    def tearDown(self):self.store.close()
    def create(self):
        thresholds={h:dict(k_numerator=1,k_denominator=100) for h in (3,7,14)}
        with patch('back.daily_experiment.development_thresholds',return_value=thresholds):return create_experiment(self.store)
    def test_freeze_digest_sources_settings_and_legacy_isolation(self):
        self.store.db.execute('CREATE TABLE experiments(id INTEGER PRIMARY KEY,secret TEXT)')
        self.store.db.execute("INSERT INTO experiments VALUES (1,'legacy')");self.store.db.commit()
        row=self.create();config=row['config']
        self.assertEqual(config['dev_end'],'2021-12-31');self.assertEqual(config['hold_end'],'2024-07-25')
        self.assertEqual(config['thresholds']['3'],dict(numerator=1,denominator=100))
        self.assertEqual(len(config['finmind_summary']),3)
        self.assertEqual(self.store.db.execute('SELECT * FROM experiments').fetchall()[0]['secret'],'legacy')
        for table,column in (('d_bars','close'),('d_margin','margin_balance'),('d_institutional','buy')):
            with self.assertRaises(sqlite3.IntegrityError):self.store.db.execute(f'UPDATE {table} SET {column}={column}+1 WHERE day=?',(self.days[200],))
            self.store.db.rollback()
        with self.assertRaises(sqlite3.IntegrityError):self.store.db.execute("UPDATE d_corp_coverage SET source='changed'")
        self.store.db.rollback()
        with self.assertRaises(sqlite3.IntegrityError):self.store.db.execute('DELETE FROM d_experiments')
        self.store.db.rollback()
        with self.assertRaises(sqlite3.IntegrityError):self.store.db.execute("UPDATE d_experiments SET config_json='{}'")
        self.store.db.rollback()
        with self.store.transaction():self.store.db.execute("UPDATE d_bars SET volume='123' WHERE day='2024-12-02'")
        self.assertEqual(load_experiment(self.store)['data_digest'],row['data_digest'])
    def test_digest_changes_for_every_source_and_config(self):
        config=settings();original=source_summary(self.store,config,HOLD_END)
        before=digest(config,original)
        queries=["UPDATE d_bars SET close='200' WHERE day='2010-05-03'",
                 "UPDATE d_institutional SET buy=999 WHERE day='2010-05-03'",
                 "UPDATE d_margin SET margin_balance=999 WHERE day='2010-05-03'",
                 "UPDATE d_calendar SET source='other' WHERE day='2010-05-03'",
                 "INSERT INTO d_corp_events VALUES ('2330','2010-05-03','100','98','fixture')",
                 "UPDATE d_corp_coverage SET source='other'",
                 "UPDATE d_fetch_log SET source='other' WHERE kind='calendar'"]
        for sql in queries:
            self.store.db.execute('SAVEPOINT check_digest');self.store.db.execute(sql)
            self.assertNotEqual(before,digest(config,source_summary(self.store,config,HOLD_END)))
            self.store.db.execute('ROLLBACK TO check_digest');self.store.db.execute('RELEASE check_digest')
        self.assertNotEqual(before,digest(dict(config,thresholds={'3':2}),original))
    def test_creation_refuses_missing_warmup_bar_unknown_corp_and_rolls_back(self):
        cases=[("DELETE FROM d_calendar WHERE day='2010-01-04'",'warmup'),
               ("DELETE FROM d_bars WHERE day='2010-05-03'",'missing_calendar'),
               ("DELETE FROM d_corp_coverage",'unknown_corporate')]
        for sql,reason in cases:
            with self.subTest(reason=reason):
                self.store.db.execute(sql);self.store.db.commit()
                with self.assertRaisesRegex(DataError,reason):self.create()
                self.assertEqual(self.store.db.execute('SELECT count(*) FROM d_experiments').fetchone()[0],0)
                self.store.close();self.store=DailyStore(':memory:');self.days=seed_daily(self.store,3900)
    def test_result_table_guards_refuse_holdout_and_crossing_endpoint(self):
        self.create()
        for d,end in [('2022-01-03','2022-01-06'),('2024-07-26','2024-07-31'),('2021-12-30','2022-01-04')]:
            with self.assertRaises(sqlite3.IntegrityError):self.store.db.execute('INSERT INTO d_outcomes VALUES (4,3,?,?,?,?,?)',(d,end,'up','1','10'))
            self.store.db.rollback()
        with self.assertRaises(sqlite3.IntegrityError):self.store.db.execute("INSERT INTO d_features VALUES (4,'2022-01-03','hash','{}')")
        self.store.db.rollback()
    def test_digest_validation_detects_bypass_and_protocol_change(self):
        self.create()
        self.store.db.execute('DROP TRIGGER d_margin_freeze_update')
        self.store.db.execute("UPDATE d_margin SET margin_balance=123 WHERE day='2010-05-03'")
        self.store.db.commit()
        with self.assertRaisesRegex(DataError,'digest_changed'):load_experiment(self.store)
        with patch.dict(PROTOCOL,version='different'):
            with self.assertRaisesRegex(DataError,'settings_changed'):load_experiment(self.store,verify=False)
    def test_run_offline_rerun_no_refit_and_dev_cap(self):
        row=self.create();points=[p for p in frames(self.store,end='2010-09-30') if p.predictable and p.day>='2010-04-01'][:40]
        guard=ForbiddenClient()
        with guard.guard(),patch('back.daily_run.prepare_development',return_value=points),patch('back.daily_models.Logistic.fit',side_effect=AssertionError('unexpected_fit')):
            first=run_horizon(self.store,H=3);second=run_horizon(self.store,H=3)
        self.assertEqual(guard.calls,0);self.assertEqual(first['new_predictions'],40*17);self.assertEqual(second['new_predictions'],0)
        for split in ('holdout','forward','exposed'):
            with self.assertRaises(DataError):run_horizon(self.store,split=split)
            with self.assertRaises(DataError):development_report(self.store,split=split)
        with self.assertRaises(DataError):run_horizon(self.store,experiment_id=1)
        tails=frames(self.store,end=DEV_END)[-15:]
        original=DailyReplay.outcome
        called=[]
        def checked(engine,day,H,*,end_limit=None):
            called.append(end_limit);self.assertEqual(end_limit,DEV_END)
            return original(engine,day,H,end_limit=end_limit)
        with patch.object(DailyReplay,'outcome',checked):
            _,records,_=development_records(self.store,row,tails,14)
        self.assertEqual(len(records),1);self.assertTrue(called)
    def test_no_execute_no_database_or_http(self):
        guard=ForbiddenClient()
        with tempfile.TemporaryDirectory() as d,guard.guard(),patch('sys.stdout',new=io.StringIO()):
            path=d+'/absent.sqlite3'
            self.assertEqual(main(['--db',path]),0)
            from pathlib import Path
            self.assertFalse(Path(path).exists())
        self.assertEqual(guard.calls,0)
    def test_report_missing_outcome_incomplete_and_all_states_visible(self):
        self.create()
        points=[p for p in frames(self.store,end='2010-09-30') if p.predictable and p.day>='2010-04-01'][:40]
        with patch('back.daily_run.prepare_development',return_value=points):
            for H in (3,7,14):run_horizon(self.store,H=H)
        with patch('back.daily_score.prepare_development',return_value=points):
            a=development_report(self.store)
            self.assertTrue(all(p['complete'] for p in a['horizons'].values()))
            self.assertEqual(a['horizons']['3']['methods']['majority']['brier_vs_majority']['difference'],0)
            self.assertTrue(any(c['n']==0 and c['state']=='bull_cross' for c in a['horizons']['3']['claims_vs_frequency']))
            with self.store.transaction():self.store.db.execute('DELETE FROM d_outcomes WHERE H=3 AND day=?',(points[0].day,))
            b=development_report(self.store)
            self.assertFalse(b['horizons']['3']['complete'])
            self.assertEqual(b['horizons']['3']['shortlist'],[])
            self.assertEqual(b['horizons']['3']['methods']['majority']['missing'],1)
    def test_shortlisting_requires_strictly_negative_upper_bound(self):
        self.create()
        points=[p for p in frames(self.store,end='2010-09-30') if p.predictable and p.day>='2010-04-01'][:5]
        with patch('back.daily_run.prepare_development',return_value=points):
            for H in (3,7,14):run_horizon(self.store,H=H)
        with patch('back.daily_score.prepare_development',return_value=points):
            for ci,qualifies in (([-.1,-.01],True),([-.1,0.],False),([-.1,.1],False),([.01,.1],False)):
                with patch('back.daily_score.paired_blocks',return_value=dict(ci95=ci,n=5,difference=sum(ci)/2)):
                    report=development_report(self.store)
                part=report['horizons']['3']
                self.assertEqual('ind_logit' in part['shortlist'],qualifies)
                self.assertNotIn('majority',part['shortlist'])
    def test_rerun_rejects_tampered_outcome(self):
        self.create();points=[p for p in frames(self.store,end='2010-09-30') if p.predictable and p.day>='2010-04-01'][:5]
        with patch('back.daily_run.prepare_development',return_value=points):
            run_horizon(self.store,H=3)
            with self.store.transaction():self.store.db.execute("UPDATE d_outcomes SET return_numerator='9999' WHERE H=3")
            with self.assertRaisesRegex(DataError,'existing_result_changed'):run_horizon(self.store,H=3)


class DailyScoreTests(unittest.TestCase):
    def test_twenty_session_blocks_tail_pairing_and_seed(self):
        days=[f'd{i:03}' for i in range(45)]
        base={d:ScoredPoint(d,'up','up',dict(up=1.,flat=0.,down=0.)) for d in days}
        model={d:ScoredPoint(d,'up','flat',dict(up=0.,flat=1.,down=0.)) if i>=40 else base[d] for i,d in enumerate(days)}
        a=paired_blocks(model,base,days);b=paired_blocks(model,base,days)
        self.assertEqual(a,b);self.assertEqual(a['blocks'],3);self.assertEqual(a['repetitions'],2000)
        self.assertAlmostEqual(a['difference'],10/45)
        self.assertEqual(a['ci95'],[0.,2.])
        self.assertNotEqual(a,paired_blocks(model,base,days,seed=7))
        shifted=dict(base);shifted[days[0]]=ScoredPoint(days[0],'down','down',dict(up=0,flat=0,down=1))
        with self.assertRaises(DataError):paired_blocks(model,shifted,days)
    def test_blocks_follow_full_calendar_not_filtered_intersection(self):
        days=[f'd{i:03}' for i in range(61)]
        subset=[days[0],days[21],days[60]]
        points={d:ScoredPoint(d,'up','up',dict(up=1,flat=0,down=0)) for d in subset}
        r=paired_blocks(points,points,days)
        self.assertEqual(r['blocks'],3);self.assertEqual(r['difference'],0);self.assertEqual(r['ci95'],[0,0])

if __name__=='__main__':unittest.main()
