#!/usr/local/bin/python3
"""Round 2 mutations in temporary copies; synthetic fixtures, forbidden network."""
from pathlib import Path
import sys

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts import mutation_check

E='tests.test_evolution.EvolutionTests.'
R='tests.test_evolution_run.EvolutionRunTests.'
C='tests.test_evolution_run.EvolutionComparisonTests.'
M='back/evolution.py'
RUN='back/evolution_run.py'
MUTATIONS=[
    ('future_labels_allowed',M,[('if t + timedelta(minutes=30) > point.t:', 'if False:')],E+'test_future_random_labels_choices_and_volatility_do_not_change_outputs'),
    ('maturity_30_minutes_removed',M,[('t + timedelta(minutes=30) > point.t','t > point.t')],E+'test_exact_30_minute_boundary_and_current_truth_unavailable'),
    ('exact_30_boundary_excluded',M,[('t + timedelta(minutes=30) > point.t','t + timedelta(minutes=30) >= point.t')],E+'test_exact_30_minute_boundary_and_current_truth_unavailable'),
    ('nondev_labels_allowed',M,[("if plan.split_of(t.date().isoformat()) != 'dev':",'if False:')],E+'test_only_eligible_dev_observations_train_and_foreign_records_rejected'),
    ('unpredictable_labels_train',M,[('if not obs.predictable or not obs.scorable:', 'if not obs.scorable:')],E+'test_only_eligible_dev_observations_train_and_foreign_records_rejected'),
    ('unscorable_labels_train',M,[('if not obs.predictable or not obs.scorable:', 'if not obs.predictable:')],E+'test_only_eligible_dev_observations_train_and_foreign_records_rejected'),
    ('duplicate_labels_count_twice',M,[('if t in seen:', 'if False:')],E+'test_only_eligible_dev_observations_train_and_foreign_records_rejected'),
    ('cross_experiment_labels_train',M,[('obs.experiment_id != plan.experiment_id','False')],E+'test_only_eligible_dev_observations_train_and_foreign_records_rejected'),
    ('nondev_target_allowed',M,[("if plan.split_of(point.t.date().isoformat()) != 'dev':",'if False:')],E+'test_non_dev_points_and_foreign_points_refused_and_unpredictable_skipped'),
    ('29_samples_no_fallback',M,[('MIN_SAMPLES = 30','MIN_SAMPLES = 29')],E+'test_empty_and_29_samples_use_exact_majority_probabilities'),
    ('30_samples_still_fallback',M,[('if len(selected) < MIN_SAMPLES:', 'if len(selected) <= MIN_SAMPLES:')],E+'test_exactly_30_uses_group_only_and_add_one'),
    ('fallback_discards_learning',M,[('(r.observation for r in ready)', '()')],E+'test_empty_and_29_samples_use_exact_majority_probabilities'),
    ('clock_buckets_merged',M,[('if r.observation.t.time() == point.t.time()', 'if True')],E+'test_all_eight_clock_buckets_are_separate'),
    ('add_one_removed',M,[('(counts[label] + 1) / denominator','counts[label] / denominator')],E+'test_exactly_30_uses_group_only_and_add_one'),
    ('smoothing_denominator_wrong',M,[('sum(counts.values()) + 3','sum(counts.values()) + 1')],E+'test_exactly_30_uses_group_only_and_add_one'),
    ('ties_up_first',M,[('max(TIE_ORDER,','max(LABELS,')],E+'test_ties_flat_then_up_then_down_and_probabilities_sum_to_one'),
    ('future_bar_allowed',M,[("if local_time(bar['bar_end']) <= point.t",'if True')],E+'test_volatility_29_simple_returns_population_std_last30_visible_only'),
    ('all_visible_closes_in_volatility',M,[('for bar in bars[-30:]','for bar in bars')],E+'test_volatility_29_simple_returns_population_std_last30_visible_only'),
    ('29_closes_accepted',M,[('if len(bars) < 30:', 'if len(bars) < 29:')],E+'test_volatility_29_simple_returns_population_std_last30_visible_only'),
    ('sample_std_instead_of_population',M,[('return pstdev(returns)', 'return pstdev(returns) * math.sqrt(29/28)')],E+'test_volatility_29_simple_returns_population_std_last30_visible_only'),
    ('price_differences_not_returns',M,[('float(right / left - 1)','float(right - left)')],E+'test_volatility_29_simple_returns_population_std_last30_visible_only'),
    ('terciles_use_future_volatility',M,[('samples = [r for r in ready if r.volatility is not None]', 'samples = [r for r in self.records if r.volatility is not None]')],E+'test_vol_cuts_use_only_matured_points_and_recompute_past_groups'),
    ('tercile_interpolation_dropped',M,[('(percentile(values, 1 / 3), percentile(values, 2 / 3))','(sorted(values)[int((len(values)-1)/3)], sorted(values)[int(2*(len(values)-1)/3)])')],E+'test_vol_terciles_linear_equal_cuts_go_lower_and_group_smoothing'),
    ('tercile_ties_go_higher',M,[('from bisect import bisect_left','from bisect import bisect_right as bisect_left')],E+'test_vol_terciles_linear_equal_cuts_go_lower_and_group_smoothing'),
    ('vol_group_filter_removed',M,[('if bisect_left(cuts, r.volatility) == group','if True')],E+'test_vol_terciles_linear_equal_cuts_go_lower_and_group_smoothing'),
    ('threshold_uses_total_samples',M,[('if len(selected) < MIN_SAMPLES:', 'if len(ready) < MIN_SAMPLES:')],E+'test_vol_small_group_not_total_sample_count_and_missing_bars_fallback'),
    ('calibration_matrix_transposed',M,[('if r.jev_choice == jev_choice', 'if r.observation.label == jev_choice')],E+'test_calibration_uses_current_choice_rows_not_probabilities_or_truth_columns'),
    ('calibration_rows_merged',M,[('if r.jev_choice == jev_choice','if True')],E+'test_calibration_uses_current_choice_rows_not_probabilities_or_truth_columns'),
    ('nondev_experiment_cli_guard_removed',RUN,[("if type(experiment_id) is not int or experiment_id != 1 or split != 'dev':",'if False:')],R+'test_non_dev_or_other_experiment_refused_before_any_sql'),
    ('p2_allowed',RUN,[("row['prompt_version'] != 'p1'",'False')],R+'test_settings_must_be_selected_p1'),
    ('hidden_prediction_rows_read',RUN,[("args = (experiment_id, row['dev_start'], row['dev_end'])", "args = (experiment_id, row['dev_start'], row['hold_end'])")],R+'test_atomic_only_dev_three_methods_zero_http_and_rerun_no_changes'),
    ('network_guard_not_installed',RUN,[('with client.guard(), store.transaction():','with store.transaction():')],R+'test_network_attempt_during_work_is_blocked_and_rolls_back'),
    ('old_source_run_status_ignored',RUN,[("last['status'] == 'complete'",'True')],R+'test_incomplete_source_run_suppresses_promotion'),
    ('outcome_integrity_ignored',RUN,[("old is None or (old['close_t'], old['close_end'], old['label']) != (point.close_t, outcome.close_end, outcome.label)",'False')],R+'test_corrupt_sources_and_existing_predictions_refuse_without_overwriting'),
    ('comparison_uses_union',RUN,[('common = set.intersection(', 'common = set.union(')],C+'test_shared_eight_method_intersection_delta_direction_and_existing_bootstrap'),
    ('difference_sign_reversed',RUN,[("paired[method][t].brier - paired['majority'][t].brier", "paired['majority'][t].brier - paired[method][t].brier")],C+'test_shared_eight_method_intersection_delta_direction_and_existing_bootstrap'),
    ('touching_zero_qualifies',RUN,[('ci[1] < 0','ci[1] <= 0')],C+'test_significance_required_touching_zero_none_and_brier_not_accuracy_selection'),
    ('selection_uses_accuracy',RUN,[("key=lambda m: summaries[m]['brier']", "key=lambda m: -summaries[m]['accuracy']")],C+'test_significance_required_touching_zero_none_and_brier_not_accuracy_selection'),
    ('nonsignificant_winner_selected',RUN,[("qualified = [m for m in METHODS if comparisons[m]['qualifies']]",'qualified = list(METHODS)')],C+'test_significance_required_touching_zero_none_and_brier_not_accuracy_selection'),
    ('coverage_requirement_removed',RUN,[('len(common) * 100 >= eligible * 95','True')],C+'test_under_95_or_incomplete_no_conclusion_exact_95_allowed'),
    ('rollback_commits_partial_methods','back/store.py',[('self.db.rollback()','self.db.commit()')],R+'test_mid_write_failure_rolls_back_all_new_methods_and_runs'),
]

if __name__ == '__main__':
    mutation_check.MUTATIONS=MUTATIONS
    raise SystemExit(mutation_check.main())
