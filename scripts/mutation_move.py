#!/usr/local/bin/python3
"""p3 binary question, timestamp sampling, maturity, quotas and stage-A isolation."""
from pathlib import Path
import sys
if __package__ in (None, ''): sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts import mutation_check
T='tests.test_move.MoveTests.'
M='back/move.py'
MUTATIONS=[
 ('p3_inherits_base_rates','back/prompts.py',[("P2_INSTRUCTIONS.split('\\n\\n')[0] + '\\n\\n'", "P2_INSTRUCTIONS + '\\n\\n'")],T+'test_p3_actual_http_only_binary_glossary_and_no_private_fields'),
 ('p3_wrong_threshold','back/jevcast.py',[("f'上漲或下跌 {k}‰ 或更多（不論方向）'","f'上漲或下跌 {k+1}‰ 或更多（不論方向）'")],T+'test_p3_actual_http_only_binary_glossary_and_no_private_fields'),
 ('p3_three_probability_keys','back/jevcast.py',[("labels = ('move', 'still') if prompt_version == 'p3' else LABELS","labels = LABELS")],T+'test_binary_validation_shape_sum_max_model_all_invalid'),
 ('p3_max_guard_removed','back/jevcast.py',[("if values[choice] != max(values.values()):","if False:")],T+'test_binary_validation_shape_sum_max_model_all_invalid'),
 ('p3_sum_guard_removed','back/jevcast.py',[("if abs(sum(values.values()) - 1) > Fraction(1, 100):","if False:")],T+'test_binary_validation_shape_sum_max_model_all_invalid'),
 ('future_labels_used',M,[('if local_time(stamp) + timedelta(minutes=30) <= local_time(t)','if True')],T+'test_maturity_inclusive_smoothing_flat_exclusion_future_random_and_ties'),
 ('maturity_boundary_exclusive',M,[('timedelta(minutes=30) <= local_time(t)','timedelta(minutes=30) < local_time(t)')],T+'test_maturity_inclusive_smoothing_flat_exclusion_future_random_and_ties'),
 ('no_add_one',M,[('(up + 1) / (up + down + 2)','up / (up + down)')],T+'test_maturity_inclusive_smoothing_flat_exclusion_future_random_and_ties'),
 ('wrong_conversion_direction',M,[("answer.probabilities['move'] * u","answer.probabilities['move'] * (1-u)")],T+'test_maturity_inclusive_smoothing_flat_exclusion_future_random_and_ties'),
 ('sample_reads_labels',M,[("SELECT p.t FROM predictions p","SELECT p.t,o.label FROM predictions p")],T+'test_timestamp_only_fixed_sample_and_future_labels_do_not_change_it'),
 ('sample_wrong_seed',M,[('random.Random(SEED).sample(population, size)','random.Random(SEED+1).sample(population, size)')],T+'test_timestamp_only_fixed_sample_and_future_labels_do_not_change_it'),
 ('sample_holdout_allowed',M,[("(reference['dev_start'], reference['dev_end'])))","(reference['dev_start'], reference['hold_end'])))")],T+'test_timestamp_only_fixed_sample_and_future_labels_do_not_change_it'),
 ('sample_replacement_allowed',M,[('elif tuple(prior)[:-1] != values:','elif False:')],T+'test_timestamp_only_fixed_sample_and_future_labels_do_not_change_it'),
 ('settings_guard_removed',M,[('if row[key] != reference[key]:','if False:')],T+'test_exact_settings_before_http_and_sample_not_enough'),
 ('opt_in_ignored',M,[('if not execute:','if False:')],T+'test_no_execute_no_http_no_writes_and_cli_no_db_needed'),
 ('failed_subset_scored',M,[("if result['missing'] == 0:",'if True:')],T+'test_failure_no_selective_report_missing_baseline_abort_and_write_rollback'),
 ('quota_ignored','scripts/run_dev.py',[('if self.calls >= self.limit:','if False:')],T+'test_max_calls_retries_campaign_and_resume'),
 ('existing_answers_resent',M,[('if old is not None:','if False:')],T+'test_atomic_dev_only_no_reveal_rerun_zero_http_and_report_pairing'),
 ('original_binary_counts_not_verified',M,[("if raw is None or (raw['up_count'], raw['down_count']) != (up, down):","if raw is None:")],T+'test_atomic_dev_only_no_reveal_rerun_zero_http_and_report_pairing'),
 ('writes_commit_on_error','back/store.py',[('self.db.rollback()','self.db.commit()')],T+'test_failure_no_selective_report_missing_baseline_abort_and_write_rollback'),
 ('comparison_sign_reversed',M,[("summaries[METHOD]['brier']-summaries['vol_prior']['brier']","summaries['vol_prior']['brier']-summaries[METHOD]['brier']")],T+'test_atomic_dev_only_no_reveal_rerun_zero_http_and_report_pairing'),
 ('unrevealed_days_includes_revealed','back/evolution_forward.py',[("len(set(days) - set(revealed_days(store, experiment_id)))","len(days)")],'tests.test_evolution_forward.EvolutionForwardTests.test_unrevealed_day_metadata_excludes_already_revealed_days'),
]
if __name__=='__main__':
    mutation_check.MUTATIONS=MUTATIONS
    raise SystemExit(mutation_check.main())
