from dataclasses import replace
from datetime import date
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from back.data import DataError, bar_end, decimal_value
from back.store import Store
from tests.helpers import batch, candle, corp, experiment, KEY


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'store.sqlite3'
        self.store = Store(self.path)
        self.addCleanup(self.store.close)

    def rows(self, table='bars'):
        return [dict(r) for r in self.store.db.execute('SELECT * FROM ' + table)]

    def test_all_tables_wal_and_persistent_exact_prices(self):
        tables = {r[0] for r in self.store.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertEqual(tables, {'bars', 'daily', 'corp_events', 'corp_coverage', 'fetch_log',
                                 'experiments', 'runs', 'predictions', 'outcomes', 'reveals',
                                 'prior_exposures', 'run_scopes'})
        self.assertEqual(self.store.db.execute('PRAGMA journal_mode').fetchone()[0], 'wal')
        self.assertEqual(self.store.db.execute('PRAGMA busy_timeout').fetchone()[0], 1000)
        self.store.write_candles(batch([dict(candle(), close='100.123456789')]))
        with Store(self.path, readonly=True) as other:
            self.assertEqual(other.db.execute('SELECT close FROM bars').fetchone()[0], '100.123456789')

    def test_decimal_conversion_does_not_round_to_context_precision(self):
        original = '100.1234567890123456789012345678901'
        self.assertEqual(decimal_value(original), original)
        self.assertEqual(decimal_value('100.000'), '100')

    def test_same_month_replaced_without_duplicates_and_removed_rows(self):
        rows = [candle(), candle('2024-09-12T09:01:00+08:00')]
        for _ in range(2):
            self.store.write_candles(batch(rows))
        self.assertEqual(len(self.rows()), 2)
        self.store.write_candles(batch([candle(close='100.7')]))
        self.assertEqual(len(self.rows()), 1)
        self.assertEqual(self.rows()[0]['close'], '100.7')

    def test_bad_month_and_duplicate_rejected_without_partial_write(self):
        self.store.write_candles(batch())
        original, logs = self.rows(), self.rows('fetch_log')
        valid = batch([candle(), candle('2024-09-12T09:01:00+08:00')])
        bad_rows = [dict(r) for r in valid.rows]
        bad_rows[-1]['close'] = '999'
        with self.assertRaises(DataError):
            self.store.write_candles(replace(valid, rows=bad_rows))
        with self.assertRaises(DataError):
            self.store.write_candles(replace(valid, rows=[valid.rows[0], valid.rows[0]]))
        self.assertEqual(self.rows(), original)
        self.assertEqual(self.rows('fetch_log'), logs)

    def test_mid_transaction_failure_rolls_back_deletes_inserts_and_log(self):
        self.store.write_candles(batch())
        original, logs = self.rows(), self.rows('fetch_log')
        self.store.db.execute('''CREATE TRIGGER abort_second BEFORE INSERT ON bars
            WHEN new.volume='20' BEGIN SELECT RAISE(ABORT,'fixture failure'); END''')
        rows = [candle(close='100.8'), dict(candle('2024-09-12T09:01:00+08:00'), volume='20')]
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.write_candles(batch(rows))
        self.assertEqual(self.rows(), original)
        self.assertEqual(self.rows('fetch_log'), logs)

    def test_404_empty_preserves_existing_and_records_only_log(self):
        self.store.write_candles(batch())
        original = self.rows()
        for status in (404, 200):
            self.store.write_candles(batch([], status=status))
        self.assertEqual(self.rows(), original)
        self.assertEqual([r['http_status'] for r in self.rows('fetch_log')], [200, 404, 200])
        self.assertEqual([r['note'] for r in self.rows('fetch_log')][-2:], ['empty', 'empty'])

    def test_frozen_bars_protect_updates_deletes_and_insertions(self):
        self.store.write_candles(batch([candle(), candle('2024-09-12T09:01:00+08:00')]))
        original = self.rows()
        experiment(self.store)
        incoming = batch([candle(close='100.8'), candle('2024-09-12T09:02:00+08:00')])
        result = self.store.write_candles(incoming)
        self.assertEqual(self.rows(), original)
        self.assertEqual(result['frozen_differences'], 3)
        self.assertGreaterEqual(result['written'], 0)
        self.assertEqual(self.rows('fetch_log')[-1]['note'], 'frozen_difference')

    def test_frozen_warmup_daily_corp_and_coverage(self):
        self.store.write_candles(batch([candle('2024-09-02')], timeframe='D'))
        self.store.write_corp(corp(event_day='2024-09-02'))
        original_daily = self.rows('daily')
        original_corp, original_coverage = self.rows('corp_events'), self.rows('corp_coverage')
        experiment(self.store)
        self.store.write_candles(batch([candle('2024-09-02', close='100.7')], timeframe='D'))
        self.store.write_corp(corp(event_day=None))
        self.assertEqual(self.rows('daily'), original_daily)
        self.assertEqual(self.rows('corp_events'), original_corp)
        self.assertEqual(self.rows('corp_coverage'), original_coverage)
        self.assertEqual(self.rows('fetch_log')[-1]['note'], 'frozen_difference')

    def test_unfrozen_new_dates_can_extend_frozen_history(self):
        self.store.write_corp(corp())
        experiment(self.store)
        self.store.write_candles(batch([candle('2024-10-01T09:00:00+08:00')], '2024-10-01', '2024-10-31'))
        self.store.write_corp(corp('2024-10-01', '2024-10-31', None))
        self.assertEqual(len(self.rows()), 1)
        self.assertEqual(self.store.corp_state('2330', '2024-10-01')['state'], 'none')

    def test_corp_three_states_and_no_stale_event_after_replacement(self):
        self.assertEqual(self.store.corp_state('2330', '2024-09-12')['state'], 'unknown')
        self.store.write_corp(corp())
        self.assertEqual(self.store.corp_state('2330', '2024-09-12')['state'], 'event')
        self.assertEqual(self.store.corp_state('2330', '2024-09-13')['state'], 'none')
        self.assertEqual(self.store.corp_state('2330', '2024-10-01')['state'], 'unknown')
        self.store.write_corp(corp(event_day=None))
        self.assertEqual(self.store.corp_state('2330', '2024-09-12')['state'], 'none')

    def test_corp_event_and_coverage_atomic(self):
        self.store.db.execute('''CREATE TRIGGER reject_coverage BEFORE INSERT ON corp_coverage
            BEGIN SELECT RAISE(ABORT,'fixture failure'); END''')
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.write_corp(corp())
        self.assertEqual(self.rows('corp_events'), [])
        self.assertEqual(self.rows('corp_coverage'), [])
        self.assertEqual(self.rows('fetch_log'), [])
        self.assertEqual(self.store.corp_state('2330', '2024-09-12')['state'], 'unknown')

    def test_bad_corp_does_not_establish_coverage(self):
        value = corp()
        value.events[0]['ref_price'] = KEY
        with self.assertRaises(DataError) as caught:
            self.store.write_corp(value)
        self.assertNotIn(KEY, str(caught.exception))
        self.assertEqual(self.rows('corp_coverage'), [])
        self.assertNotIn(KEY, '\n'.join(self.store.db.iterdump()))

    def seed_calendar(self):
        october = ['01', '02', '03', '04', '07', '08', '09', '10']
        self.store.write_candles(batch([candle('2024-10-' + d) for d in october],
                                      '2024-10-01', '2024-10-31', 'D'))
        self.store.write_candles(batch([candle('2024-09-12')], timeframe='D'))
        self.store.write_corp(corp())

    def test_finalized_month_skipped_current_and_recent_seven_refetched(self):
        self.seed_calendar()
        result = self.store.write_candles(batch(), today=date(2024, 10, 11))
        self.assertTrue(result['final'])
        self.assertFalse(self.store.should_fetch('2330', '2024-09', today=date(2024, 10, 11)))
        self.assertTrue(self.store.should_fetch('2330', '2024-10', today=date(2024, 10, 11)))
        # On the first day of a new month, previous month's last seven days remain mutable.
        self.assertTrue(self.store.should_fetch('2330', '2024-09', today=date(2024, 10, 1)))

    def test_missing_day_unknown_corp_partial_month_not_final(self):
        self.seed_calendar()
        self.store.write_candles(batch([candle('2024-09-12'), candle('2024-09-13')], timeframe='D'))
        self.assertFalse(self.store.write_candles(batch(), today=date(2024, 10, 11))['final'])
        with self.store.transaction():
            self.store.db.execute('DELETE FROM corp_coverage')
        self.assertFalse(self.store.write_candles(batch(), today=date(2024, 10, 11))['final'])
        self.store.write_corp(corp())
        partial = batch([candle()], start='2024-09-12')
        self.assertFalse(self.store.write_candles(partial, today=date(2024, 10, 11))['final'])

    def test_available_bars_boundary_raw_at_t_excluded_end_at_t_included(self):
        self.store.write_candles(batch([candle('2024-09-12T09:29:00+08:00'),
                                        candle('2024-09-12T09:30:00+08:00')]))
        visible = self.store.available_bars('2024-09-12', '2024-09-12T09:30:00+08:00')
        self.assertEqual([r['bar_end'] for r in visible], ['2024-09-12T09:30:00+08:00'])
        self.store.write_candles(batch([candle('2024-09-12T09:29:00+08:00'),
                                       candle('2024-09-12T09:30:00+08:00', close='100.8')]))
        self.assertEqual(self.store.available_bars('2024-09-12', '2024-09-12T09:30:00+08:00'), visible)
        self.store.write_candles(batch([candle('2024-09-12T09:29:00+08:00', close='100.7')]))
        self.assertNotEqual(self.store.available_bars('2024-09-12', '2024-09-12T09:30:00+08:00'), visible)

    def test_available_bars_midminute_catches_plus_60_seconds_mutation(self):
        self.store.write_candles(batch([candle('2024-09-12T09:29:00+08:00'),
                                        candle('2024-09-12T09:30:00+08:00')]))
        self.assertEqual(len(self.store.available_bars('2024-09-12', '2024-09-12T09:30:30+08:00')), 1)

    def test_auction_timezone_and_day_isolation(self):
        self.assertEqual(bar_end('2024-09-12T13:30:00.000+08:00'), '2024-09-12T13:30:00+08:00')
        self.store.write_candles(batch([candle('2024-09-12T13:30:00+08:00'),
                                       candle('2024-09-13T09:00:00+08:00')]))
        self.assertEqual(len(self.store.available_bars('2024-09-12', '2024-09-12T05:30:00Z')), 1)
        self.assertEqual(self.store.available_bars('2024-09-12', '2024-09-12T13:29:59+08:00'), [])
        with self.assertRaises(DataError):
            self.store.available_bars('2024-09-12', '2024-09-13T10:00:00+08:00')
