#!/usr/local/bin/python3
"""Development CLI guard mutations, using only offline fixtures."""
from pathlib import Path
import sys

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts import mutation_check

T = 'tests.test_run_dev.RunDevTests.'
MUTATIONS = [
    ('execute_guard_removed', 'scripts/run_dev.py', [('if not args.execute:', 'if False:')],
     T + 'test_cli_without_execute_no_request_no_writer_no_changes'),
    ('local_replay_uses_holdout', 'scripts/run_dev.py',
     [("for t in replay.candidates('dev'):", "for t in replay.candidates('holdout'):")],
     T + 'test_only_dev_methods_outcomes_and_report_and_restart_zero_http'),
    ('jev_uses_holdout', 'scripts/run_dev.py',
     [("runner.start(experiment_id, split='dev')", "runner.start(experiment_id, split='holdout')")],
     T + 'test_only_dev_methods_outcomes_and_report_and_restart_zero_http'),
    ('call_limit_guard_removed', 'scripts/run_dev.py',
     [('if self.calls >= self.limit:', 'if False:')],
     T + 'test_atomic_call_limit_with_concurrent_permits'),
    ('retries_do_not_consume_quota', 'scripts/run_dev.py',
     [('    def consume(self, *, retry=False):\n', '    def consume(self, *, retry=False):\n        if retry:\n            return\n')],
     T + 'test_retries_consume_quota_and_terminal_failures_are_distinct'),
    ('per_method_sample_sizes_replace_intersection', 'scripts/run_dev.py',
     [("metrics[method] = {'n': len(common)", "metrics[method] = {'n': len(answers)")],
     T + 'test_all_five_methods_share_exact_same_intersection'),
    ('duplicate_jev_dispatched', 'back/jevcast.py', [('if exists:', 'if False:')],
     T + 'test_only_dev_methods_outcomes_and_report_and_restart_zero_http'),
    ('local_rollback_replaced_by_commit', 'back/store.py', [('self.db.rollback()', 'self.db.commit()')],
     T + 'test_outcomes_baselines_and_runs_commit_atomically'),
]

if __name__ == '__main__':
    mutation_check.MUTATIONS = MUTATIONS
    raise SystemExit(mutation_check.main())
