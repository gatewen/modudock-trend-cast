from decimal import localcontext
import hashlib
import json
import unittest

from back.data import DataError
from back.replay import (Replay, ReplayPlan, label_for_prices, observation,
                         relative_permille, temporary_plan, volume_ratio)
from back.store import Store
from tests.replay_fixture import at, plan_for, seed_replay


class ReplayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.base = Store(':memory:')
        cls.days = seed_replay(cls.base)

    @classmethod
    def tearDownClass(cls):
        cls.base.close()

    def setUp(self):
        self.store = Store(':memory:')
        self.base.db.backup(self.store.db)
        self.addCleanup(self.store.close)
        self.plan = plan_for(self.days)
        self.replay = Replay(self.store, self.plan)
        self.day = self.days[25]

    def execute(self, sql, args=()):
        with self.store.transaction():
            self.store.db.execute(sql, args)

    def test_candidates_and_experiment_adapter(self):
        points = list(self.replay.candidates())
        self.assertEqual([p.strftime('%H:%M') for p in points[:8]],
                         ['09:30', '10:00', '10:30', '11:00', '11:30', '12:00', '12:30', '13:00'])
        self.assertEqual(len(points), 40)
        self.assertEqual(points[0], at(self.days[25]))
        row = dict(id=9, dev_start=self.plan.dev_start, dev_end=self.plan.dev_end,
                   hold_start=self.plan.hold_start, hold_end=self.plan.hold_end,
                   threshold_permille=5, feature_version='f1',
                   config_json=json.dumps({'symbol': '2330', 'warmup_start': self.days[0]}))
        adapted = ReplayPlan.from_experiment(row, self.days)
        self.assertEqual(adapted.experiment_id, 9)
        self.assertEqual(adapted.threshold_permille, 5)
        self.assertEqual(adapted.warmup_start, self.days[0])
        with self.assertRaises(DataError):
            ReplayPlan.from_experiment(dict(row, feature_version='f2'), self.days)
        with self.assertRaises(DataError):
            self.replay.prepare(at(self.day, '09:45'))

    def test_f1_matches_hand_computed_state_canonical_json_and_hash(self):
        point = self.replay.prepare(at(self.day))
        expected = {'clock': '09:30', 'minutes_to_close': 240,
                    'today': {'open': 0, 'high': 1, 'low': -1, 'close': 0},
                    'recent': [[i, 0, 1.68] for i in range(30)],
                    'prev_days': [[0, 1, -1, 0, ratio] for ratio in (2.0, 1.91, 1.84, 1.78, 1.72)]}
        serialized = json.dumps(expected, sort_keys=True, separators=(',', ':'))
        self.assertTrue(point.predictable)
        self.assertEqual(point.state, expected)
        self.assertEqual(point.input_json, serialized)
        self.assertEqual(point.input_hash, hashlib.sha256(serialized.encode()).hexdigest())
        copy = point.state
        copy['today']['close'] = 999
        self.assertEqual(point.state, expected)
        self.assertEqual(self.replay.prepare(at(self.day)).input_json, serialized)

    def test_f1_contains_no_identity_date_reference_or_absolute_price(self):
        point = self.replay.prepare(at(self.day))
        self.assertEqual(set(point.state), {'clock', 'minutes_to_close', 'today', 'recent', 'prev_days'})
        for token in ('2330', self.day, 'ref', 'symbol', 'date', '1000', '1001', '999'):
            self.assertNotIn(token, point.input_json)
        self.assertEqual(len(point.state['prev_days']), 5)
        self.assertEqual(len(point.state['recent'][0]), 3)
        self.assertEqual(len(point.state['prev_days'][0]), 5)

    def test_relative_features_invariant_under_uniform_price_rescaling(self):
        before = self.replay.prepare(at(self.day)).input_json
        with self.store.transaction():
            for table in ('bars', 'daily'):
                self.store.db.execute(f'''UPDATE {table} SET open=CAST(open AS INTEGER)*7,
                    high=CAST(high AS INTEGER)*7,low=CAST(low AS INTEGER)*7,close=CAST(close AS INTEGER)*7''')
        self.assertEqual(self.replay.prepare(at(self.day)).input_json, before)

    def test_recent_is_same_day_at_most_60_and_no_gap_filling(self):
        first = self.replay.prepare(at(self.day))
        later = self.replay.prepare(at(self.day, '10:30'))
        self.assertEqual(len(first.state['recent']), 30)
        self.assertEqual([row[0] for row in later.state['recent']], list(range(30, 90)))
        self.execute('DELETE FROM bars WHERE day=? AND ts_raw=?', (self.day, at(self.day, '09:15').isoformat()))
        revised = self.replay.prepare(at(self.day))
        self.assertEqual(len(revised.state['recent']), 29)
        self.assertNotIn(15, [row[0] for row in revised.state['recent']])

    def test_minute_volume_uses_only_present_days_and_minimum_ten(self):
        # Minute zero remains on 10 prior days (16..25 lots); mean=20.5.
        with self.store.transaction():
            for day in self.days[5:15]:
                self.store.db.execute('DELETE FROM bars WHERE day=? AND ts_raw=?', (day, at(day, '09:00').isoformat()))
        self.assertEqual(self.replay.prepare(at(self.day)).state['recent'][0][2], 1.27)
        self.execute('DELETE FROM bars WHERE day=? AND ts_raw=?',
                     (self.days[15], at(self.days[15], '09:00').isoformat()))
        self.assertIsNone(self.replay.prepare(at(self.day)).state['recent'][0][2])

    def test_zero_volume_is_present_and_zero_mean_is_null(self):
        # Ten samples, nine zero and one 10: mean=1. Missing is distinct from zero.
        with self.store.transaction():
            for day in self.days[5:15]:
                self.store.db.execute('DELETE FROM bars WHERE day=? AND ts_raw=?', (day, at(day, '09:00').isoformat()))
            for day in self.days[15:25]:
                self.store.db.execute('UPDATE bars SET volume=? WHERE day=? AND ts_raw=?',
                                     ('10' if day == self.days[24] else '0', day, at(day, '09:00').isoformat()))
        self.assertEqual(self.replay.prepare(at(self.day)).state['recent'][0][2], 26.0)
        self.execute('UPDATE bars SET volume=\'0\' WHERE day=? AND ts_raw=?',
                     (self.days[24], at(self.days[24], '09:00').isoformat()))
        self.assertIsNone(self.replay.prepare(at(self.day)).state['recent'][0][2])
        self.execute("UPDATE daily SET volume='0' WHERE day<?", (self.day,))
        self.assertTrue(all(row[4] is None for row in self.replay.prepare(at(self.day)).state['prev_days']))

    def test_round_half_even_positive_negative_and_volume_precision(self):
        self.assertEqual(relative_permille('1002.5', '1000'), 2)
        self.assertEqual(relative_permille('1003.5', '1000'), 4)
        self.assertEqual(relative_permille('997.5', '1000'), -2)
        self.assertEqual(relative_permille('996.5', '1000'), -4)
        self.assertEqual(volume_ratio('1.225', [1]), 1.22)
        self.assertEqual(volume_ratio('1.235', [1]), 1.24)

    def test_missing_warmup_or_prior_data_rejects_all_methods_input(self):
        plan = temporary_plan(self.store)
        point = Replay(self.store, plan).prepare(at(self.days[24]))
        self.assertFalse(point.predictable)
        self.assertEqual(point.reason, 'insufficient_warmup')
        self.execute('DELETE FROM daily WHERE day=?', (self.days[10],))
        point = self.replay.prepare(at(self.day))
        self.assertFalse(point.predictable)
        self.assertIsNone(point.input_json)
        self.assertIsNone(point.input_hash)
        self.assertEqual(point.reason, 'incomplete_prior_day')

    def test_prior_day_requires_minute_data_without_requiring_every_minute(self):
        self.execute('DELETE FROM bars WHERE day=?', (self.days[10],))
        self.assertEqual(self.replay.prepare(at(self.day)).reason, 'incomplete_prior_day')

    def test_unknown_current_and_prior_reference_reject_no_silent_previous_close(self):
        self.execute('DELETE FROM corp_coverage WHERE start_day=?', (self.day,))
        self.assertEqual(self.replay.prepare(at(self.day)).reason, 'unknown_current_reference')
        with self.store.transaction():
            self.store.db.execute('INSERT INTO corp_coverage VALUES (?,?,?,?,?)',
                                 ('2330', self.day, self.day, 'TWSE:TWT49U', 'fixture'))
            self.store.db.execute('DELETE FROM corp_coverage WHERE start_day=?', (self.days[24],))
        self.assertEqual(self.replay.prepare(at(self.day)).reason, 'unknown_prior_reference')

    def test_event_reference_and_known_none_previous_close(self):
        self.assertEqual(self.replay.prepare(at(self.day)).state['today']['close'], 0)
        self.execute('INSERT INTO corp_events VALUES (?,?,?,?,?)',
                     ('2330', self.day, '1000', '995', 'TWSE:TWT49U'))
        self.assertEqual(self.replay.prepare(at(self.day)).state['today']['close'], 5)
        self.execute('INSERT INTO corp_events VALUES (?,?,?,?,?)',
                     ('2330', self.days[24], '1000', '900', 'TWSE:TWT49U'))
        self.assertEqual(self.replay.prepare(at(self.day)).state['prev_days'][-1][3], 111)

    def test_current_price_freshness_five_minutes_inclusive_and_no_bar(self):
        self.execute('DELETE FROM bars WHERE day=? AND bar_end>?', (self.day, at(self.day, '09:25').isoformat()))
        self.assertTrue(self.replay.prepare(at(self.day)).predictable)
        self.execute('DELETE FROM bars WHERE day=? AND bar_end=?', (self.day, at(self.day, '09:25').isoformat()))
        self.assertEqual(self.replay.prepare(at(self.day)).reason, 'stale_current_price')
        self.execute('DELETE FROM bars WHERE day=?', (self.day,))
        self.assertEqual(self.replay.prepare(at(self.day)).reason, 'no_current_bar')

    def test_future_missingness_changes_scorability_not_predictability_or_input(self):
        point = self.replay.prepare(at(self.day))
        self.assertTrue(self.replay.outcome(point).scorable)
        self.execute('DELETE FROM bars WHERE day=? AND bar_end>?', (self.day, point.t.isoformat()))
        later = self.replay.prepare(point.t)
        self.assertEqual(later.input_json, point.input_json)
        self.assertEqual(later.input_hash, point.input_hash)
        self.assertTrue(later.predictable)
        self.assertFalse(self.replay.outcome(later).scorable)

    def test_endpoint_25_minutes_inclusive_and_older_rejected(self):
        point = self.replay.prepare(at(self.day))
        self.execute('DELETE FROM bars WHERE day=? AND bar_end>?', (self.day, at(self.day, '09:55').isoformat()))
        self.assertTrue(self.replay.outcome(point).scorable)
        self.execute('DELETE FROM bars WHERE day=? AND bar_end=?', (self.day, at(self.day, '09:55').isoformat()))
        self.assertFalse(self.replay.outcome(point).scorable)

    def test_1300_requires_actual_1330_auction_even_if_1325_is_fresh_enough(self):
        point = self.replay.prepare(at(self.day, '13:00'))
        self.assertTrue(self.replay.outcome(point).scorable)
        self.execute('DELETE FROM bars WHERE day=? AND ts_raw=?', (self.day, at(self.day, '13:30').isoformat()))
        result = self.replay.outcome(point)
        self.assertFalse(result.scorable)
        self.assertEqual(result.reason, 'missing_closing_auction')

    def test_outcome_ends_at_30_minutes_and_never_uses_next_minute(self):
        point = self.replay.prepare(at(self.day))
        before = self.replay.outcome(point)
        self.execute("UPDATE bars SET close='1100',high='1100' WHERE day=? AND ts_raw=?",
                     (self.day, at(self.day, '10:00').isoformat()))
        self.assertEqual(self.replay.outcome(point), before)
        self.execute("UPDATE bars SET close='1003',high='1003' WHERE day=? AND bar_end=?",
                     (self.day, at(self.day, '10:00').isoformat()))
        result = self.replay.outcome(point)
        self.assertEqual(result.label, 'up')
        self.assertEqual(result.revealed_at, at(self.day, '10:00'))
        self.assertTrue(observation(point, result).predictable)

    def test_integer_labels_at_boundaries_from_original_price_strings(self):
        for end, expected in [('100.3', 'up'), ('100.299', 'flat'), ('99.7', 'down'),
                              ('99.699', 'down'), ('99.701', 'flat'), ('100', 'flat')]:
            with self.subTest(end=end):
                self.assertEqual(label_for_prices('100', end), expected)
        # Both inputs collapse to the boundary under binary float, but are flat.
        self.assertEqual(label_for_prices('100', '100.299999999999999999999999999999'), 'flat')
        self.assertEqual(label_for_prices('100', '99.700000000000000000000000000001'), 'flat')
        with localcontext() as context:
            context.prec = 3
            self.assertEqual(label_for_prices('100', '100.299999999999999999999999999999'), 'flat')
        self.assertEqual(label_for_prices('100', '100.4', 5), 'flat')
        self.assertEqual(label_for_prices('100', '100.5', 5), 'up')
        for value in (0, -3, True, 3.0):
            with self.assertRaises(DataError):
                label_for_prices('100', '101', value)

    def test_cache_invalidates_when_prior_data_changes_on_same_connection(self):
        before = self.replay.prepare(at(self.day)).input_json
        self.execute("UPDATE daily SET high='1200' WHERE day=?", (self.days[24],))
        self.assertNotEqual(self.replay.prepare(at(self.day)).input_json, before)
