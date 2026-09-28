from copy import deepcopy
from dataclasses import asdict,replace
from fractions import Fraction
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from back.daily_experiment import create_experiment
from back.daily_hold_model import build,FrozenDaily,METHODS,PRIMARY,POLICY
from back.daily_hold_store import ensure_schema,freeze_models,load_models,install_market,preflight
from back.daily_hold_run import predict_all,prepare_inputs,settle_and_reveal,report,verdict
from back.daily_indicators import frames
from back.daily_models import Logistic,probabilities
from back.daily_replay import DEV_END,HOLD_START,HOLD_END,HORIZONS,DailyReplay
from back.daily_reveal import revealed,holdout_status
from back.daily_run import development_records
from back.daily_store import DailyStore
from back.daily_view import DailyViews
from back.data import DataError
from back.evolution_run import ForbiddenClient
from back.experiment import canonical,exposed_days
from back.market_config import PROTOCOL as MARKET_PROTOCOL
from back.market_features import augment
from back.market_models import MarketLogistic
from back.market_sources import MarketBatch,SOURCES,START
from back.market_store import ensure_schema as market_schema,write_batches,freeze
from back.store import SCHEMA as LEGACY_SCHEMA
from tests.daily_fixture import seed_daily
from tests.test_market import source_rows,weekdays


class HoldoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source=DailyStore(':memory:');cls.source.db.executescript(LEGACY_SCHEMA)
        days=seed_daily(cls.source,3900)
        for table in ('d_calendar','d_bars','d_institutional','d_margin'):
            cls.source.db.executemany(f'DELETE FROM {table} WHERE day=?',((d,) for d in days[25:30]))
        cls.source.db.commit()
        with patch('back.daily_experiment.development_thresholds',return_value={H:dict(k_numerator=1,k_denominator=100) for H in HORIZONS}):
            cls.original=create_experiment(cls.source)
        market_schema(cls.source)
        raw=source_rows(weekdays('2009-01-01',4200))
        batches=[MarketBatch(s,START,DEV_END,tuple(r for r in raw if r['series']==s and r['day']<=DEV_END),'test-'+s) for s in SOURCES]
        write_batches(cls.source,batches);freeze(cls.source,MARKET_PROTOCOL)
        points=tuple(p for p in frames(cls.source,end=DEV_END) if p.predictable and p.day>='2010-04-01')
        points,_=augment(cls.source,points);cls.dev_points=points
        for H in HORIZONS:
            _,records,_=development_records(cls.source,cls.original,points,H)
            # Deliberately sparse fit history distinguishes last saved fit from a new full-development fit.
            for table,model in (('d_fits',Logistic),('market_fits',MarketLogistic)):
                fitted=model.fit(records[:250],points[-1])
                earlier=replace(fitted,fit_day=points[270].day,fit_index=points[270].index)
                cls.source.db.execute(f'INSERT INTO {table} VALUES (4,?,?,?)',(H,earlier.fit_day,canonical(asdict(earlier))))
                cls.source.db.execute(f'INSERT INTO {table} VALUES (4,?,?,?)',(H,points[-1].day,canonical(asdict(fitted))))
        cls.source.db.commit()
        ensure_schema(cls.source);freeze_models(cls.source)
        cls.hold_batches=[MarketBatch(s,HOLD_START,HOLD_END,tuple(r for r in raw if r['series']==s and HOLD_START<=r['day']<=HOLD_END),'held-'+s) for s in SOURCES]
        install_market(cls.source,cls.hold_batches)
        # Use 35 consecutive synthetic holdout dates to keep behavioral mutations fast.
        cls.prepared=prepare_inputs(cls.source)
        frozen,points,alignment=cls.prepared
        cls.prepared=(frozen,points[:35],{p.day:alignment[p.day] for p in points[:35]})
        cls.model_before=canonical(frozen['model'])

    @classmethod
    def tearDownClass(cls):cls.source.close()

    def setUp(self):
        self.s=DailyStore(':memory:');self.source.db.backup(self.s.db);self.s.db.execute('PRAGMA foreign_keys=ON')
        self.addCleanup(self.s.close)
        p=patch('back.daily_hold_run.prepare_inputs',return_value=self.prepared);p.start();self.addCleanup(p.stop)

    def settle(self):
        # Bound outcome() to the end of this synthetic sub-cohort, still strictly inside holdout.
        original=DailyReplay.outcome;end=self.prepared[1][-1].day
        with patch.object(DailyReplay,'outcome',lambda obj,d,H,**kw:original(obj,d,H,end_limit=end)):
            return settle_and_reveal(self.s)

    def test_development_last_fit_and_full_frequency_no_refit(self):
        with patch.object(Logistic,'fit',side_effect=AssertionError('must not refit')),patch.object(MarketLogistic,'fit',side_effect=AssertionError('must not refit')):
            actual=build(self.s)
        self.assertEqual(canonical(actual),self.model_before)
        for H in HORIZONS:
            m=actual['horizons'][str(H)]
            _,records,_=development_records(self.s,self.original,self.dev_points,H)
            self.assertEqual(m['majority'],probabilities(r.truth for r in records))
            self.assertEqual(m['train_n'],len(records));self.assertGreater(len(records),250)
            self.assertEqual(m['ind_logit']['train_n'],250);self.assertEqual(m['mkt_logit']['train_n'],250)
            self.assertEqual(m['ind_logit'],json.loads(self.s.db.execute('SELECT model_json FROM d_fits WHERE H=? ORDER BY day DESC LIMIT 1',(H,)).fetchone()[0]))

    def test_held_labels_do_not_affect_frozen_predictions(self):
        predict_all(self.s)
        before=[tuple(r) for r in self.s.db.execute('SELECT * FROM d_hold_predictions ORDER BY day,H,method')]
        self.settle()
        self.s.db.execute('DROP TRIGGER d_hold_outcomes_no_update')
        self.s.db.execute("UPDATE d_hold_outcomes SET label=CASE WHEN label='up' THEN 'down' ELSE 'up' END");self.s.db.commit()
        def deny(action,table,*args):
            return sqlite3.SQLITE_DENY if action==sqlite3.SQLITE_READ and table=='d_hold_outcomes' else sqlite3.SQLITE_OK
        self.s.db.set_authorizer(deny)
        with ForbiddenClient().guard():result=predict_all(self.s)
        self.s.db.set_authorizer(None)
        self.assertEqual(result['new_predictions'],0)
        self.assertEqual(before,[tuple(r) for r in self.s.db.execute('SELECT * FROM d_hold_predictions ORDER BY day,H,method')])
        self.assertEqual(canonical(load_models(self.s)['model']),self.model_before)
        with self.assertRaisesRegex(DataError,'sealed_results_changed'):report(self.s)

    def test_all_horizons_before_first_outcome_and_reveal(self):
        progress=[]
        with patch.object(DailyReplay,'outcome',side_effect=AssertionError('too early')):
            predict_all(self.s,progress=progress.append)
        self.assertEqual(progress,[dict(stage='predicting'),dict(stage='predictions_sealed')])
        n=len(self.prepared[1]);self.assertEqual(self.s.db.execute('SELECT count(*) FROM d_hold_predictions').fetchone()[0],n*3*24)
        self.assertEqual(self.s.db.execute('SELECT count(*) FROM d_hold_outcomes').fetchone()[0],0)
        self.assertFalse(revealed(self.s));self.assertEqual(holdout_status(self.s)['state'],'unused')
        self.assertTrue(self.settle());self.assertTrue(revealed(self.s))
        self.assertFalse(self.settle());self.assertEqual(holdout_status(self.s)['state'],'used')
        self.assertEqual(self.s.db.execute("SELECT count(*) FROM reveals WHERE namespace='daily' AND experiment_id=4").fetchone()[0],1)
        self.assertFalse(self.s.db.execute('PRAGMA foreign_key_check').fetchall())

    def test_missing_any_method_any_horizon_blocks_all_outcomes(self):
        with patch.object(DailyReplay,'outcome',side_effect=AssertionError('too early')):
            with self.assertRaises(DataError):settle_and_reveal(self.s)
        predict_all(self.s)
        self.s.db.execute('DROP TRIGGER d_hold_predictions_no_delete')
        self.s.db.execute("DELETE FROM d_hold_predictions WHERE H=14 AND method='ind_sox_ret1' AND day=?",(self.prepared[1][0].day,));self.s.db.commit()
        with patch.object(DailyReplay,'outcome',side_effect=AssertionError('too early')):
            with self.assertRaises(DataError):settle_and_reveal(self.s)
        self.assertFalse(revealed(self.s));self.assertEqual(self.s.db.execute('SELECT count(*) FROM d_hold_outcomes').fetchone()[0],0)

    def test_completion_seal_required_even_when_predictions_are_complete(self):
        predict_all(self.s)
        self.s.db.execute('DROP TRIGGER d_hold_completion_no_delete')
        self.s.db.execute('DELETE FROM d_hold_completion');self.s.db.commit()
        with patch.object(DailyReplay,'outcome',side_effect=AssertionError('too early')):
            with self.assertRaisesRegex(DataError,'predictions_incomplete'):settle_and_reveal(self.s)

    def test_locked_report_cli_and_existing_views_never_read_held_results(self):
        from scripts.run_daily_holdout import main
        predict_all(self.s)
        def deny(action,table,*args):
            return sqlite3.SQLITE_DENY if action==sqlite3.SQLITE_READ and table in ('d_hold_features','d_hold_predictions','d_hold_outcomes') else sqlite3.SQLITE_OK
        self.s.db.set_authorizer(deny)
        with self.assertRaisesRegex(DataError,'daily_holdout_locked'):report(self.s)
        views=DailyViews()
        self.assertEqual(views.handle(self.s,dict(op='daily_holdout',H=3)),
            dict(status='ok',experiment_id=4,H=3,split='holdout',state='locked'))
        for op,kw in (('daily_status',{}),('daily_chart',{'range':'all'}),('daily_report',{}),('daily_indicators',{'date':self.dev_points[-1].day})):
            result=views.handle(self.s,dict(op=op,H=3,**kw));self.assertEqual(result.get('split'),'dev')
            self.assertNotIn('holdout_prediction',canonical(result))
        self.s.db.set_authorizer(None)
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'test.sqlite3';output=Path(tmp)/'secret.json'
            with sqlite3.connect(path) as db:self.s.db.backup(db)
            with patch('sys.stdout',new=io.StringIO()) as stdout,ForbiddenClient().guard():
                with self.assertRaisesRegex(DataError,'daily_holdout_locked'):main(['--report-only','--db',str(path),'--report',str(output)])
                self.assertEqual(stdout.getvalue(),'')
            self.assertFalse(output.exists());self.assertFalse(output.with_suffix('.md').exists())

    def test_atomic_reveal_failure_rolls_back_outcomes(self):
        predict_all(self.s)
        self.s.db.execute("CREATE TRIGGER fail_reveal BEFORE INSERT ON reveals BEGIN SELECT RAISE(ABORT,'fixture'); END")
        with self.assertRaises(sqlite3.IntegrityError):self.settle()
        self.assertFalse(revealed(self.s));self.assertEqual(self.s.db.execute('SELECT count(*) FROM d_hold_outcomes').fetchone()[0],0)
        self.assertEqual(self.s.db.execute('SELECT count(*) FROM d_hold_seal').fetchone()[0],0)

    def test_ensemble_is_frozen_current_mean_and_models_dont_mutate(self):
        frozen,points,_=self.prepared;predictor=FrozenDaily(frozen['model'])
        for point in (points[0],points[-1]):
            for H in HORIZONS:
                p,_=predictor.predict(point,H)
                self.assertEqual(set(p),set(METHODS))
                for label in ('up','flat','down'):
                    self.assertAlmostEqual(p['ens_avg'].probabilities[label],sum(p[m].probabilities[label] for m in ('majority','vol_prior_d','ind_logit'))/3)
                self.assertEqual(p['majority'].probabilities,frozen['model']['horizons'][str(H)]['majority'])
        self.assertEqual(canonical(predictor.bundle),self.model_before)
        for day in ('2021-12-31','2024-07-26'):
            with self.assertRaises(DataError):predictor.predict(replace(points[0],day=day),3)

    def test_model_source_results_immutable_and_exposure_permanent(self):
        predict_all(self.s);self.settle()
        for sql in ("UPDATE d_hold_models SET model_hash='x'",'DELETE FROM d_hold_predictions',
                    'INSERT OR REPLACE INTO d_hold_models SELECT * FROM d_hold_models',
                    'INSERT OR REPLACE INTO d_hold_outcomes SELECT * FROM d_hold_outcomes',
                    "UPDATE d_hold_market_rows SET close='1'",'DELETE FROM d_hold_market_rows',
                    "DELETE FROM reveals WHERE namespace='daily'", "UPDATE reveals SET what='none' WHERE namespace='daily'"):
            with self.assertRaises(sqlite3.IntegrityError):self.s.db.execute(sql)
            self.s.db.rollback()
        self.assertEqual(exposed_days(self.s,'2330',[HOLD_START]),(HOLD_START,))
        with self.assertRaises(DataError):preflight(self.s)
        with self.assertRaises(sqlite3.IntegrityError):self.s.db.execute("INSERT INTO reveals VALUES (999,'now','2022-01-03','2024-07-25','all','holdout','intraday')")

    def test_four_verdicts_and_exactly_three_comparisons(self):
        for ci,expected in (([-.2,-.1],'ens_avg 比 majority 好'),([.1,.2],'ens_avg 比 majority 差'),
                            ([-.1,.1],'沒有證據顯示 ens_avg 比簡單方法好')):
            c=dict(difference=sum(ci)/2,ci95=ci,n=100)
            self.assertEqual(verdict('ens_avg',c,100),expected)
            self.assertEqual(verdict('ens_avg',c,100,False),'結果不完整，不下結論')
            self.assertEqual(verdict('ens_avg',dict(c,n=94),100),'結果不完整，不下結論')
        self.assertEqual(PRIMARY,((3,'ens_avg'),(7,'ens_avg'),(3,'mkt_logit')))
        self.assertEqual(POLICY['expected_lucky'],.075)

    def test_complete_report_only_three_primary_all_others_descriptive(self):
        with patch('back.daily_hold_run.prepare_inputs',new=prepare_inputs),ForbiddenClient().guard():
            predict_all(self.s);settle_and_reveal(self.s)
            result=report(self.s)
        self.assertEqual({(p['H'],p['method']) for p in result['primary_comparisons']},set(PRIMARY))
        self.assertEqual(result['multiplicity']['comparisons'],3)
        self.assertEqual(result['multiplicity']['expected_lucky'],.075)
        self.assertTrue(all(c['complete'] for c in result['primary_comparisons']))
        for c in result['primary_comparisons']:
            self.assertEqual(c['repetitions'],2000);self.assertEqual(c['seed'],20260927);self.assertEqual(c['block_sessions'],20)
        for H in HORIZONS:
            rows=result['descriptive'][str(H)];self.assertEqual(set(rows),set(METHODS))
            for r in rows.values():
                self.assertEqual(r['n'],result['n_days']-H);self.assertEqual(r['coverage'],1.)
                self.assertNotIn('verdict',r);self.assertNotIn('brier_vs_majority',r)
        views=DailyViews();self.assertEqual(views.handle(self.s,dict(op='daily_status',H=3))['holdout']['state'],'used')
        changes=self.s.db.total_changes
        for H in HORIZONS:
            projected=views.handle(self.s,dict(op='daily_holdout',H=H))
            self.assertEqual(projected['state'],'used');self.assertEqual(projected['split'],'holdout')
            self.assertEqual(projected['primary_comparisons'],result['primary_comparisons'])
            self.assertEqual({r['method']: {k:v for k,v in r.items() if k!='method'} for r in projected['descriptive']},result['descriptive'][str(H)])
        self.assertEqual(changes,self.s.db.total_changes)
        # A warmed UI must revalidate the seal and reveal, not serve a cached score.
        self.s.db.execute('DROP TRIGGER d_hold_outcomes_no_update')
        self.s.db.execute("UPDATE d_hold_outcomes SET label='up'");self.s.db.commit()
        with self.assertRaisesRegex(DataError,'sealed_results_changed'):
            views.handle(self.s,dict(op='daily_holdout',H=3))
        self.s.db.execute('DROP TRIGGER reveals_daily_no_delete')
        self.s.db.execute("DELETE FROM reveals WHERE namespace='daily'");self.s.db.commit()
        self.assertEqual(views.handle(self.s,dict(op='daily_holdout',H=3))['state'],'locked')

    def test_real_bounded_input_reads_exclude_exposed_suffix(self):
        # Unpatched entry point, complete synthetic holdout prefix; no outcomes are touched.
        # Function captured in setUpClass before per-test patches.
        before=self.__class__.prepared
        self.s.db.execute("UPDATE d_bars SET close='999999' WHERE day>'2024-07-25'");self.s.db.commit()
        with patch('back.daily_hold_run.prepare_inputs',new=prepare_inputs),patch.object(DailyReplay,'outcome',side_effect=AssertionError('too early')):
            actual=prepare_inputs(self.s)
        self.assertEqual([p.digest for p in before[1]],[p.digest for p in actual[1][:35]])
        self.assertTrue(all(HOLD_START<=p.day<=HOLD_END for p in actual[1]))


if __name__=='__main__':unittest.main()
