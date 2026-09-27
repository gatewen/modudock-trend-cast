from contextlib import redirect_stderr, redirect_stdout
from dataclasses import replace
import io
import json
from pathlib import Path
import socket
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from back.data import DataError
from back.evolution import METHODS, WalkForward
from back.evolution_run import (ALL_METHODS, ForbiddenClient, comparison_report,
                                read_development, run_development)
from back.jevcast import JevClient
from back.replay import Replay
from back.score import INCOMPLETE, ScoredPoint, paired_bootstrap
from back.store import Store
from scripts.run_evolution import main
from tests.score_fixture import scored_fixture


class EvolutionRunTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.base = Store(':memory:')
        cls.row, cls.days = scored_fixture(cls.base, populate_holdout=True)

    @classmethod
    def tearDownClass(cls):
        cls.base.close()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)/'test.sqlite3'
        self.store = Store(self.path)
        self.base.db.backup(self.store.db)
        self.addCleanup(self.store.close)

    def test_network_forbidden_at_client_and_transport_layers(self):
        def connect_socket():
            with socket.socket() as connection:
                connection.connect(('127.0.0.1',1))
        for invoke in (lambda: JevClient().predict(None), lambda: JevClient()._request(b'', ''),
                       lambda: socket.create_connection(('unused.invalid',443)),
                       lambda: socket.getaddrinfo('unused.invalid',443),
                       connect_socket):
            client = ForbiddenClient()
            with self.assertRaisesRegex(DataError, 'network_forbidden'), client.guard():
                invoke()
            self.assertEqual(client.calls,1)

    def test_network_attempt_during_work_is_blocked_and_rolls_back(self):
        client=ForbiddenClient()
        with patch.object(WalkForward,'predict',side_effect=lambda *a,**k: JevClient().predict(None)):
            with self.assertRaisesRegex(DataError,'network_forbidden'):
                run_development(self.store,client=client)
        self.assertEqual(client.calls,1)
        self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM predictions WHERE method='clock_prior'").fetchone()[0],0)

    def test_atomic_only_dev_three_methods_zero_http_and_rerun_no_changes(self):
        old = {table: [tuple(r) for r in self.store.db.execute(f'SELECT * FROM {table} ORDER BY rowid')]
               for table in ('predictions','outcomes','reveals','forward_days','experiments')}
        trace = []
        self.store.db.set_trace_callback(trace.append)
        client = ForbiddenClient()
        original = Replay.prepare
        def dev_prepare(engine,t):
            self.assertEqual(engine.plan.split_of(t.date().isoformat()),'dev')
            return original(engine,t)
        forbidden_reads = {'reveals','forward_days','forward_attempts','forward_exclusions','prior_exposures','reveal_context'}
        def guard(action, table, column, database, trigger):
            if action == sqlite3.SQLITE_READ and table in forbidden_reads:
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK
        self.store.db.set_authorizer(guard)
        with patch.object(Replay,'prepare',dev_prepare):
            report = run_development(self.store,client=client)
        self.store.db.set_authorizer(None)
        self.store.db.set_trace_callback(None)
        self.assertEqual(client.calls,0)
        self.assertEqual(report['http_calls'],0)
        self.assertEqual(report['new_predictions'],{m:32 for m in METHODS})
        self.assertEqual(report['missing_predictions'],{m:0 for m in METHODS})
        self.assertEqual(report['comparison']['n'],32)
        self.assertEqual(report['comparison']['coverage'],1)
        for query in trace:
            for table in ('daily','bars','corp_events','outcomes','predictions'):
                if query.startswith('SELECT') and f'FROM {table} ' in query:
                    self.assertNotIn(self.row['hold_end'],query)
                    self.assertNotIn(self.row['hold_start'],query)
        for table in old:
            if table == 'predictions':
                current = [tuple(r) for r in self.store.db.execute(
                    "SELECT * FROM predictions WHERE method NOT IN ('clock_prior','vol_prior','jev_calibrated') ORDER BY rowid")]
            else:
                current = [tuple(r) for r in self.store.db.execute(f'SELECT * FROM {table} ORDER BY rowid')]
            self.assertEqual(old[table],current,table)
        rows = self.store.db.execute("SELECT method,split,status,n_ok FROM runs WHERE method IN ('clock_prior','vol_prior','jev_calibrated')").fetchall()
        self.assertEqual({tuple(r) for r in rows},{(m,'dev','complete',32) for m in METHODS})
        total = self.store.db.total_changes
        again = run_development(self.store,client=ForbiddenClient())
        self.assertEqual(again['new_predictions'],{m:0 for m in METHODS})
        self.assertEqual(self.store.db.total_changes,total)
        self.assertEqual(again['comparison'],report['comparison'])
        self.assertEqual(again['source_digest'],report['source_digest'])
        for marker in ('holdout','forward',self.row['hold_start'],self.row['hold_end']):
            self.assertNotIn(marker,json.dumps(report))

    def test_non_dev_or_other_experiment_refused_before_any_sql(self):
        for kwargs in ({'split':'holdout'},{'split':'forward'},{'experiment_id':2},{'experiment_id':True}):
            with self.assertRaisesRegex(DataError,'evolution_dev_only'):
                read_development(None,**kwargs)

    def test_settings_must_be_selected_p1(self):
        self.store.db.execute("UPDATE experiments SET prompt_version='p2' WHERE id=1")
        self.store.db.commit()
        with self.assertRaisesRegex(DataError,'evolution_settings_changed'):
            run_development(self.store)

    def test_hidden_data_changes_cannot_affect_any_report_field(self):
        before=run_development(self.store)
        with self.store.transaction():
            self.store.db.execute("UPDATE predictions SET answer='PRIVATE',probs_json='NOT_JSON' WHERE substr(t,1,10)>?",(self.row['dev_end'],))
            self.store.db.execute("UPDATE outcomes SET label='PRIVATE',close_end='BAD' WHERE substr(t,1,10)>?",(self.row['dev_end'],))
            self.store.db.execute("UPDATE bars SET close='BAD' WHERE day>?",(self.row['dev_end'],))
            self.store.db.execute("UPDATE daily SET close='BAD' WHERE day>?",(self.row['dev_end'],))
        after=run_development(self.store)
        before.pop('new_predictions'); after.pop('new_predictions')
        self.assertEqual(before,after)

    def test_missing_current_jev_never_requests_repair_or_invents_calibration(self):
        self.store.db.execute("DELETE FROM predictions WHERE method='jev' AND substr(t,1,10)<=?",(self.row['dev_end'],))
        self.store.db.commit()
        client = ForbiddenClient()
        report=run_development(self.store,client=client)
        self.assertEqual(client.calls,0)
        self.assertEqual(report['new_predictions']['jev_calibrated'],0)
        self.assertEqual(report['missing_predictions']['jev_calibrated'],32)
        self.assertEqual(report['comparison']['n'],0)
        self.assertIsNone(report['comparison']['promoted'])
        self.assertTrue(all(c['statement']==INCOMPLETE for c in report['comparison']['comparisons'].values()))

    def test_corrupt_sources_and_existing_predictions_refuse_without_overwriting(self):
        original = self.store.db.execute("SELECT t FROM outcomes WHERE substr(t,1,10)<=? ORDER BY t LIMIT 1",(self.row['dev_end'],)).fetchone()[0]
        self.store.db.execute("UPDATE outcomes SET label='up' WHERE t=?",(original,)); self.store.db.commit()
        with self.assertRaisesRegex(DataError,'development_outcome_mismatch'):
            run_development(self.store)
        self.store.db.execute("UPDATE outcomes SET label='flat' WHERE t=?",(original,)); self.store.db.commit()
        run_development(self.store)
        self.store.db.execute("UPDATE predictions SET answer='down' WHERE method='clock_prior' AND t=?",(original,)); self.store.db.commit()
        count=self.store.db.total_changes
        with self.assertRaisesRegex(DataError,'evolution_prediction_conflict'):
            run_development(self.store)
        self.assertEqual(self.store.db.total_changes,count)

    def test_mid_write_failure_rolls_back_all_new_methods_and_runs(self):
        self.store.db.execute("""CREATE TEMP TRIGGER fail_new BEFORE INSERT ON predictions
            WHEN NEW.method='vol_prior' BEGIN SELECT RAISE(ABORT,'fixture'); END""")
        runs=self.store.db.execute('SELECT COUNT(*) FROM runs').fetchone()[0]
        with self.assertRaises(sqlite3.IntegrityError):
            run_development(self.store)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM runs').fetchone()[0],runs)
        self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM predictions WHERE method IN ('clock_prior','vol_prior','jev_calibrated')").fetchone()[0],0)

    def test_incomplete_source_run_suppresses_promotion(self):
        self.store.db.execute("UPDATE runs SET status='running' WHERE method='majority' AND split='dev'")
        self.store.db.commit()
        result=run_development(self.store)['comparison']
        self.assertTrue(all(c['statement']==INCOMPLETE for c in result['comparisons'].values()))
        self.assertIsNone(result['promoted'])

    def test_future_fixture_outcomes_and_jev_randomized_no_early_change(self):
        # Reader validates the real point/input association; the learner is then
        # challenged with independently randomized future truths and forecasts.
        import random
        with self.store.transaction():
            data=read_development(self.store)
        point=data.points[20][0]
        learner=WalkForward(data.engine,data.records)
        before={m:learner.predict(m,point,jev_choice='flat') for m in METHODS}
        rng=random.Random(19)
        for _ in range(5):
            records=[replace(r,observation=replace(r.observation,label=rng.choice(('up','flat','down'))),
                             jev_choice=rng.choice(('up','flat','down')),volatility=rng.random())
                     if r.observation.t>=point.t else r for r in data.records]
            for m in METHODS:
                self.assertEqual(WalkForward(data.engine,records).predict(m,point,jev_choice='flat'),before[m])

    def test_cli_requires_execute_and_refuses_hidden_splits_and_db_output(self):
        for argv in ([],['--execute','--split','holdout'],['--execute','--split','forward'],
                     ['--execute','--experiment','2'],['--execute','--db',str(self.path),'--output',str(self.path)]):
            with redirect_stderr(io.StringIO()), patch('scripts.run_evolution.run_development') as run:
                with self.assertRaises(SystemExit):
                    main(argv)
                run.assert_not_called()
        output=Path(self.temp.name)/'dev.json'
        with redirect_stdout(io.StringIO()):
            self.assertEqual(main(['--execute','--db',str(self.path),'--output',str(output)]),0)
        self.assertEqual(json.loads(output.read_text())['http_calls'],0)


class EvolutionComparisonTests(unittest.TestCase):
    def scored(self,n=40):
        def point(i,method):
            t=f'2024-02-{i//8+1:02d}T{9+i%8//2:02d}:{30 if i%2 else 0:02d}'
            p={'majority':.5,'clock_prior':.7,'vol_prior':.8,'jev_calibrated':.6}.get(method,.5)
            return t,ScoredPoint(t,'flat','flat',{'up':(1-p)/2,'flat':p,'down':(1-p)/2})
        return {m:dict(point(i,m) for i in range(n)) for m in ALL_METHODS}

    def test_shared_eight_method_intersection_delta_direction_and_existing_bootstrap(self):
        scored=self.scored()
        removed=next(iter(scored['reversal']))
        del scored['reversal'][removed]
        report=comparison_report(scored,40)
        self.assertEqual(report['n'],39)
        self.assertEqual({v['n'] for v in report['methods'].values()},{39})
        method='clock_prior'
        common=set(scored[method])-{removed}
        expected=paired_bootstrap({t:scored[method][t] for t in common},{t:scored['majority'][t] for t in common})
        self.assertEqual(report['comparisons'][method]['bootstrap'],expected)
        self.assertAlmostEqual(report['comparisons'][method]['brier_difference'],.135-.375)
        self.assertEqual(expected['repetitions'],2000)
        self.assertEqual(expected['seed'],20260927)
        self.assertEqual(expected['unit'],'trading_day')
        self.assertEqual(report['promoted'],'vol_prior')

    def test_significance_required_touching_zero_none_and_brier_not_accuracy_selection(self):
        scored=self.scored()
        result=comparison_report(scored,40)
        self.assertEqual(result['promoted'],'vol_prior')
        self.assertTrue(all(c['statement']=='值得前瞻驗證' for c in result['comparisons'].values()))
        for method in METHODS:
            scored[method]=dict(scored['majority'])
        result=comparison_report(scored,40)
        self.assertIsNone(result['promoted'])
        self.assertTrue(all(c['statement']=='沒有改善' for c in result['comparisons'].values()))
        scored=self.scored()
        # Lowest Brier can fail significance; only qualifying methods compete.
        with patch('back.evolution_run.paired_bootstrap',side_effect=[
                {'brier_difference_ci95':[-.3,-.1]}, {'brier_difference_ci95':[-.4,.01]},
                {'brier_difference_ci95':[-.2,-.01]}]):
            self.assertEqual(comparison_report(scored,40)['promoted'],'clock_prior')

    def test_under_95_or_incomplete_no_conclusion_exact_95_allowed(self):
        for missing in (2,3):
            scored=self.scored()
            for t in list(scored['jev'])[:missing]:
                del scored['jev'][t]
            result=comparison_report(scored,40)
            if missing==2:
                self.assertEqual(result['promoted'],'vol_prior')
            else:
                self.assertIsNone(result['promoted'])
                self.assertTrue(all(c['statement']==INCOMPLETE for c in result['comparisons'].values()))
        result=comparison_report(self.scored(),40,complete=False)
        self.assertIsNone(result['promoted'])
        result=comparison_report({m:{} for m in ALL_METHODS},0)
        self.assertIsNone(result['promoted'])


if __name__ == '__main__':
    unittest.main()
