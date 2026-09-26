from contextlib import redirect_stderr
import io
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from back.data import DataError
from back.db_writer import DBWriter
from back.experiment import (ActivityGate, experiment_row, holdout_overlap, record_exposure,
    reveal_holdout)
from back.jevcast import JevRunner, _AUTH_DISABLED
from back.jobs import BaselineRunner
from back.report import build_report, markdown_report
from back.store import Store
from scripts.check_data import build_report as data_report
from scripts.run_dev import CallBudget, DevClient
from scripts.run_holdout import completion, finish_and_reveal, main, run_holdout
from tests.experiment_fixture import frozen_experiment
from tests.helpers import FakeServer, KEY
from tests.score_fixture import change_answers, scored_fixture, seed_split
from tests.test_jev_client import response


class HoldoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = FakeServer()

    @classmethod
    def tearDownClass(cls):
        cls.server.close()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'held.sqlite3'
        self.store = Store(self.path); self.addCleanup(self.store.close)
        self.row, self.days = scored_fixture(self.store)
        # Deliberately make majority the DEVELOPMENT choice; holdout itself will
        # prefer a different baseline, proving it is not used for selection.
        for method in ('always_flat', 'momentum', 'reversal'):
            change_answers(self.store, method, 'up', self.row['dev_start'], self.row['dev_end'])
        self.server.reset(); _AUTH_DISABLED.clear(); self.addCleanup(_AUTH_DISABLED.clear)
        env = patch.dict(os.environ, {'TYPESAFE_API_KEY': KEY}); env.start(); self.addCleanup(env.stop)

    def client(self, limit=1500):
        return DevClient(CallBudget(limit), opener=self.server, sleep=lambda _: None)

    def run_final(self, **kwargs):
        return run_holdout(self.path, experiment_id=1, split='holdout', **kwargs)

    def queue(self, count=16):
        for _ in range(count): self.server.queue(response())

    def test_cli_requires_execute_split_experiment_and_enforces_ceiling_before_writer(self):
        variants = [[], ['--experiment', '1', '--split', 'holdout'],
            ['--execute', '--experiment', '2', '--split', 'holdout'],
            ['--execute', '--experiment', '1', '--split', 'dev'],
            ['--execute', '--experiment', '1', '--split', 'holdout', '--max-calls', '1501']]
        for args in variants:
            with self.subTest(args=args), redirect_stderr(io.StringIO()), \
                 patch('scripts.run_holdout.run_holdout') as execute:
                with self.assertRaises(SystemExit): main(args)
                execute.assert_not_called()

    def test_experiment_two_refused_by_script_runners_and_both_reveal_paths(self):
        p2 = frozen_experiment(self.store, self.days, end=self.days[30], prompt_version='p2')
        with patch('scripts.run_holdout.DBWriter') as constructor:
            with self.assertRaisesRegex(DataError, 'holdout_experiment_forbidden'):
                run_holdout(self.path, experiment_id=p2['id'], split='holdout')
            constructor.assert_not_called()
        writer, gate = DBWriter(self.path), ActivityGate()
        jev, baseline = JevRunner(writer, gate), BaselineRunner(writer, gate)
        try:
            for runner in (jev, baseline):
                kwargs = {'method': 'majority'} if runner is baseline else {}
                with self.assertRaisesRegex(DataError, 'holdout_experiment_forbidden'):
                    runner.start(p2['id'], split='holdout', **kwargs)
            with self.assertRaisesRegex(DataError, 'holdout_experiment_forbidden'):
                reveal_holdout(self.store, p2['id'])
            with self.assertRaisesRegex(DataError, 'holdout_experiment_forbidden'):
                data_report(self.store, experiment_id=p2['id'], include_holdout=True)
        finally:
            jev.close().result(3); baseline.close().result(3)
            writer.close(); writer.thread.join(3)
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM reveals').fetchone()[0], 0)
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM runs WHERE experiment_id=2').fetchone()[0], 0)

    def test_development_selection_change_stops_before_holdout_work(self):
        change_answers(self.store, 'majority', 'up', self.row['dev_start'], self.row['dev_end'])
        self.queue()
        with self.assertRaisesRegex(DataError, 'development_selection_changed'):
            self.run_final(client=self.client())
        self.assertEqual(len(self.server.bodies), 0)
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM runs WHERE split='holdout'").fetchone()[0], 0)
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM reveals').fetchone()[0], 0)

    def test_reveal_only_after_every_commit_keeps_development_baseline_and_prior_overlap(self):
        with self.store.transaction():
            record_exposure(self.store, '2330', self.row['hold_start'], self.row['hold_start'], 'fixture-exposure')
        before = [tuple(r) for r in self.store.db.execute('SELECT * FROM predictions ORDER BY t,method')]
        original, observed = reveal_holdout, []
        def reveal(store, experiment, **kwargs):
            observed.append(completion(store, experiment_row(store, experiment)))
            self.assertTrue(observed[-1]['complete'])
            return original(store, experiment, **kwargs)
        self.queue()
        with patch('scripts.run_holdout.reveal_holdout', side_effect=reveal):
            result = self.run_final(client=self.client())
        self.assertEqual(len(observed), 1)
        self.assertTrue(result['complete']); self.assertEqual(result['http_calls'], 16)
        held = result['report']['holdout']
        self.assertEqual(held['comparison']['baseline'], 'majority')
        self.assertLess(held['methods']['always_flat']['brier'], held['methods']['majority']['brier'])
        self.assertEqual(held['prior_overlap_days'], 1)
        self.assertEqual(holdout_overlap(self.store, 1), 2)
        self.assertIn('解鎖前 1 日重疊', markdown_report(result['report']))
        self.assertEqual(build_report(self.store, experiment_id=1)['holdout']['prior_overlap_days'], 1)
        for raw in self.server.bodies:
            self.assertEqual(json.loads(raw)['questions']['direction']['instructions'],
                '根據 state，這檔股票從現在到 30 分鐘後，價格變化最可能落在哪一類？')
        after = [tuple(r) for r in self.store.db.execute('SELECT * FROM predictions WHERE substr(t,1,10)<=? ORDER BY t,method', (self.row['dev_end'],))]
        self.assertEqual(before, after)
        again = self.run_final(max_calls=0, client=self.client(0))
        self.assertTrue(again['complete']); self.assertEqual(again['http_calls'], 0)
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM reveals').fetchone()[0], 1)

    def test_missing_answers_quota_exhaustion_never_reveals_or_reports_holdout_numbers(self):
        self.queue()
        with patch('scripts.run_holdout.reveal_holdout', wraps=reveal_holdout) as reveal:
            result = self.run_final(max_calls=3, client=self.client(3))
        reveal.assert_not_called()
        self.assertFalse(result['complete']); self.assertNotIn('report', result)
        self.assertEqual(result['http_calls'], 3)
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM reveals').fetchone()[0], 0)
        with self.assertRaisesRegex(DataError, 'holdout_incomplete'):
            finish_and_reveal(self.store, 1)
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM reveal_context').fetchone()[0], 0)
        self.assertEqual(build_report(self.store, experiment_id=1)['holdout'], {'state': 'locked', 'message': '保留段未解鎖'})

    def test_shared_budget_repairs_missing_answers_before_single_reveal(self):
        self.server.queue(response(choice='up'))  # choice is not maximum; reject.
        self.queue(16)
        result = self.run_final(max_calls=17, client=self.client(17))
        self.assertTrue(result['complete'])
        self.assertEqual(result['http_calls'], 17)
        self.assertEqual(result['errors'], {'choice_not_maximum': 1})
        self.assertEqual(result['report']['holdout']['methods']['jev']['n'], 16)
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM reveals').fetchone()[0], 1)

    def test_incomplete_traversal_corrupt_answer_or_missing_outcome_blocks_atomic_reveal(self):
        seed_split(self.store, self.row, 'holdout')
        statements = ["UPDATE runs SET status='running' WHERE split='holdout' AND method='jev'",
            "UPDATE run_scopes SET full_split=0 WHERE run_id IN (SELECT id FROM runs WHERE split='holdout')",
            "UPDATE predictions SET probs_json='{}' WHERE substr(t,1,10)>'" + self.row['dev_end'] + "' AND method='majority'",
            "DELETE FROM outcomes WHERE substr(t,1,10)>'" + self.row['dev_end'] + "'",
            "UPDATE outcomes SET label='up' WHERE substr(t,1,10)>'" + self.row['dev_end'] + "'"]
        for sql in statements:
            snapshot = sqlite3.connect(':memory:'); self.store.db.backup(snapshot)
            try:
                self.store.db.execute(sql); self.store.db.commit()
                with self.assertRaisesRegex(DataError, 'holdout_incomplete'):
                    finish_and_reveal(self.store, 1)
                self.assertEqual(self.store.db.execute('SELECT count(*) FROM reveals').fetchone()[0], 0)
            finally:
                snapshot.backup(self.store.db); snapshot.close()

    def test_reveal_write_failure_rolls_back_exposure_context_and_stays_locked(self):
        seed_split(self.store, self.row, 'holdout')
        self.store.db.execute("CREATE TRIGGER reject_final BEFORE INSERT ON reveals BEGIN SELECT RAISE(ABORT, 'fixture'); END")
        with self.assertRaises(sqlite3.IntegrityError): finish_and_reveal(self.store, 1)
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM reveal_context').fetchone()[0], 0)
        self.assertEqual(build_report(self.store, experiment_id=1)['holdout']['state'], 'locked')
