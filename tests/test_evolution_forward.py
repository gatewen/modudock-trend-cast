from dataclasses import replace
import io
import json
import random
import sqlite3
import threading
import unittest
from contextlib import redirect_stderr
from types import SimpleNamespace
from unittest.mock import patch

from back.data import DataError
from back.evolution import LearningPoint
from back.evolution_forward import FrozenVolPrior, frozen_model, progress_metadata, run_forward_vol
from back.evolution_run import ForbiddenClient, run_development
from back.forward import FORWARD_METHODS, LOCKED, enroll, reveal_forward
from back.replay import Replay
from back.report import build_report, markdown_report, status_view
from back.score import ScoredPoint, SplitScores, forward_report, metrics, paired_bootstrap
from scripts.run_forward_vol import main
import tests.test_forward as forward_fixture
import tests.test_evolution as evolution_fixture


class FrozenVolTests(unittest.TestCase):
    def setUp(self):
        self.fixture=evolution_fixture.EvolutionTests()
        self.fixture.setUp()

    def development(self,records):
        return SimpleNamespace(engine=self.fixture.replay,records=records)

    def test_fit_only_matured_eligible_dev_and_includes_last_point(self):
        f=self.fixture
        records=[f.record(i,'down',vol=float(i)) for i in range(1,30)]
        records += [f.record(139,'up',clock='13:00',vol=30), f.record(140,'flat',vol=999),
                    f.record(0,'flat',vol=999), f.record(100,'flat',predictable=False),
                    f.record(101,'flat',scorable=False)]
        model=FrozenVolPrior.fit(self.development(records))
        self.assertEqual(model.majority,(1,0,29))
        self.assertEqual(sum(sum(g) for g in model.groups),30)
        self.assertAlmostEqual(model.cuts[0],1+29/3)
        self.assertAlmostEqual(model.cuts[1],1+58/3)
        rng=random.Random(8)
        changed=[replace(r,observation=replace(r.observation,label=rng.choice(('up','flat','down'))),
                         volatility=rng.random()*99999,jev_choice='down')
                 if r.observation.t.date().isoformat()>=f.plan.hold_start else r for r in records]
        self.assertEqual(model,FrozenVolPrior.fit(self.development(changed)))

    def test_frozen_groups_30_threshold_smoothing_lower_ties_and_fallback(self):
        f=self.fixture
        records=[f.record(i+1,('up','flat','down')[i//30],vol=float(i)) for i in range(90)]
        model=FrozenVolPrior.fit(self.development(records))
        for vol,choice in [(model.cuts[0],'up'),(model.cuts[0]+1e-7,'flat'),
                           (model.cuts[1],'flat'),(model.cuts[1]+1e-7,'down')]:
            with patch('back.evolution_forward.volatility',return_value=vol):
                result=model.predict(f.point)
                self.assertEqual(result.answer,choice)
                self.assertEqual(result.probabilities[choice],31/33)
        with patch('back.evolution_forward.volatility',return_value=None):
            self.assertEqual(model.predict(f.point).probabilities,{'up':1/3,'flat':1/3,'down':1/3})
        model=replace(model,groups=((0,0,29),model.groups[1],model.groups[2]))
        with patch('back.evolution_forward.volatility',return_value=0):
            self.assertEqual(model.predict(f.point).probabilities,{'up':1/3,'flat':1/3,'down':1/3})
        self.assertIsNone(model.predict(replace(f.point,predictable=False)).answer)


class EvolutionForwardTests(unittest.TestCase):
    setUp=forward_fixture.ForwardTests.setUp
    cleanup=forward_fixture.ForwardTests.cleanup
    run_all=forward_fixture.ForwardTests.run_all
    add_day=forward_fixture.ForwardTests.add_day

    def enroll(self):
        with self.store.transaction(): enroll(self.store)

    def stored(self):
        return [tuple(r) for r in self.store.db.execute("SELECT t,answer,probs_json FROM predictions WHERE method='vol_prior' AND substr(t,1,10)>? ORDER BY t",(self.row['hold_end'],))]

    def test_offline_forward_never_reads_forward_truth_or_calls_network_and_no_reveal(self):
        self.enroll()
        original=Replay.outcome
        def dev_only(engine,point):
            self.assertEqual(engine.plan.split_of(point.t.date().isoformat()),'dev')
            return original(engine,point)
        trace=[]; self.store.db.set_trace_callback(trace.append)
        client=ForbiddenClient()
        with patch.object(Replay,'outcome',dev_only):
            result=run_forward_vol(self.store,client=client)
        self.store.db.set_trace_callback(None)
        self.assertEqual(result,dict(points=16,predictable=16,missing_answers=0,http_calls=0))
        self.assertEqual(client.calls,0)
        self.assertEqual(len(self.stored()),16)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM reveals').fetchone()[0],0)
        for sql in trace:
            if sql.startswith('SELECT') and 'FROM outcomes' in sql:
                self.assertIn(self.row['dev_start'],sql)
                self.assertIn(self.row['dev_end'],sql)
                self.assertNotIn(self.row['hold_end'],sql)
        self.assertEqual(build_report(self.store)['forward'],LOCKED)
        before=self.store.db.total_changes
        again=run_forward_vol(self.store,client=ForbiddenClient())
        self.assertEqual(again,result)
        self.assertEqual(self.store.db.total_changes,before)

    def test_hidden_labels_and_predictions_randomized_model_and_answers_unchanged(self):
        self.enroll(); run_forward_vol(self.store)
        model=tuple(self.store.db.execute('SELECT * FROM evolution_models').fetchone())
        before=self.stored()
        with self.store.transaction():
            # Poison labels not used by the offline inference path.
            self.store.db.execute("UPDATE outcomes SET label='PRIVATE' WHERE substr(t,1,10)>?",(self.row['dev_end'],))
            for day in self.future:
                self.store.db.execute("INSERT OR REPLACE INTO outcomes VALUES (1,?,'PRIVATE','PRIVATE','PRIVATE')",(day+'T09:30:00+08:00',))
        run_forward_vol(self.store)
        self.assertEqual(model,tuple(self.store.db.execute('SELECT * FROM evolution_models').fetchone()))
        self.assertEqual(before,self.stored())

    def test_frozen_parameters_cannot_be_replaced_or_refit_to_new_day(self):
        self.enroll(); run_forward_vol(self.store)
        before=tuple(self.store.db.execute('SELECT * FROM evolution_models').fetchone())
        answers=self.stored()
        self.add_day(); self.enroll(); run_forward_vol(self.store)
        self.assertEqual(before,tuple(self.store.db.execute('SELECT * FROM evolution_models').fetchone()))
        self.assertEqual(self.stored()[:len(answers)],answers)
        self.assertEqual(len(self.stored()),24)
        self.store.db.execute("UPDATE evolution_models SET source_digest='changed'");self.store.db.commit()
        with self.assertRaisesRegex(DataError,'evolution_frozen_changed'):
            run_forward_vol(self.store)
        self.store.db.execute('UPDATE evolution_models SET source_digest=?',(before[2],));self.store.db.commit()
        self.store.db.execute("UPDATE evolution_models SET parameters_json='{}'"); self.store.db.commit()
        with self.assertRaisesRegex(DataError,'evolution_frozen_changed'):
            run_forward_vol(self.store)

    def test_missing_or_wrong_vol_answer_blocks_reveal_and_both_comparisons_required(self):
        self.run_all()
        saved=self.store.db.execute("SELECT * FROM predictions WHERE method='vol_prior' LIMIT 1").fetchone()
        self.store.db.execute("DELETE FROM predictions WHERE method='vol_prior' AND t=?",(saved['t'],));self.store.db.commit()
        with self.assertRaisesRegex(DataError,'forward_incomplete'): reveal_forward(self.store)
        run_forward_vol(self.store)
        self.store.db.execute("UPDATE predictions SET answer='up',probs_json=? WHERE method='vol_prior' AND t=?",
            (json.dumps({'up':1,'flat':0,'down':0}),saved['t']));self.store.db.commit()
        with self.assertRaisesRegex(DataError,'forward_incomplete'): reveal_forward(self.store)
        self.store.db.execute('UPDATE predictions SET answer=?,probs_json=? WHERE method=? AND t=?',
            (saved['answer'],saved['probs_json'],'vol_prior',saved['t']));self.store.db.commit()
        reveal_forward(self.store)
        body=build_report(self.store)
        report=body['forward']
        self.assertEqual(set(report['comparisons']),{'jev','vol_prior'})
        self.assertEqual(set(report['methods']),set(FORWARD_METHODS))
        for c in report['comparisons'].values():
            self.assertEqual(c['baseline'],'majority')
            self.assertEqual(c['n'],16)
            self.assertEqual(c['bootstrap']['repetitions'],2000)
            self.assertEqual(c['bootstrap']['seed'],20260927)
        md=markdown_report(body)
        self.assertIn('jev − majority',md);self.assertIn('vol_prior − majority',md)

    def test_locked_status_only_explicit_metadata_and_never_scores(self):
        self.run_all()
        status=status_view(self.store)['forward']
        self.assertEqual(status,dict(LOCKED,participants=list(FORWARD_METHODS),run_points=16))
        def auth(action,table,column,*_):
            if action==sqlite3.SQLITE_READ and (table=='outcomes' or column in ('answer','probs_json','close','label')):
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK
        self.store.db.set_authorizer(auth)
        try: self.assertEqual(progress_metadata(self.store,1)['run_points'],16)
        finally: self.store.db.set_authorizer(None)
        self.store.db.execute("DELETE FROM predictions WHERE method='vol_prior' AND t=(SELECT min(t) FROM predictions WHERE method='vol_prior')");self.store.db.commit()
        self.assertEqual(progress_metadata(self.store,1)['run_points'],15)
        with patch('back.report.forward_report',side_effect=AssertionError('unrevealed score read')):
            self.assertEqual(build_report(self.store)['forward'],LOCKED)

    def test_development_view_is_readonly_and_matches_round2_report(self):
        result=run_development(self.store)
        before=self.store.db.total_changes
        body=build_report(self.store)
        self.assertEqual(body['evolution_dev']['state'],'ready')
        for key,value in result['comparison'].items():
            self.assertEqual(body['evolution_dev'][key],value)
        self.assertIn('開發段勝出＝值得前瞻驗證，不是證明有效',markdown_report(body))
        self.assertEqual(self.store.db.total_changes,before)
        self.assertEqual(body['forward'],LOCKED)

    def test_cancel_rolls_back_new_model_and_predictions_and_other_experiment_refused(self):
        self.enroll()
        event=threading.Event()
        original=FrozenVolPrior.predict
        def stop(model,point):
            result=original(model,point);event.set();return result
        with patch.object(FrozenVolPrior,'predict',stop),self.assertRaisesRegex(DataError,'cancelled'):
            run_forward_vol(self.store,cancel=event)
        self.assertEqual(self.stored(),[])
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM evolution_models').fetchone()[0],0)
        with self.assertRaises(DataError): run_forward_vol(self.store,experiment_id=2)
        for argv in ([],['--execute','--experiment','2']):
            with redirect_stderr(io.StringIO()),self.assertRaises(SystemExit):main(argv)


class ForwardComparisonTests(unittest.TestCase):
    def test_same_six_method_intersection_both_results_report_even_if_opposite(self):
        scored={m:{} for m in FORWARD_METHODS}
        for day,n in [('2024-03-01',1),('2024-03-02',3)]:
            for i in range(n):
                t=f'{day}T{9+i}:30'
                for m in FORWARD_METHODS:
                    p=.9 if m=='vol_prior' else .1 if m=='jev' else .5
                    scored[m][t]=ScoredPoint(t,'flat','flat' if p>=.5 else 'up',{'flat':p,'up':1-p,'down':0})
        common=tuple(scored['jev'])
        data=SplitScores('forward','2024-03-01','2024-03-02',4,4,4,{t:'flat' for t in common},
            scored,{m:metrics(v.values()) for m,v in scored.items()},common,True)
        report=forward_report(data)
        self.assertEqual(report['comparisons']['jev']['statement'],'jev 比 majority 差')
        self.assertEqual(report['comparisons']['vol_prior']['statement'],'vol_prior 比 majority 好')
        for name in ('jev','vol_prior'):
            c=report['comparisons'][name]
            self.assertEqual(c['bootstrap'],paired_bootstrap(scored[name],scored['majority']))
            self.assertEqual(c['n'],4)
        self.assertAlmostEqual(report['comparisons']['vol_prior']['brier_difference'],-.48)
