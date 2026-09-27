#!/usr/local/bin/python3
"""Behavior mutations for development-only UI query boundaries and projection."""
from pathlib import Path
import sys
if __package__ in (None,''):sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts import mutation_check
P='back/daily_view.py';T='tests.test_daily_view.DailyViewTests.'
MUTATIONS=[
 ('date_guard_removed',P,[("if not first <= value <= last <= DEV_END:","if False:")],T+'test_reject_holdout_exposed_dates_invalid_horizon_and_write_parameters'),
 ('config_leaks_holdout_dates',P,[("return dict(common, **result)","return dict(common, config=c, **result)")],T+'test_all_exits_bounded_readonly_no_network_no_config_leak'),
 ('maturity_guard_removed',P,[("AND end_day BETWEEN day AND ?", "AND ? IS NOT NULL")],T+'test_maturity_outside_dev_is_not_scored_or_colored_and_future_rows_are_inert'),
 ('prediction_date_guard_removed',P,[("WHERE experiment_id=4 AND H=? AND day BETWEEN ? AND ? ORDER BY day,method", "WHERE experiment_id=4 AND H=? AND ? IS NOT NULL AND ? IS NOT NULL ORDER BY day,method")],T+'test_maturity_outside_dev_is_not_scored_or_colored_and_future_rows_are_inert'),
 ('corporate_reference_ignored',P,[("engine.reference(day, previous)","Fraction(previous['close'])")],T+'test_adjusted_chart_reference_and_ranges_end_at_development'),
 ('six_month_range_uses_three',P,[("months = 6 if span == '6m' else 12", "months = 3 if span == '6m' else 12")],T+'test_adjusted_chart_reference_and_ranges_end_at_development'),
 ('chart_only_one_method',P,[("for method in ('jev_ind', 'ind_logit'):","for method in ('jev_ind',):")],T+'test_markers_only_sampled_two_methods_with_boolean_correctness'),
 ('correctness_swapped',P,[("correct=bool(p.correct)","correct=not bool(p.correct)")],T+'test_markers_only_sampled_two_methods_with_boolean_correctness'),
 ('difference_sign_swapped',P,[("difference=comp['difference']", "difference=-comp['difference'] if comp['difference'] is not None else None")],T+'test_eighteen_methods_comparison_matches_existing_report_and_small_samples'),
 ('small_sample_guard_removed',P,[("small_sample=n<30", "small_sample=False")],T+'test_eighteen_methods_comparison_matches_existing_report_and_small_samples'),
 ('all_indicators_not_rendered',P,[("for name in INDICATORS:\n            state", "for name in INDICATORS[:-1]:\n            state")],T+'test_indicator_all_eleven_lagged_chips_and_maturity_bias'),
 ('cache_ignores_prediction_content',P,[("hashlib.sha256(canonical([outcomes, rows]).encode()).hexdigest()", "'constant'")],T+'test_cache_rechecks_source_digest_and_prediction_changes'),
 ('unused_holdout_lost',P,[("HOLD_MESSAGE = '未使用（沒有入圍者，保留給未來）'", "HOLD_MESSAGE = '已使用'")],T+'test_all_exits_bounded_readonly_no_network_no_config_leak'),
]
if __name__=='__main__':
    mutation_check.MUTATIONS=MUTATIONS
    raise SystemExit(mutation_check.main())
