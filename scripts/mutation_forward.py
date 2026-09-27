#!/usr/local/bin/python3
"""Prospective-cohort honesty, disclosure and durable-budget mutations."""
from pathlib import Path
import sys

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts import mutation_check

T = 'tests.test_forward.ForwardTests.'
PRIVATE = T + 'test_zero_answers_refuses_reveal_and_all_unrevealed_exits_are_constant'
RUN = T + 'test_five_methods_p1_payload_majority_frozen_and_explicit_reveal'
FROZEN = T + 'test_settings_and_data_are_frozen_before_replay'
MUTATIONS = [
    ('exposure_filter_removed', 'back/forward.py', [('if day in exposed:', 'if False:')],
     T + 'test_dates_only_excludes_exposed_and_holdout_without_reading_prices'),
    ('holdout_end_admitted', 'back/forward.py', [('AND day>? AND day<=?', 'AND day>=? AND day<=?')],
     T + 'test_dates_only_excludes_exposed_and_holdout_without_reading_prices'),
    ('closing_time_guard_removed', 'back/forward.py',
     [("if now < datetime.fromisoformat(day + 'T13:30:00').replace(tzinfo=TAIPEI):", 'if False:')],
     T + 'test_only_closed_days_but_intraday_gaps_use_existing_predictability_rules'),
    ('closing_data_guard_removed', 'back/forward.py', [("if not observed or ('13:30'", "if False and ('13:30'")],
     T + 'test_only_closed_days_but_intraday_gaps_use_existing_predictability_rules'),
    ('threshold_can_change', 'back/forward.py', [("or row['threshold_permille'] != 3", 'or False')], FROZEN),
    ('prompt_can_change', 'back/forward.py', [("or row['prompt_version'] != 'p1'", 'or False')], FROZEN),
    ('forward_digest_ignored', 'back/forward.py',
     [("saved['data_digest'] != source_digest(store, row, saved['warmup_start'], day)", 'False')], FROZEN),
    ('forward_not_frozen_against_sync', 'back/store.py',
     [("WHERE json_extract(e.config_json,'$.symbol')=?", "WHERE 0 AND json_extract(e.config_json,'$.symbol')=?")], FROZEN),
    ('own_reveals_drop_from_cohort', 'back/forward.py', [('if day not in own_revealed:', 'if True:')], RUN),
    ('exposure_after_admission_ignored', 'back/forward.py', [("if exposed_days(store, '2330', unopened):", 'if False:')],
     T + 'test_exposure_after_admission_prevents_reveal_and_is_recorded_on_next_run'),
    ('reveal_completion_check_removed', 'back/forward.py', [('        require_complete(store, row, days)', '        pass')], PRIVATE),
    ('invalid_prediction_accepted', 'back/forward.py', [('prediction_answer(record, point, row[\'model\'])', 'pass')],
     T + 'test_missing_or_invalid_answer_and_unfinished_run_do_not_reveal'),
    ('unfinished_run_accepted', 'back/forward.py', [("latest['status'] != 'complete'", 'False')],
     T + 'test_missing_or_invalid_answer_and_unfinished_run_do_not_reveal'),
    ('forward_reveal_not_tagged', 'back/forward.py', [("VALUES (?,?,?,?,?,'forward')", "VALUES (?,?,?,?,?,'holdout')")], RUN),
    ('forward_report_read_before_permission', 'back/report.py', [('            if forward_days:', '            if True:')], PRIVATE),
    ('forward_score_reader_guard_removed', 'back/score.py', [('if not authorized:', 'if False:')], PRIVATE),
    ('forward_quality_stats_exposed', 'scripts/check_data.py', [("cutoff = experiment['hold_end'] if experiment is not None else '9999-12-31'", "cutoff = '9999-12-31'")], PRIVATE),
    ('new_failures_contaminate_old_report', 'back/score.py', [("if r['day'] in days)", 'if True)')],
     T + 'test_new_day_extends_cohort_but_unopened_work_does_not_change_revealed_report'),
    ('incomplete_development_allows_forward_conclusion', 'back/report.py',
     [('and len(development.common) * 100 >= len(development.eligible) * 95))', 'and True))')],
     T + 'test_incomplete_development_selection_suppresses_forward_conclusion'),
    ('majority_forward_warmup_removed', 'back/jobs.py', [("if handle.split in ('holdout', 'forward') else", "if handle.split == 'holdout' else")], RUN),
    ('forward_progress_counts_exposed', 'back/runtime.py', [("if progress.get('split') == 'forward':", 'if False:')],
     T + 'test_runtime_requires_confirmation_rejects_overrides_and_hides_forward_progress'),
    ('forward_setting_overrides_allowed', 'back/runtime.py',
     [("if set(body) - {'op', 'experiment_id', 'confirmed', 'request_id'}:", 'if False:')],
     T + 'test_runtime_requires_confirmation_rejects_overrides_and_hides_forward_progress'),
    ('campaign_limit_removed', 'back/evolution_budget.py', [('if used >= LIMIT:', 'if False:')],
     T + 'test_budget_is_durable_atomic_and_counts_retries_before_http'),
    ('campaign_counter_not_incremented', 'back/evolution_budget.py', [('SET used=used+1', 'SET used=used')],
     T + 'test_budget_is_durable_atomic_and_counts_retries_before_http'),
    ('real_transport_budget_bypassed', 'back/jevcast.py',
     [('if self._opener is None or self._campaign_budget is not None:', 'if self._campaign_budget is not None:')],
     T + 'test_default_transport_budget_cannot_be_bypassed_by_existing_cli_hook'),
]

if __name__ == '__main__':
    mutation_check.MUTATIONS = MUTATIONS
    raise SystemExit(mutation_check.main())
