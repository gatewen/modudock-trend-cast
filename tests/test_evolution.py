from collections import Counter
from dataclasses import replace
from datetime import date, timedelta
from fractions import Fraction
import math
import random
import unittest
from unittest.mock import patch

from back.baselines import Baselines
from back.data import DataError
from back.evolution import LearningPoint, METHODS, WalkForward, smoothed, volatility
from back.replay import LabelObservation, PreparedPoint, Replay, ReplayPlan
from tests.replay_fixture import at


class EvolutionTests(unittest.TestCase):
    def setUp(self):
        self.days = tuple((date(2024, 1, 1) + timedelta(days=i)).isoformat() for i in range(150))
        self.plan = ReplayPlan(self.days, self.days[1], self.days[139], self.days[140], self.days[-1], experiment_id=1)
        self.replay = Replay(None, self.plan)
        self.point = self.point_at(self.days[120])

    def point_at(self, day, clock='09:30', closes=None):
        stamp = at(day, clock)
        closes = closes or ['100'] * 30
        bars = tuple({'bar_end': (stamp - timedelta(minutes=len(closes)-1-i)).isoformat(),
                      'close': str(close)} for i, close in enumerate(closes))
        return PreparedPoint(stamp, 1, '2330', 3, True, None, visible_bars=bars)

    def record(self, i, label='down', clock='09:30', vol=0.0, choice='up', **kwargs):
        obs = LabelObservation(at(self.days[i], clock), True, True, label, 1)
        return LearningPoint(replace(obs, **kwargs), vol, choice)

    def assert_fallback(self, method, records, point=None, choice='up'):
        point = point or self.point
        actual = WalkForward(self.replay, records).predict(method, point, jev_choice=choice)
        expected = Baselines(self.replay, [r.observation for r in records]).predict('majority', point)
        self.assertEqual((actual.answer, actual.probabilities), (expected.answer, expected.probabilities))
        self.assertEqual(actual.reason, 'majority_fallback')

    def test_empty_and_29_samples_use_exact_majority_probabilities(self):
        for method in METHODS:
            self.assert_fallback(method, [])
            self.assert_fallback(method, [self.record(i) for i in range(1, 30)])
        records = [self.record(i) for i in range(1, 30)] + [self.record(31, 'flat', clock='10:00', vol=100, choice='down')]
        for method in ('clock_prior', 'jev_calibrated'):
            self.assert_fallback(method, records)

    def test_exactly_30_uses_group_only_and_add_one(self):
        records = [self.record(i) for i in range(1, 31)]
        records += [self.record(i, 'flat', clock='10:00', choice='down') for i in range(31, 100)]
        for method in ('clock_prior', 'jev_calibrated'):
            result = WalkForward(self.replay, records).predict(method, self.point, jev_choice='up')
            self.assertIsNone(result.reason)
            self.assertEqual(result.answer, 'down')
            self.assertEqual(result.probabilities, {'up': 1/33, 'flat': 1/33, 'down': 31/33})

    def test_all_eight_clock_buckets_are_separate(self):
        clocks = ('09:30','10:00','10:30','11:00','11:30','12:00','12:30','13:00')
        labels = ('up', 'flat', 'down')
        records = [self.record(i, labels[j % 3], clock=clock) for j, clock in enumerate(clocks) for i in range(1,31)]
        for j, clock in enumerate(clocks):
            result = WalkForward(self.replay, records).predict('clock_prior', self.point_at(self.days[120], clock))
            self.assertEqual(result.answer, labels[j % 3])
            self.assertAlmostEqual(result.probabilities[labels[j % 3]], 31/33)

    def test_exact_30_minute_boundary_and_current_truth_unavailable(self):
        point = self.point_at(self.days[120], '10:00')
        history = [self.record(i) for i in range(1,30)]
        boundary = self.record(120, 'up')
        poison = self.record(120, object(), clock='10:00', choice=object(), vol=object())
        learner = WalkForward(self.replay, history + [boundary, poison])
        self.assertEqual(len(learner.matured(point)), 30)
        for method in ('vol_prior', 'jev_calibrated'):
            result = learner.predict(method, point, jev_choice='up')
            self.assertIsNone(result.reason)
            self.assertEqual(result.probabilities, {'up': 2/33, 'flat': 1/33, 'down': 30/33})

    def test_future_random_labels_choices_and_volatility_do_not_change_outputs(self):
        records = [self.record(i, ('up','flat','down')[i%3], vol=float(i%5), choice=('up','flat','down')[i%3]) for i in range(1,120)]
        records += [self.record(i, 'down', vol=0, choice='up') for i in range(120,140)]
        original = {m: WalkForward(self.replay, records).predict(m, self.point, jev_choice='up') for m in METHODS}
        rng = random.Random(27)
        for _ in range(12):
            changed = [replace(r, observation=replace(r.observation, label=rng.choice(('up','flat','down'))),
                        jev_choice=rng.choice(('up','flat','down')), volatility=rng.random()*10000)
                       if r.observation.t + timedelta(minutes=30) > self.point.t else r for r in records]
            rng.shuffle(changed)
            for method in METHODS:
                self.assertEqual(WalkForward(self.replay, changed).predict(method, self.point, jev_choice='up'), original[method])

    def test_only_eligible_dev_observations_train_and_foreign_records_rejected(self):
        records = [self.record(1, 'up'), self.record(2, predictable=False),
                   self.record(3, scorable=False), self.record(0), self.record(140)]
        self.assertEqual(WalkForward(self.replay,records).matured(self.point),(records[0],))
        for method in METHODS:
            self.assert_fallback(method, records)
        for bad in (self.record(1, experiment_id=2), self.record(1, symbol='0050'),
                    self.record(1, threshold_permille=4)):
            with self.assertRaisesRegex(DataError, 'observation_plan_mismatch'):
                WalkForward(self.replay, [bad]).predict('clock_prior', self.point)
        with self.assertRaisesRegex(DataError, 'duplicate_observation'):
            WalkForward(self.replay, [records[0], records[0]]).predict('clock_prior', self.point)
        trained = [self.record(i) for i in range(1,31)]
        with self.assertRaisesRegex(DataError, 'duplicate_observation'):
            WalkForward(self.replay, trained + [trained[0]]).predict('clock_prior', self.point)
        with self.assertRaisesRegex(DataError, 'observation_plan_mismatch'):
            WalkForward(self.replay, trained + [self.record(31, experiment_id=2)]).predict('clock_prior', self.point)

    def test_non_dev_points_and_foreign_points_refused_and_unpredictable_skipped(self):
        for method in METHODS:
            with self.assertRaisesRegex(DataError, 'evolution_dev_only'):
                WalkForward(self.replay).predict(method, self.point_at(self.days[140]))
            with self.assertRaisesRegex(DataError, 'point_plan_mismatch'):
                WalkForward(self.replay).predict(method, replace(self.point, experiment_id=2))
            self.assertIsNone(WalkForward(self.replay).predict(method, replace(self.point, predictable=False)).answer)
        with self.assertRaisesRegex(DataError, 'unknown_method'):
            WalkForward(self.replay).predict('jev', self.point)

    def test_volatility_29_simple_returns_population_std_last30_visible_only(self):
        closes = [Fraction(100)]
        returns = [Fraction(1,10) if i % 2 else Fraction(-1,20) for i in range(29)]
        for ret in returns:
            closes.append(closes[-1] * (1 + ret))
        point = self.point_at(self.days[120], '10:30', [999999] + [float(v) for v in closes])
        mean = sum(returns) / 29
        expected = math.sqrt(float(sum((v - mean)**2 for v in returns) / 29))
        self.assertAlmostEqual(volatility(point), expected, places=13)
        future = {'bar_end': (point.t + timedelta(minutes=1)).isoformat(), 'close': '9999999'}
        self.assertEqual(volatility(replace(point, visible_bars=point.visible_bars + (future,))), volatility(point))
        self.assertIsNone(volatility(replace(point, visible_bars=point.visible_bars[-29:])))

    def test_vol_terciles_linear_equal_cuts_go_lower_and_group_smoothing(self):
        records = [self.record(i+1, ('up','flat','down')[i//30], vol=float(i)) for i in range(90)]
        learner = WalkForward(self.replay, records)
        lower, upper = 89 * (1/3), 89 * (2/3)
        for current, label in [(0,'up'), (lower,'up'), (lower+1e-8,'flat'),
                               (upper,'flat'), (upper+1e-8,'down'), (100,'down')]:
            with patch('back.evolution.volatility', return_value=current):
                result = learner.predict('vol_prior', self.point)
                self.assertEqual(result.answer, label)
                self.assertEqual(result.probabilities[label], 31/33)
                self.assertIsNone(result.reason)

    def test_vol_cuts_use_only_matured_points_and_recompute_past_groups(self):
        past = [self.record(i+1, ('up','flat','down')[i//30], vol=float(i)) for i in range(90)]
        future = [self.record(i, 'up', vol=10000) for i in range(120,140)]
        with patch('back.evolution.volatility', return_value=70):
            result = WalkForward(self.replay, past+future).predict('vol_prior', self.point)
        self.assertEqual(result.probabilities, {'up':1/33,'flat':1/33,'down':31/33})
        # Add newly matured small values: old high observations regroup with
        # ten former middle observations; their labels must be recounted.
        grown = past + [self.record(i, 'up', vol=-0.0) for i in range(91,121)]
        with patch('back.evolution.volatility', return_value=70):
            result = WalkForward(self.replay, grown).predict('vol_prior', self.point_at(self.days[121]))
        self.assertEqual(result.probabilities, {'up':1/43,'flat':11/43,'down':31/43})

    def test_vol_small_group_not_total_sample_count_and_missing_bars_fallback(self):
        records = [self.record(i+1, 'flat', vol=float(i)) for i in range(60)]
        with patch('back.evolution.volatility', return_value=0):
            self.assert_fallback('vol_prior', records)
        self.assert_fallback('vol_prior', records, replace(self.point, visible_bars=()))
        self.assert_fallback('vol_prior', [replace(r, volatility=None) for r in records])

    def test_vol_duplicate_terciles_no_arbitrary_tie_split(self):
        records = [self.record(i, 'up', vol=0) for i in range(1,91)]
        result = WalkForward(self.replay, records).predict('vol_prior', self.point)
        self.assertEqual(result.probabilities, {'up':91/93,'flat':1/93,'down':1/93})

    def test_calibration_uses_current_choice_rows_not_probabilities_or_truth_columns(self):
        records = [self.record(i, 'down', choice='up') for i in range(1,31)]
        records += [self.record(i, 'up', choice='flat') for i in range(31,61)]
        records += [self.record(i, 'flat', choice='down') for i in range(61,91)]
        for choice, truth in [('up','down'),('flat','up'),('down','flat')]:
            result = WalkForward(self.replay, records).predict('jev_calibrated', self.point, jev_choice=choice)
            self.assertEqual(result.answer, truth)
            self.assertEqual(result.probabilities[truth],31/33)
        missing = WalkForward(self.replay, records).predict('jev_calibrated', self.point)
        self.assertIsNone(missing.answer)
        self.assertEqual(missing.reason, 'missing_current_jev')

    def test_ties_flat_then_up_then_down_and_probabilities_sum_to_one(self):
        for counts, answer in [({'up':10,'flat':10,'down':10},'flat'),
                                ({'up':15,'flat':0,'down':15},'up')]:
            result = smoothed('clock_prior', Counter(counts))
            self.assertEqual(result.answer,answer)
            self.assertAlmostEqual(sum(result.probabilities.values()),1)

    def test_invalid_matured_values_rejected(self):
        for bad in (self.record(1, 'wrong'), self.record(1, choice='wrong'),
                    self.record(1, vol=float('nan')), self.record(1, vol=-1)):
            with self.assertRaises(DataError):
                WalkForward(self.replay,[bad]).predict('clock_prior', self.point)


if __name__ == '__main__':
    unittest.main()
