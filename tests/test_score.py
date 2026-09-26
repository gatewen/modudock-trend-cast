import math
import unittest

from back.data import DataError
from back.score import (BOOTSTRAP_REPETITIONS, BOOTSTRAP_SEED, INCOMPLETE, NO_EVIDENCE,
    ALL_METHODS, ScoredPoint, SplitScores, best_development_baseline, conclusion,
    descriptive_choices, metrics, paired_bootstrap, percentile, split_report, wilson95)


def point(t, truth, choice, probabilities=None):
    return ScoredPoint(t, truth, choice, probabilities or {label: int(choice == label)
                                                         for label in ('up', 'flat', 'down')})


class ScoreMathTests(unittest.TestCase):
    def test_hand_calculated_accuracy_confusion_precision_recall_and_brier(self):
        points = [
            point('2024-01-01T09:30', 'up', 'up', {'up': .7, 'flat': .2, 'down': .1}),
            point('2024-01-01T10:00', 'up', 'flat', {'up': .2, 'flat': .6, 'down': .2}),
            point('2024-01-02T09:30', 'flat', 'flat', {'up': .1, 'flat': .8, 'down': .1}),
            point('2024-01-02T10:00', 'down', 'up', {'up': .6, 'flat': .1, 'down': .3})]
        scored = metrics(points)
        self.assertEqual((scored['n'], scored['correct'], scored['accuracy']), (4, 2, .5))
        # Per-point squared errors: .14, 1.04, .06, .86; sum / 4 = .525.
        self.assertAlmostEqual(scored['brier'], .525)
        self.assertEqual(scored['confusion_matrix'], {
            'up': {'up': 1, 'flat': 1, 'down': 0},
            'flat': {'up': 0, 'flat': 1, 'down': 0},
            'down': {'up': 1, 'flat': 0, 'down': 0}})
        self.assertEqual(scored['per_class']['up']['precision'], .5)
        self.assertEqual(scored['per_class']['up']['recall'], .5)
        self.assertEqual(scored['per_class']['flat']['precision'], .5)
        self.assertEqual(scored['per_class']['flat']['recall'], 1)
        self.assertIsNone(scored['per_class']['down']['precision'])
        self.assertEqual(scored['per_class']['down']['recall'], 0)
        self.assertEqual(scored['confusion_axes']['rows'], 'truth')
        self.assertAlmostEqual(scored['accuracy_wilson95'][0], .15003898915214947)
        self.assertAlmostEqual(scored['accuracy_wilson95'][1], .8499610108478506)

    def test_wilson_empty_extremes_known_values_and_invalid_counts(self):
        self.assertIsNone(wilson95(0, 0))
        self.assertEqual(wilson95(0, 10)[0], 0)
        self.assertAlmostEqual(wilson95(0, 10)[1], .2775327998628892)
        self.assertEqual(wilson95(10, 10)[1], 1)
        self.assertAlmostEqual(wilson95(10, 10)[0], .7224672001371107)
        for bad in ((-1, 10), (11, 10), (1, -1), (True, 10), (1, 2.5)):
            with self.assertRaises(DataError):
                wilson95(*bad)

    def test_multiclass_brier_not_divided_by_classes_or_two(self):
        self.assertEqual(metrics([point('d', 'up', 'down')])['brier'], 2)
        self.assertEqual(metrics([point('d', 'up', 'up')])['brier'], 0)
        self.assertAlmostEqual(metrics([point('d', 'up', 'flat',
            {label: 1/3 for label in ('up', 'flat', 'down')})])['brier'], 2/3)

    def test_empty_metrics_do_not_invent_accuracy_or_precision(self):
        result = metrics([])
        self.assertEqual((result['n'], result['correct']), (0, 0))
        for key in ('accuracy', 'brier', 'accuracy_wilson95'):
            self.assertIsNone(result[key])
        for values in result['per_class'].values():
            self.assertIsNone(values['precision'])
            self.assertIsNone(values['recall'])

    def test_descriptive_bias_and_choice_hit_rates_use_identical_samples(self):
        data = [point('d1', 'up', 'up'), point('d2', 'up', 'flat'),
                point('d3', 'flat', 'flat'), point('d4', 'down', 'up')]
        description = descriptive_choices(data)
        self.assertEqual(description['n'], 4)
        self.assertFalse(description['affects_conclusion'])
        self.assertEqual(description['classes']['flat'], dict(choice_count=2, correct=1,
            hit_rate=.5, choice_share=.5, true_count=1, true_share=.25, share_difference=.25))
        self.assertEqual(description['classes']['down']['share_difference'], -.25)
        self.assertIsNone(description['classes']['down']['hit_rate'])

    def test_day_bootstrap_is_paired_point_weighted_fixed_seed_and_order_independent(self):
        # Day A: one win; day B: three losses. Point estimate = (−2+6)/4 = 1,
        # not the zero obtained by averaging two day means. With day sampling,
        # three possible Brier differences are −2, +1, +2 (probability .25/.5/.25).
        jev = {'2024-01-01T09:30': point('2024-01-01T09:30', 'up', 'up')}
        base = {'2024-01-01T09:30': point('2024-01-01T09:30', 'up', 'flat')}
        for clock in ('09:30', '10:00', '10:30'):
            t = '2024-01-02T' + clock
            jev[t], base[t] = point(t, 'up', 'flat'), point(t, 'up', 'up')
        estimate = sum(jev[t].brier - base[t].brier for t in jev) / len(jev)
        self.assertEqual(estimate, 1)
        result = paired_bootstrap(jev, base)
        self.assertEqual(result['repetitions'], 2000)
        self.assertEqual(result['seed'], 20260927)
        self.assertEqual(result['trading_days'], 2)
        self.assertEqual(result['brier_difference_ci95'], [-2, 2])
        self.assertEqual(result['accuracy_difference_ci95'], [-1, 1])
        self.assertEqual(result, paired_bootstrap(dict(reversed(list(jev.items()))), base))

    def test_pairing_uses_same_draws_and_does_not_resample_methods_independently(self):
        points = {f'2024-01-0{i}T09:30': point(f'2024-01-0{i}T09:30', 'up', 'flat' if i % 2 else 'up')
                  for i in range(1, 5)}
        result = paired_bootstrap(points, dict(points))
        self.assertEqual(result['brier_difference_ci95'], [0, 0])
        self.assertEqual(result['accuracy_difference_ci95'], [0, 0])
        with self.assertRaisesRegex(DataError, 'unpaired_comparison'):
            paired_bootstrap(points, {})

    def test_unequal_day_sizes_change_interval_and_all_points_stay_together(self):
        left, right = {}, {}
        for day, size in enumerate((1, 3, 5, 7), 1):
            for minute in range(size):
                t = f'2024-01-0{day}T09:{minute:02d}'
                left[t] = point(t, 'up', 'flat' if day == 4 else 'up')
                right[t] = point(t, 'up', 'flat' if day == 1 else 'up')
        self.assertEqual(sum(left[t].brier - right[t].brier for t in left) / 16, .75)
        # Whole-day draws with point weighting give these percentile bounds.
        # Averaging the four day means or sampling 16 independent points differs.
        result = paired_bootstrap(left, right)
        self.assertEqual(result['brier_difference_ci95'], [-.75, 1.75])
        self.assertEqual(result['accuracy_difference_ci95'], [-.875, .375])

    def test_primary_brier_drives_conclusion_when_accuracy_is_worse(self):
        scored = {method: {} for method in ALL_METHODS}
        for day in range(1, 21):
            for minute in (0, 30):
                t = f'2024-01-{day:02d}T10:{minute:02d}'
                scored['jev'][t] = point(t, 'flat', 'up', {'up': .34, 'flat': .33, 'down': .33})
                for method in ALL_METHODS[1:]:
                    scored[method][t] = point(t, 'flat', 'flat' if minute else 'up')
        data = SplitScores('dev', '2024-01-01', '2024-01-20', 40, 40, 40,
            {t: 'flat' for t in scored['jev']}, scored,
            {method: metrics(values.values()) for method, values in scored.items()},
            tuple(sorted(scored['jev'])), True)
        result = split_report(data, best_development_baseline(data))['comparison']
        self.assertEqual(result['baseline'], 'always_flat')
        self.assertAlmostEqual(result['brier_difference'], -.3266)
        self.assertEqual(result['accuracy_difference'], -.5)
        self.assertEqual(result['statement'], 'jev 比 always_flat 好')

    def test_bootstrap_single_day_empty_and_percentile_interpolation(self):
        self.assertEqual(percentile([0, 10], .025), .25)
        self.assertEqual(percentile([0, 10], .975), 9.75)
        self.assertIsNone(paired_bootstrap({}, {})['brier_difference_ci95'])
        left, right = point('2024-01-01T09:30', 'up', 'up'), point('2024-01-01T09:30', 'up', 'flat')
        result = paired_bootstrap({left.t: left}, {right.t: right})
        self.assertEqual(result['brier_difference_ci95'], [-2, -2])
        self.assertEqual(result['accuracy_difference_ci95'], [1, 1])

    def test_four_conclusion_sentences_and_touching_zero(self):
        kwargs = dict(common_n=100, eligible_n=100, complete=True)
        self.assertEqual(conclusion('majority', -.1, [-.2, -.01], **kwargs), 'jev 比 majority 好')
        self.assertEqual(conclusion('majority', -.1, [-.2, .01], **kwargs), NO_EVIDENCE)
        self.assertEqual(conclusion('majority', -.1, [-.2, 0], **kwargs), NO_EVIDENCE)
        self.assertEqual(conclusion('majority', .1, [.01, .2], **kwargs), 'jev 比 majority 差')
        self.assertEqual(conclusion('majority', .1, [0, .2], **kwargs), NO_EVIDENCE)
        self.assertEqual(conclusion('majority', -.1, [-.2, -.01],
                                    common_n=94, eligible_n=100, complete=True), INCOMPLETE)
        self.assertEqual(conclusion('majority', -.1, [-.2, -.01],
                                    common_n=95, eligible_n=100, complete=True), 'jev 比 majority 好')
        self.assertEqual(conclusion('majority', -.1, [-.2, -.01],
                                    common_n=100, eligible_n=100, complete=False), INCOMPLETE)

    def test_conclusion_better(self):
        self.assertEqual(conclusion('majority', -.1, [-.2, -.01],
            common_n=100, eligible_n=100, complete=True), 'jev 比 majority 好')

    def test_conclusion_worse(self):
        self.assertEqual(conclusion('majority', .1, [.01, .2],
            common_n=100, eligible_n=100, complete=True), 'jev 比 majority 差')

    def test_conclusion_no_evidence(self):
        self.assertEqual(conclusion('majority', .01, [-.1, .1],
            common_n=100, eligible_n=100, complete=True), NO_EVIDENCE)

    def test_conclusion_incomplete_overrides_worse(self):
        self.assertEqual(conclusion('majority', .1, [.01, .2],
            common_n=94, eligible_n=100, complete=True), INCOMPLETE)
