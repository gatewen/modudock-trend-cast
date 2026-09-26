from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stderr, redirect_stdout
import io
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from back.data import DataError
from back.experiment import load_plan
from back.http_client import ClientError
from back.jevcast import _AUTH_DISABLED
from back.replay import Replay
from back.store import Store
from scripts.run_dev import (CallBudget, DevClient, exclusive_run, main,
                             prepare_development, raw_report, run_development)
from tests.experiment_fixture import frozen_experiment, seed_experiment
from tests.helpers import FakeClock, FakeServer, KEY
from tests.replay_fixture import at
from tests.test_jev_client import response


class RunDevTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = FakeServer()

    @classmethod
    def tearDownClass(cls):
        cls.server.close()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'dev.sqlite3'
        self.store = Store(self.path)
        self.addCleanup(self.store.close)
        self.days = seed_experiment(self.store)
        self.server.reset()
        self.env = patch.dict(os.environ, {'TYPESAFE_API_KEY': KEY})
        self.env.start()
        self.addCleanup(self.env.stop)
        _AUTH_DISABLED.clear()
        self.addCleanup(_AUTH_DISABLED.clear)

    def freeze(self):
        self.row = frozen_experiment(self.store, self.days, end=self.days[28])
        return self.row['id']

    def client(self, limit=3500):
        return DevClient(CallBudget(limit), opener=self.server, sleep=lambda _: None)

    def queue(self, count=32):
        for _ in range(count):
            self.server.queue(response())

    def test_cli_without_execute_no_request_no_writer_no_changes(self):
        self.freeze()
        for argv in (["--db", str(self.path)], ['--execute', '--split', 'holdout'],
                     ['--execute', '--max-calls', '-1']):
            with self.subTest(argv=argv), redirect_stderr(io.StringIO()), \
                 patch('scripts.run_dev.run_development') as execute:
                with self.assertRaises(SystemExit) as error:
                    main(argv)
                self.assertEqual(error.exception.code, 2)
                execute.assert_not_called()
        self.assertEqual(self.server.requests, [])
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM runs').fetchone()[0], 0)

    def test_cli_explicit_execution_defaults_to_3500_calls(self):
        with patch('scripts.run_dev.run_development', return_value={'complete': True}) as execute, \
             redirect_stdout(io.StringIO()):
            self.assertEqual(main(['--execute', '--db', str(self.path)]), 0)
        self.assertEqual(execute.call_args.kwargs['max_calls'], 3500)
        self.assertNotIn('split', execute.call_args.kwargs)

    def test_cli_selected_p2_experiment_routes_prompt_and_checks_reference_before_http(self):
        p1 = self.freeze()
        prepare_development(self.store, p1)
        p2 = frozen_experiment(self.store, self.days, end=self.days[28], prompt_version='p2')['id']
        self.queue()
        output = io.StringIO()
        with patch('scripts.run_dev.DevClient', return_value=self.client()), \
             redirect_stdout(output), redirect_stderr(io.StringIO()):
            self.assertEqual(main(['--execute', '--db', str(self.path), '--experiment', str(p2),
                                  '--reference-experiment', str(p1)]), 0)
        result = json.loads(output.getvalue())
        self.assertEqual(result['experiment_id'], p2)
        self.assertEqual(result['baseline_reference']['experiment_id'], p1)
        self.assertTrue(all(v == {'n': 16, 'identical': True} for v in result['baseline_reference']['methods'].values()))
        self.assertEqual(len(self.server.bodies), 16)
        for body in self.server.bodies:
            self.assertIn('51% 屬於 flat', json.loads(body)['questions']['direction']['instructions'])
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM predictions WHERE experiment_id=? AND method='jev'", (p1,)).fetchone()[0], 0)
        self.assertEqual(set(r[0] for r in self.store.db.execute('SELECT DISTINCT split FROM runs')), {'dev'})
        again = run_development(self.path, experiment_id=p2, reference_experiment=p1, client=self.client())
        self.assertEqual(again['jev']['http_calls'], 0)

    def test_reference_baseline_mismatch_stops_before_any_http(self):
        p1 = self.freeze(); prepare_development(self.store, p1)
        p2 = frozen_experiment(self.store, self.days, end=self.days[28], prompt_version='p2')['id']
        self.store.db.execute("UPDATE predictions SET answer='down' WHERE experiment_id=? AND method='majority'", (p1,))
        self.store.db.commit()
        with self.assertRaisesRegex(DataError, '^baseline_reference_mismatch$'):
            run_development(self.path, experiment_id=p2, reference_experiment=p1, client=self.client())
        self.assertEqual(self.server.requests, [])

    def test_reference_split_mismatch_stops_before_preparation_or_http(self):
        p1 = self.freeze()
        p2 = frozen_experiment(self.store, self.days, end=self.days[29], prompt_version='p2')['id']
        with self.assertRaisesRegex(DataError, '^reference_settings_mismatch$'):
            run_development(self.path, experiment_id=p2, reference_experiment=p1, client=self.client())
        self.assertEqual(self.server.requests, [])
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM outcomes').fetchone()[0], 0)

    def test_only_dev_methods_outcomes_and_report_and_restart_zero_http(self):
        experiment = self.freeze()
        # Synthetic holdout sentinels must survive byte-for-byte, never consulted
        # for predictions/reporting. No real holdout data is used by this test.
        hold_t = at(self.row['hold_start']).isoformat()
        with self.store.transaction():
            cursor = self.store.db.execute('''INSERT INTO runs
                (experiment_id,method,split,started_at,status) VALUES (?,'jev','holdout','fixture','complete')''',
                (experiment,))
            self.store.db.execute('INSERT INTO predictions VALUES (?,?,?,?,?,?,?,?,?,?)',
                (experiment, 'jev', hold_t, cursor.lastrowid, 'hold-hash', 'PRIVATE-HOLDOUT',
                 'down', '{"down":1}', None, 'fixture'))
            self.store.db.execute('INSERT INTO outcomes VALUES (?,?,?,?,?)',
                                  (experiment, hold_t, '987654', '876543', 'down'))
        hidden = {table: [tuple(r) for r in self.store.db.execute(
            f'SELECT * FROM {table} WHERE t=?', (hold_t,))] for table in ('predictions', 'outcomes')}
        self.queue()
        original_prepare, original_outcome = Replay.prepare, Replay.outcome
        visits = []
        def guarded_prepare(replay, t):
            self.assertEqual(replay.plan.split_of(t.date().isoformat()), 'dev')
            visits.append(t)
            return original_prepare(replay, t)
        def guarded_outcome(replay, point):
            self.assertEqual(replay.plan.split_of(point.t.date().isoformat()), 'dev')
            return original_outcome(replay, point)
        with patch.object(Replay, 'prepare', guarded_prepare), patch.object(Replay, 'outcome', guarded_outcome):
            result = run_development(self.path, experiment_id=experiment, client=self.client())
        self.assertTrue(result['complete'])
        self.assertEqual(result['split'], 'dev')
        self.assertEqual(result['predictable_and_scorable'], 16)
        self.assertEqual(result['jev']['http_calls'], 16)
        self.assertEqual(result['jev']['committed_this_run'], 16)
        self.assertEqual(result['jev']['failed_this_run'], 0)
        self.assertEqual(result['jev']['retries'], 0)
        self.assertEqual(result['jev']['choice_counts'], {'up': 0, 'flat': 16, 'down': 0})
        self.assertEqual(result['baseline_new_predictions'],
                         {m: 16 for m in ('always_flat', 'majority', 'momentum', 'reversal')})
        scopes = dict(self.store.db.execute('''SELECT r.method,s.full_split FROM runs r
            JOIN run_scopes s ON s.run_id=r.id WHERE r.split='dev' '''))
        self.assertEqual(scopes, {m: 1 for m in ('jev', 'always_flat', 'majority', 'momentum', 'reversal')})
        for method, metrics in result['all_methods_intersection'].items():
            self.assertEqual(metrics, {'n': 16, 'correct': 16, 'accuracy': 1.0}, method)
        self.assertGreater(len(visits), 0)
        for table in hidden:
            self.assertEqual([tuple(r) for r in self.store.db.execute(
                f'SELECT * FROM {table} WHERE t=?', (hold_t,))], hidden[table])
        report = json.dumps(result)
        for forbidden in ('PRIVATE-HOLDOUT', '987654', '876543', 'holdout', KEY, self.row['hold_start']):
            self.assertNotIn(forbidden, report)
        before = {table: [tuple(r) for r in self.store.db.execute(f'SELECT * FROM {table} ORDER BY t')]
                  for table in ('predictions', 'outcomes')}
        self.server.reset()
        with patch.dict(os.environ, {'TYPESAFE_API_KEY': ''}):
            again = run_development(self.path, experiment_id=experiment, client=self.client())
        self.assertTrue(again['complete'])
        self.assertEqual(again['jev']['http_calls'], 0)
        self.assertEqual(again['jev']['existing_skipped'], 16)
        self.assertEqual(again['jev']['committed_this_run'], 0)
        self.assertEqual(sum(again['baseline_new_predictions'].values()), 0)
        self.assertEqual(self.server.requests, [])
        for table in before:
            self.assertEqual([tuple(r) for r in self.store.db.execute(f'SELECT * FROM {table} ORDER BY t')], before[table])

    def test_max_calls_caps_six_workers_and_drains_successful_requests(self):
        experiment = self.freeze()
        self.queue()
        result = run_development(self.path, experiment_id=experiment, max_calls=3, client=self.client(3))
        self.assertEqual(len(self.server.requests), 3)
        self.assertEqual(result['jev']['http_calls'], 3)
        self.assertEqual(result['jev']['committed_this_run'], 3)
        self.assertEqual(result['jev']['run_status'], 'call_limit')
        self.assertEqual(result['jev']['missing_predictable_points'], 13)
        self.assertFalse(result['complete'])
        self.assertEqual({m['n'] for m in result['all_methods_intersection'].values()}, {3})
        self.server.reset()
        self.queue()
        resumed = run_development(self.path, experiment_id=experiment, client=self.client())
        self.assertEqual(resumed['jev']['http_calls'], 13)
        self.assertEqual(resumed['jev']['existing_skipped'], 3)
        self.assertTrue(resumed['complete'])

    def test_zero_max_calls_preserves_local_work_without_http(self):
        experiment = self.freeze()
        result = run_development(self.path, experiment_id=experiment, max_calls=0, client=self.client(0))
        self.assertEqual(self.server.requests, [])
        self.assertEqual(result['jev']['http_calls'], 0)
        self.assertEqual(result['all_methods_intersection']['jev'], {'n': 0, 'correct': 0, 'accuracy': None})
        self.assertEqual(result['baseline_new_predictions']['majority'], 16)
        self.assertFalse(result['complete'])

    def test_retries_consume_quota_and_terminal_failures_are_distinct(self):
        experiment = self.freeze()
        point = Replay(self.store, load_plan(self.store, experiment)).prepare(at(self.days[25]))
        client = self.client(2)
        self.server.queue(status=429)
        self.server.queue(status=529)
        self.server.queue(response())
        with self.assertRaisesRegex(ClientError, 'call_limit'):
            client.predict(point)
        self.assertEqual(len(self.server.requests), 2)
        self.assertEqual((client.budget.calls, client.budget.retries), (2, 1))
        self.assertEqual(dict(client.errors), {'call_limit': 1})
        self.assertEqual(client.quota_blocked, 0)
        self.assertTrue(client.budget.stopped.is_set())
        self.server.reset()
        client = self.client(3)
        self.server.queue(status=429)
        self.server.queue(response())
        self.assertEqual(client.predict(point).choice, 'flat')
        self.assertEqual((client.budget.calls, client.budget.retries), (2, 1))
        self.assertEqual(dict(client.errors), {})

    def test_atomic_call_limit_with_concurrent_permits(self):
        budget = CallBudget(7)
        def attempt(_):
            try:
                budget.consume()
                return True
            except ClientError:
                return False
        with ThreadPoolExecutor(max_workers=6) as pool:
            success = list(pool.map(attempt, range(30)))
        self.assertEqual(sum(success), 7)
        self.assertEqual(budget.calls, 7)

    def test_majority_uses_only_matured_eligible_labels(self):
        # 09:30 -> 10:00 is up; 10:00 -> 10:30 is down.
        with self.store.transaction():
            self.store.db.execute("UPDATE bars SET high='1004',close='1004' WHERE day=? AND bar_end=?",
                (self.days[25], at(self.days[25], '10:00').isoformat()))
        experiment = self.freeze()
        prepare_development(self.store, experiment)
        records = [dict(r) for r in self.store.db.execute('''SELECT t,answer,probs_json FROM predictions
            WHERE method='majority' ORDER BY t LIMIT 3''')]
        self.assertEqual(records[0]['answer'], 'flat')
        self.assertEqual(json.loads(records[0]['probs_json']), {k: 1/3 for k in ('up', 'flat', 'down')})
        self.assertEqual(records[1]['answer'], 'up')
        self.assertEqual(json.loads(records[1]['probs_json']), {'up': .5, 'flat': .25, 'down': .25})
        self.assertEqual(json.loads(records[2]['probs_json']), {'up': .4, 'flat': .2, 'down': .4})

    def test_unpredictable_but_scorable_label_excluded_from_majority_and_cohort(self):
        with self.store.transaction():
            self.store.db.execute('DELETE FROM bars WHERE day=? AND bar_end BETWEEN ? AND ?',
                (self.days[25], at(self.days[25], '09:20').isoformat(), at(self.days[25]).isoformat()))
            self.store.db.execute("UPDATE bars SET high='1004',close='1004' WHERE day=? AND bar_end=?",
                (self.days[25], at(self.days[25], '10:00').isoformat()))
        experiment = self.freeze()
        dev = prepare_development(self.store, experiment)
        self.assertEqual(dev.scorable, 16)
        self.assertEqual(len(dev.cohort), 15)
        majority = self.store.db.execute("SELECT answer,probs_json FROM predictions WHERE method='majority' ORDER BY t LIMIT 1").fetchone()
        self.assertEqual(majority['answer'], 'flat')
        self.assertEqual(json.loads(majority['probs_json']), {k: 1/3 for k in ('up', 'flat', 'down')})

    def test_all_five_methods_share_exact_same_intersection(self):
        experiment = self.freeze()
        dev = prepare_development(self.store, experiment)
        stamps = sorted(dev.cohort)[:3]
        with self.store.transaction():
            cursor = self.store.db.execute('''INSERT INTO runs
                (experiment_id,method,split,started_at,status) VALUES (?,'jev','dev','fixture','complete')''',
                (experiment,))
            for stamp, answer in zip(stamps, ('up', 'flat', 'flat')):
                self.store.db.execute('INSERT INTO predictions VALUES (?,?,?,?,?,?,?,?,?,?)',
                    (experiment, 'jev', stamp, cursor.lastrowid, 'fixture', '{}', answer,
                     json.dumps({label: int(label == answer) for label in ('up', 'flat', 'down')}), None, 'fixture'))
            self.store.db.execute("DELETE FROM predictions WHERE method='momentum' AND t=?", (stamps[2],))
        result = raw_report(self.store, dev, dict(status='complete', n_ok=3, n_fail=0, skipped=0), self.client(), 1)
        self.assertEqual(result['all_methods_intersection']['jev'], {'n': 2, 'correct': 1, 'accuracy': .5})
        for method in ('always_flat', 'majority', 'momentum', 'reversal'):
            self.assertEqual(result['all_methods_intersection'][method], {'n': 2, 'correct': 2, 'accuracy': 1})

    def test_outcomes_baselines_and_runs_commit_atomically(self):
        experiment = self.freeze()
        self.store.db.execute('''CREATE TRIGGER refuse_momentum BEFORE INSERT ON predictions
            WHEN new.method='momentum' BEGIN SELECT RAISE(ABORT,'fixture'); END''')
        with self.assertRaises(sqlite3.IntegrityError):
            prepare_development(self.store, experiment)
        for table in ('outcomes', 'predictions', 'runs'):
            self.assertEqual(self.store.db.execute(f'SELECT count(*) FROM {table}').fetchone()[0], 0)

    def test_competing_process_lock_refuses_run_before_dispatch(self):
        self.freeze()
        with exclusive_run(self.path):
            with self.assertRaisesRegex(ValueError, 'development_run_busy'):
                run_development(self.path, client=self.client())
        self.assertEqual(self.server.requests, [])
