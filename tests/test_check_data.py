from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest

from back.data import DataError
from back.store import Store
from scripts.check_data import build_report, main
from tests.helpers import batch, candle, corp, experiment


class CheckDataTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'test.sqlite3'
        self.store = Store(self.path)
        self.addCleanup(self.store.close)
        self.store.write_candles(batch([candle(), candle('2024-09-12T13:30:00+08:00')]))
        self.store.write_candles(batch([dict(candle('2024-09-12'), volume='20000'), candle('2024-09-13')], timeframe='D'))
        self.store.write_corp(corp())

    def test_no_experiment_only_quality_no_prices_or_labels(self):
        report = build_report(self.store)
        self.assertEqual(len(report), 6)
        self.assertEqual(report['1_calendar']['missing_bar_days'], ['2024-09-13'])
        self.assertEqual(report['2_timestamps']['first_raw_time_counts'], {'09:00': 1})
        self.assertFalse(report['2_timestamps']['semantics_proven'])
        self.assertEqual(report['2_timestamps']['days_with_1330'], 1)
        self.assertEqual(report['5_volume']['minute_sum_over_daily']['median'], '0.001')
        self.assertEqual(report['6_corporate_actions']['states'], {'event': 1, 'none': 1, 'unknown': 0})
        serialized = json.dumps(report)
        for forbidden in ('896.99', '901', '100.3', '"label"', '"return"', 'ref_price', 'prev_close'):
            self.assertNotIn(forbidden, serialized)
        with self.assertRaisesRegex(DataError, '^experiment_required$'):
            build_report(self.store, include_holdout=True)
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM reveals').fetchone()[0], 0)

    def test_dev_only_then_explicit_reveal_persists_before_report(self):
        identity = experiment(self.store)
        with self.store.transaction():
            self.store.db.executemany('INSERT INTO outcomes VALUES (?,?,?,?,?)',
                [(identity, '2024-09-12T09:30:00+08:00', '100', '101', 'up'),
                 (identity, '2024-09-13T09:30:00+08:00', '100', '99', 'down')])
        report = build_report(self.store)
        self.assertEqual(report['7_dev_labels']['counts'], {'up': 1, 'flat': 0, 'down': 0})
        self.assertNotIn('8_holdout_labels', report)
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM reveals').fetchone()[0], 0)
        report = build_report(self.store, include_holdout=True)
        self.assertEqual(report['8_holdout_labels']['counts'], {'up': 0, 'flat': 0, 'down': 1})
        with Store(self.path, readonly=True) as reader:
            reveal = reader.db.execute('SELECT * FROM reveals').fetchone()
            self.assertEqual(reveal['first_day'], '2024-09-13')
            self.assertEqual(reveal['experiment_id'], identity)

    def test_frozen_difference_warning_without_prices(self):
        experiment(self.store)
        self.store.write_candles(batch([candle(close='100.7')]))
        report = build_report(self.store)
        self.assertEqual(report['1_calendar']['frozen_warnings'], [{'month': '2024-09', 'kind': 'bars', 'count': 1}])
        self.assertNotIn('100.7', json.dumps(report))

    def test_cli_readonly_and_missing_database(self):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            self.assertEqual(main(['--db', str(self.path)]), 0)
        self.assertEqual(len(json.loads(out.getvalue())), 6)
        self.assertEqual(err.getvalue(), '')
        missing = Path(self.temp.name) / 'missing.sqlite3'
        with redirect_stderr(io.StringIO()):
            self.assertEqual(main(['--db', str(missing)]), 2)
        self.assertFalse(missing.exists())

    def test_corrupt_numeric_rows_reported_without_crashing_or_showing_prices(self):
        with self.store.transaction():
            self.store.db.execute("UPDATE bars SET close='-1',volume='NaN'")
            self.store.db.execute("UPDATE daily SET close='0'")
        report = build_report(self.store)
        self.assertEqual(report['4_validation']['invalid_ohlc'], 2)
        self.assertEqual(report['4_validation']['nonpositive_prices'], 2)
        self.assertEqual(report['4_validation']['invalid_daily_ohlc'], 2)
        self.assertEqual(report['5_volume']['minute_sum_over_daily']['count'], 0)
        self.assertEqual(len(report), 6)
