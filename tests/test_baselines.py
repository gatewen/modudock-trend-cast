from dataclasses import replace
import unittest

from back.baselines import Baselines, METHODS
from back.data import DataError
from back.replay import LabelObservation, Replay, temporary_plan
from back.store import Store
from tests.replay_fixture import at, plan_for, seed_replay


class BaselineTests(unittest.TestCase):
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
        self.replay = Replay(self.store, plan_for(self.days))
        self.day = self.days[25]

    def record(self, day, clock, label='flat', predictable=True, scorable=True):
        return LabelObservation(at(day, clock), predictable, scorable, label)

    def test_all_methods_skip_ineligible_point_and_one_hot_probabilities(self):
        replay = Replay(self.store, temporary_plan(self.store))
        point = replay.prepare(at(self.days[0]))
        for method in METHODS:
            result = Baselines(replay).predict(method, point)
            self.assertIsNone(result.answer)
            self.assertIsNone(result.probabilities)
            self.assertEqual(result.reason, 'not_predictable')
        good = self.replay.prepare(at(self.day))
        result = Baselines(self.replay).predict('always_flat', good)
        self.assertEqual(result.answer, 'flat')
        self.assertEqual(result.probabilities, {'up': 0, 'flat': 1, 'down': 0})

    def test_majority_no_labels_flat_uniform_and_stable_ties(self):
        point = self.replay.prepare(at(self.day))
        result = Baselines(self.replay).predict('majority', point)
        self.assertEqual(result.answer, 'flat')
        self.assertEqual(result.probabilities, {'up': 1/3, 'flat': 1/3, 'down': 1/3})
        records = [self.record(self.day, '09:30', 'up'), self.record(self.day, '10:00', 'down')]
        point = self.replay.prepare(at(self.day, '10:30'))
        result = Baselines(self.replay, records).predict('majority', point)
        self.assertEqual(result.answer, 'up')  # Tie priority: flat, up, down.
        self.assertEqual(result.probabilities, {'up': 2/5, 'flat': 1/5, 'down': 2/5})

    def test_majority_maturity_boundary_and_pending_poison_label_ignored(self):
        records = [self.record(self.day, '09:30', 'up'), self.record(self.day, '10:00', object())]
        baseline = Baselines(self.replay, records)
        before = baseline.predict('majority', self.replay.prepare(at(self.day)))
        after = baseline.predict('majority', self.replay.prepare(at(self.day, '10:00')))
        self.assertEqual(before.answer, 'flat')
        self.assertEqual(before.probabilities, {'up': 1/3, 'flat': 1/3, 'down': 1/3})
        self.assertEqual(after.answer, 'up')
        self.assertEqual(after.probabilities, {'up': .5, 'flat': .25, 'down': .25})

    def test_majority_cohort_excludes_unpredictable_unscorable_and_warmup(self):
        records = [self.record(self.day, '09:30'),
                   self.record(self.day, '10:00', 'up', predictable=False),
                   self.record(self.day, '10:30', 'down', scorable=False),
                   self.record(self.days[24], '13:00', 'down')]
        point = self.replay.prepare(at(self.days[26]))
        result = Baselines(self.replay, records).predict('majority', point)
        self.assertEqual(result.probabilities, {'up': .25, 'flat': .5, 'down': .25})

    def test_majority_holdout_uses_frozen_dev_only_in_any_run_order(self):
        records = [self.record(self.days[25], '09:30', 'up'),
                   self.record(self.days[29], '12:30', 'up'),
                   self.record(self.days[29], '13:00', 'down')]
        records += [self.record(day, '09:30', 'down') for day in self.days[30:]]
        baseline = Baselines(self.replay, records)
        early = self.replay.prepare(at(self.days[30]))
        late = self.replay.prepare(at(self.days[-1], '13:00'))
        last = baseline.predict('majority', late)
        first = baseline.predict('majority', early)
        self.assertEqual(first, last)
        self.assertEqual(first.answer, 'up')
        self.assertEqual(first.probabilities, {'up': .5, 'flat': 1/6, 'down': 1/3})
        dev = baseline.predict('majority', self.replay.prepare(at(self.days[25])))
        self.assertEqual(dev.probabilities, {'up': 1/3, 'flat': 1/3, 'down': 1/3})
        records[0] = self.record(self.days[25], '09:30', 'down')
        self.assertEqual(baseline.predict('majority', early), first)

    def test_duplicate_or_cross_experiment_matured_observation_rejected(self):
        point = self.replay.prepare(at(self.day, '10:00'))
        record = self.record(self.day, '09:30')
        with self.assertRaisesRegex(DataError, '^duplicate_observation$'):
            Baselines(self.replay, [record, record]).predict('majority', point)
        for bad in (replace(record, experiment_id=9), replace(record, symbol='0050'),
                    replace(record, threshold_permille=5)):
            with self.assertRaisesRegex(DataError, '^observation_plan_mismatch$'):
                Baselines(self.replay, [bad]).predict('majority', point)

    def test_momentum_first_point_uses_open_not_first_close(self):
        with self.store.transaction():
            self.store.db.execute("UPDATE bars SET close='1010',high='1010' WHERE day=? AND ts_raw=?",
                                 (self.day, at(self.day, '09:00').isoformat()))
            self.store.db.execute("UPDATE bars SET close='1004',high='1004' WHERE day=? AND bar_end=?",
                                 (self.day, at(self.day).isoformat()))
        point = self.replay.prepare(at(self.day))
        momentum = Baselines(self.replay).predict('momentum', point)
        reversal = Baselines(self.replay).predict('reversal', point)
        self.assertEqual(momentum.answer, 'up')
        self.assertEqual(momentum.probabilities, {'up': 1, 'flat': 0, 'down': 0})
        self.assertEqual(reversal.answer, 'down')
        self.assertEqual(reversal.probabilities, {'up': 0, 'flat': 0, 'down': 1})

    def test_momentum_uses_same_day_closed_start_and_five_minute_freshness(self):
        # 10:00 starts at bar_end 09:30; raw 09:30 (end 09:31) is not usable.
        with self.store.transaction():
            self.store.db.execute("UPDATE bars SET close='1100',high='1100' WHERE day=? AND ts_raw=?",
                                 (self.day, at(self.day, '09:30').isoformat()))
        point = self.replay.prepare(at(self.day, '10:00'))
        self.assertEqual(Baselines(self.replay).predict('momentum', point).answer, 'flat')
        with self.store.transaction():
            self.store.db.execute('DELETE FROM bars WHERE day=? AND bar_end>? AND bar_end<=?',
                                 (self.day, at(self.day, '09:25').isoformat(), at(self.day).isoformat()))
        self.assertEqual(Baselines(self.replay).predict('momentum', point).answer, 'flat')
        with self.store.transaction():
            self.store.db.execute('DELETE FROM bars WHERE day=? AND bar_end=?',
                                 (self.day, at(self.day, '09:25').isoformat()))
        result = Baselines(self.replay).predict('momentum', point)
        self.assertIsNone(result.answer)
        self.assertEqual(result.reason, 'missing_momentum_start')

    def test_momentum_missing_start_never_falls_back_to_previous_day(self):
        with self.store.transaction():
            self.store.db.execute('DELETE FROM bars WHERE day=? AND bar_end<=?',
                                 (self.day, at(self.day).isoformat()))
        point = self.replay.prepare(at(self.day, '10:00'))
        self.assertTrue(point.predictable)
        for method in ('momentum', 'reversal'):
            result = Baselines(self.replay).predict(method, point)
            self.assertIsNone(result.answer)
            self.assertEqual(result.reason, 'missing_momentum_start')

    def test_reversal_flat_and_both_directions_and_configured_threshold(self):
        for price, expected in [('1004', 'down'), ('996', 'up'), ('1000', 'flat')]:
            with self.store.transaction():
                self.store.db.execute('UPDATE bars SET close=?,high=\'1100\',low=\'900\' WHERE day=? AND bar_end=?',
                                     (price, self.day, at(self.day).isoformat()))
            point = self.replay.prepare(at(self.day))
            self.assertEqual(Baselines(self.replay).predict('reversal', point).answer, expected)
        other = Replay(self.store, replace(self.replay.plan, threshold_permille=5))
        with self.store.transaction():
            self.store.db.execute("UPDATE bars SET close='1004' WHERE day=? AND bar_end=?",
                                 (self.day, at(self.day).isoformat()))
        self.assertEqual(Baselines(other).predict('momentum', other.prepare(at(self.day))).answer, 'flat')
