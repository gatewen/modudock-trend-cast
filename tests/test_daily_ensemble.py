from dataclasses import replace
from fractions import Fraction
import io
import json
from pathlib import Path
import random
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from back.baselines import Prediction
from back.daily_ensemble import COMPONENTS, average_current, current_predictions, development_report
from back.daily_experiment import create_experiment
from back.daily_indicators import frames
from back.daily_models import prediction, WalkForwardDaily, Record
from back.daily_run import run_horizon
from back.daily_store import DailyStore
from back.data import DataError
from back.evolution_run import ForbiddenClient
from back.experiment import canonical
from tests.daily_fixture import seed_daily


class EnsembleTests(unittest.TestCase):
    def test_equal_probability_average_not_vote_or_renormalized_weights(self):
        values = ((.7,.2,.1), (.1,.6,.3), (.3,.2,.5))
        current = {m:prediction(m, dict(zip(('up','flat','down'), v))) for m,v in zip(COMPONENTS,values)}
        p = average_current(current)
        self.assertEqual(p.answer, 'up')
        for label, expected in zip(('up','flat','down'), (1.1/3,1/3,.9/3)):
            self.assertAlmostEqual(p.probabilities[label], expected)
        for m in COMPONENTS:
            with self.assertRaises(DataError): average_current({k:v for k,v in current.items() if k!=m})
        with self.assertRaises(DataError): average_current(dict(current, extra=current['majority']))

    def test_ties_flat_then_up_then_down_and_invalid_probabilities(self):
        for probs, answer in ((dict(up=1/3,flat=1/3,down=1/3),'flat'),
                              (dict(up=.5,flat=0.,down=.5),'up'),
                              (dict(up=0.,flat=0.,down=1.),'down')):
            current = {m:prediction(m, probs) for m in COMPONENTS}
            self.assertEqual(average_current(current).answer, answer)
        for bad in (dict(up=True,flat=0,down=0),dict(up=float('nan'),flat=0,down=1),
                    dict(up=.5,flat=.3,down=.3),dict(up=-.1,flat=.6,down=.5)):
            current = {m:Prediction(m,'up',bad) for m in COMPONENTS}
            with self.assertRaises(DataError): average_current(current)

    def test_future_labels_and_features_do_not_change_walk_forward_ensemble(self):
        # More than 250 matured samples exercises a genuinely fitted logit.
        with DailyStore(':memory:') as s:
            seed_daily(s, 410)
            points = tuple(f for f in frames(s,end='2011-12-31') if f.predictable and f.day>='2010-04-01')
        H = 3
        records = tuple(Record(p,H,points[i+H].day,('up','flat','down')[i%3])
                        for i,p in enumerate(points[:-H]))
        point = points[280]
        rng = random.Random(20260929)
        changed = tuple(replace(r, truth=rng.choice(('up','flat','down')),
                              frame=replace(r.frame, volatility=999., values={k:999. for k in r.frame.values}))
                        if r.end_day>point.day else r for r in records)
        results = []
        for train in (records, changed):
            model = WalkForwardDaily(H,Fraction(1,100),train,point.index)
            predictions,_ = model.predict(point)
            self.assertIsNotNone(model.model)
            results.append(average_current({m:predictions[m] for m in COMPONENTS}))
        self.assertEqual(results[0], results[1])


class EnsembleReportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source=DailyStore(':memory:')
        seed_daily(cls.source,3900)
        create_experiment(cls.source)
        cls.points=tuple(f for f in frames(cls.source,end='2010-09-30') if f.predictable and f.day>='2010-04-01')[:45]
        with patch('back.daily_run.prepare_development',return_value=cls.points):
            for H in (3,7,14): run_horizon(cls.source,H=H)
    @classmethod
    def tearDownClass(cls): cls.source.close()
    def setUp(self):
        self.s=DailyStore(':memory:');self.source.db.backup(self.s.db)
        self.addCleanup(self.s.close)
        self.patcher=patch('back.daily_ensemble.prepare_development',return_value=self.points)
        self.patcher.start();self.addCleanup(self.patcher.stop)

    def test_exact_day_horizon_experiment_no_outcome_access_or_future_rows(self):
        point=self.points[0]
        def authorizer(action,table,*_):
            if action==sqlite3.SQLITE_READ and table!='d_predictions': return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK
        self.s.db.set_authorizer(authorizer)
        before=average_current(current_predictions(self.s,4,3,point))
        self.s.db.set_authorizer(None)
        with self.s.transaction():
            self.s.db.execute("UPDATE d_predictions SET probabilities_json=?,choice='down' WHERE day>? OR H!=3",
                              (canonical(dict(up=0.,flat=0.,down=1.)),point.day))
            self.s.db.execute("UPDATE d_outcomes SET label='down'")
        self.s.db.set_authorizer(authorizer)
        after=average_current(current_predictions(self.s,4,3,point))
        self.s.db.set_authorizer(None)
        self.assertEqual(before,after)
        for H, day, exp in ((3,'2022-01-03',4),(3,'2026-09-29',4),(3,point.day,1),(30,point.day,4)):
            with self.assertRaises(DataError):current_predictions(self.s,exp,H,replace(point,day=day))

    def test_report_offline_same_intersection_and_no_writes(self):
        before=self.s.db.total_changes
        guard=ForbiddenClient()
        with guard.guard(): result=development_report(self.s)
        self.assertEqual(guard.calls,0);self.assertEqual(self.s.db.total_changes,before)
        self.assertEqual(set(result['horizons']),{'3','7','14'})
        for p in result['horizons'].values():
            self.assertEqual((p['n'],p['expected_scorable']),(45,45))
            self.assertTrue(p['complete']);self.assertEqual(p['coverage'],1.)
            self.assertEqual(p['brier_vs_majority']['seed'],20260927)
            self.assertEqual(p['brier_vs_majority']['repetitions'],2000)
            self.assertAlmostEqual(p['brier_vs_majority']['difference'],p['ens_avg']['brier']-p['majority']['brier'])
        for split in ('holdout','forward'):
            with self.assertRaises(DataError): development_report(self.s,split=split)
        with self.assertRaises(DataError): development_report(self.s,1)

    def test_missing_component_or_outcome_stays_incomplete(self):
        with self.s.transaction():
            self.s.db.execute("DELETE FROM d_predictions WHERE method='ind_logit' AND H=3 AND day=?",(self.points[0].day,))
            self.s.db.execute('DELETE FROM d_outcomes WHERE H=7 AND day=?',(self.points[1].day,))
        with patch('back.daily_ensemble.paired_blocks',return_value=dict(ci95=[-.1,-.01],difference=-.05,n=44)):
            result=development_report(self.s)
        for H in ('3','7'):
            p=result['horizons'][H]
            self.assertFalse(p['complete']);self.assertFalse(p['shortlisted'])
            self.assertEqual(p['n'],44);self.assertEqual(p['verdict'],'結果不完整，不下結論')
        self.assertEqual(result['horizons']['3']['missing_components']['ind_logit'],1)
        self.assertEqual(result['horizons']['7']['missing_outcomes'],1)

    def test_strict_negative_ci_and_metadata_validation(self):
        for ci, yes in (([-.2,-.1],True),([-.2,0.],False),([-.2,.1],False),([.1,.2],False)):
            with patch('back.daily_ensemble.paired_blocks',return_value=dict(ci95=ci,difference=sum(ci)/2,n=45)):
                p=development_report(self.s)['horizons']['3']
            self.assertEqual(p['shortlisted'],yes)
        with self.s.transaction():self.s.db.execute("UPDATE d_predictions SET input_hash='wrong' WHERE method='majority'")
        with self.assertRaisesRegex(DataError,'component_mismatch'):development_report(self.s)

    def test_tampered_choice_and_outcome_rejected(self):
        with self.s.transaction():self.s.db.execute("UPDATE d_predictions SET choice='down' WHERE method='majority'")
        with self.assertRaisesRegex(DataError,'component_mismatch'):development_report(self.s)
        self.source.db.backup(self.s.db)
        with self.s.transaction():self.s.db.execute("UPDATE d_outcomes SET label=CASE WHEN label='up' THEN 'down' ELSE 'up' END")
        with self.assertRaisesRegex(DataError,'outcome_mismatch'):development_report(self.s)

    def test_readonly_cli_does_not_create_db_or_call_http(self):
        from scripts.report_ens_avg import main
        with tempfile.TemporaryDirectory() as tmp:
            db=Path(tmp)/'source.sqlite3';output=Path(tmp)/'result.json'
            with DailyStore(db) as target:self.s.db.backup(target.db)
            guard=ForbiddenClient()
            with guard.guard(),patch('sys.stdout',new=io.StringIO()):
                self.assertEqual(main(['--db',str(db),'--report',str(output)]),0)
            self.assertEqual(json.loads(output.read_text())['network_attempts'],0)
            self.assertIn('ens_avg',output.with_suffix('.md').read_text())
            missing=Path(tmp)/'absent.sqlite3'
            with self.assertRaises(sqlite3.OperationalError):main(['--db',str(missing),'--report',str(output)])
            self.assertFalse(missing.exists())


class CampaignBudgetTests(unittest.TestCase):
    def test_new_cap_keeps_existing_used_and_shared_forward_budget(self):
        from back.evolution_budget import consume, LIMIT
        from back.daily_forward_client import ForwardBudget
        from back.http_client import ClientError
        from tests.test_daily_forward import moment
        self.assertEqual(LIMIT,2222)
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'budget.sqlite3'
            budget=ForwardBudget(path=path,now=lambda:moment('2026-09-29T18:00:00'),deadline=lambda _:None)
            try:
                with sqlite3.connect(path) as db:db.execute('UPDATE budget SET used=2220')
                consume(path)
                budget.reserve('2026-09-29')
                self.assertTrue(budget.exhausted())
                with self.assertRaises(ClientError):consume(path)
                with self.assertRaises(ClientError):budget.reserve('2026-09-30')
                with sqlite3.connect(path) as db:self.assertEqual(db.execute('SELECT used FROM budget').fetchone()[0],2222)
            finally:budget.close()
