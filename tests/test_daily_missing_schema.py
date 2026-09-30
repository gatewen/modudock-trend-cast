import tempfile
import unittest
from pathlib import Path

from back.daily_experiment import load_experiment
from back.daily_forward_view import forward_view
from back.daily_store import DailyStore
from back.data import DataError


class MissingDailySchemaTests(unittest.TestCase):
    """A fresh install has no d_* tables; reads must say so instead of leaking SQLite errors."""
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = DailyStore(str(Path(self.tmp.name) / 'fresh.sqlite3'))
        self.store.db.execute('DROP TABLE IF EXISTS d_experiments')

    def tearDown(self):
        self.store.db.close()
        self.tmp.cleanup()

    def test_load_experiment_reports_missing(self):
        with self.assertRaisesRegex(DataError, '^daily_experiment_missing$'):
            load_experiment(self.store)

    def test_forward_view_reports_missing(self):
        with self.assertRaisesRegex(DataError, '^daily_experiment_missing$'):
            forward_view(self.store, dict(op='daily_forward', H=7))


if __name__ == '__main__':
    unittest.main()
