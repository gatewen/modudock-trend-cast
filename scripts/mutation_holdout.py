#!/usr/local/bin/python3
"""Final-exam authorization, completion, quota and disclosure mutations."""
from pathlib import Path
import sys

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts import mutation_check

T = 'tests.test_run_holdout.HoldoutTests.'
REFUSED = T + 'test_experiment_two_refused_by_script_runners_and_both_reveal_paths'
COMPLETE = T + 'test_reveal_only_after_every_commit_keeps_development_baseline_and_prior_overlap'
INCOMPLETE = T + 'test_incomplete_traversal_corrupt_answer_or_missing_outcome_blocks_atomic_reveal'
MUTATIONS = [
    ('execute_guard_removed', 'scripts/run_holdout.py', [('not args.execute or ', '')],
     T + 'test_cli_requires_execute_split_experiment_and_enforces_ceiling_before_writer'),
    ('cli_experiment_guard_removed', 'scripts/run_holdout.py', [('args.experiment != 1 or ', '')],
     T + 'test_cli_requires_execute_split_experiment_and_enforces_ceiling_before_writer'),
    ('cli_ceiling_removed', 'scripts/run_holdout.py', [('or not 0 <= args.max_calls <= MAX_CALLS', '')],
     T + 'test_cli_requires_execute_split_experiment_and_enforces_ceiling_before_writer'),
    ('selected_experiment_guard_removed', 'back/experiment.py',
     [("if type(experiment_id) is not int or experiment_id != FINAL_EXPERIMENT or prompt_version != 'p1':", 'if False:')], REFUSED),
    ('jev_entry_guard_removed', 'back/jevcast.py',
     [("if split == 'holdout':\n            require_final_holdout(experiment_id)", 'if False:\n            require_final_holdout(experiment_id)')], REFUSED),
    ('baseline_entry_guard_removed', 'back/jobs.py',
     [("if split == 'holdout':\n            require_final_holdout(experiment_id)", 'if False:\n            require_final_holdout(experiment_id)')], REFUSED),
    ('labels_reveal_guard_removed', 'scripts/check_data.py',
     [("require_final_holdout(experiment['id'], experiment['prompt_version'])", 'pass')], REFUSED),
    ('development_baseline_check_removed', 'scripts/run_holdout.py',
     [('or best_development_baseline(development) != SELECTED_BASELINE', 'or False')],
     T + 'test_development_selection_change_stops_before_holdout_work'),
    ('final_check_callback_removed', 'scripts/run_holdout.py', [('check=require_complete', 'check=None')],
     T + 'test_missing_answers_quota_exhaustion_never_reveals_or_reports_holdout_numbers'),
    ('atomic_check_ignored', 'back/experiment.py', [('if check is not None:', 'if False:')], INCOMPLETE),
    ('missing_predictions_ignored', 'scripts/run_holdout.py', [('not any(missing.values())', 'True')], INCOMPLETE),
    ('missing_outcomes_ignored', 'scripts/run_holdout.py', [('not missing_outcomes and', 'True and')], INCOMPLETE),
    ('unfinished_traversal_ignored', 'scripts/run_holdout.py', [('all(traversed.values())', 'True')], INCOMPLETE),
    ('partial_scope_accepted', 'scripts/run_holdout.py', [("and last['full_split'] == 1", '')], INCOMPLETE),
    ('incorrect_outcome_accepted', 'scripts/run_holdout.py',
     [("(old['close_t'], old['close_end'], old['label']) != (point.close_t, outcome.close_end, outcome.label)", 'False')], INCOMPLETE),
    ('reveal_transaction_rolls_forward', 'back/store.py', [('self.db.rollback()', 'self.db.commit()')],
     T + 'test_reveal_write_failure_rolls_back_exposure_context_and_stays_locked'),
    ('prior_overlap_lost', 'back/experiment.py', [('overlap = holdout_overlap(store, experiment_id)', 'overlap = 0')], COMPLETE),
    ('baseline_reselected_on_holdout', 'back/report.py',
     [('split_report(held, baseline, development_ready=ready)', 'split_report(held, best_development_baseline(held), development_ready=ready)')], COMPLETE),
    ('quota_stops_committed_tail_traversal', 'back/jevcast.py',
     [('            t = next(handle.points, None)\n',
       "            if self.stop_dispatch is not None and self.stop_dispatch.is_set():\n                handle.exhausted = True\n                handle.stop_reason = 'call_limit'\n                break\n            t = next(handle.points, None)\n")],
     T + 'test_shared_budget_repairs_missing_answers_before_single_reveal'),
    ('repair_pass_removed', 'scripts/run_holdout.py',
     [("                status = writer.submit(state).result()\n                if client.budget.calls", "                status = writer.submit(state).result()\n                break\n                if client.budget.calls")],
     T + 'test_shared_budget_repairs_missing_answers_before_single_reveal'),
]

if __name__ == '__main__':
    mutation_check.MUTATIONS = MUTATIONS
    raise SystemExit(mutation_check.main())
