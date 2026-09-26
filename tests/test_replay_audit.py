from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from back.data import DataError
from back.replay import Replay
from back.store import Store
from scripts.check_replay import audit_development, main
from tests.replay_fixture import seed_replay
from tests.helpers import experiment


class ReplayAuditTests(unittest.TestCase):
    def test_temporary_audit_refuses_database_with_frozen_experiments(self):
        with Store(':memory:') as store:
            seed_replay(store)
            experiment(store)
            with self.assertRaisesRegex(DataError, '^temporary_audit_requires_no_experiments$'):
                audit_development(store)

    def test_development_cohort_counts_and_no_holdout_traversal_or_output(self):
        with Store(':memory:') as store:
            days = seed_replay(store)
            original = Replay.candidates
            def guarded(engine, split='dev'):
                self.assertEqual(split, 'dev')
                return original(engine, split)
            with patch.object(Replay, 'candidates', guarded):
                report = audit_development(store)
            self.assertEqual(report['dev_trading_days'], 28)
            self.assertEqual(report['candidate_points'], 224)
            self.assertEqual(report['predictable_points'], 24)
            self.assertEqual(report['scorable_points'], 224)
            self.assertEqual(report['predictable_and_scorable_points'], 24)
            self.assertEqual(report['labels']['flat'], {'count': 24, 'percent': 100.0})
            self.assertEqual(report['labels']['up']['count'], 0)
            self.assertEqual(report['unpredictable_reasons'], {'insufficient_warmup': 200})
            for day in days[28:]:
                self.assertNotIn(day, json.dumps(report))
            self.assertNotIn('holdout', json.dumps(report))
            for table in ('experiments', 'outcomes', 'reveals', 'predictions'):
                self.assertEqual(store.db.execute('SELECT count(*) FROM ' + table).fetchone()[0], 0)

    def test_cli_readonly_and_no_database_creation(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'audit.sqlite3'
            with Store(path) as store:
                seed_replay(store)
            out, err = io.StringIO(), io.StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                self.assertEqual(main(['--db', str(path)]), 0)
            self.assertEqual(json.loads(out.getvalue())['candidate_points'], 224)
            self.assertEqual(err.getvalue(), '')
            missing = Path(directory) / 'missing.sqlite3'
            with redirect_stderr(io.StringIO()):
                self.assertEqual(main(['--db', str(missing)]), 2)
            self.assertFalse(missing.exists())
