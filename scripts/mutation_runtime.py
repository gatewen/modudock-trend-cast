#!/usr/local/bin/python3
"""Shell/lifecycle/replay integration mutations, synthetic fixtures only."""
from pathlib import Path
import sys

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts import mutation_check

P = 'tests.test_protocol.ProtocolTests.'
R = 'tests.test_runtime.RuntimeTests.'
J = 'tests.test_jobs.JobTests.'
MUTATIONS = [
    ('seq_mismatch_guard_removed', 'back/trendcast.py', [("elif packet['seq'] != seq:", 'elif False:')],
     P + 'test_real_process_seq_lifecycle_invalid_messages_and_no_business_before_up'),
    ('bool_accepted_as_seq', 'back/protocol.py', [('type(value) is int and', 'isinstance(value, int) and')],
     P + 'test_real_process_seq_lifecycle_invalid_messages_and_no_business_before_up'),
    ('outgoing_seq_not_echoed', 'back/trendcast.py', [("outbox.put({'t': 'ready', 'seq': seq})", "outbox.put({'t': 'ready', 'seq': 1})")],
     P + 'test_real_process_seq_lifecycle_invalid_messages_and_no_business_before_up'),
    ('business_allowed_before_up', 'back/trendcast.py', [("elif kind == 'msg' and running:", "elif kind == 'msg' and ready:")],
     P + 'test_real_process_seq_lifecycle_invalid_messages_and_no_business_before_up'),
    ('ready_before_hello', 'back/trendcast.py', [('outbox = outbox_factory(stdout)', "outbox = outbox_factory(stdout)\n    outbox.put({'t': 'ready', 'seq': 1})")],
     P + 'test_real_process_seq_lifecycle_invalid_messages_and_no_business_before_up'),
    ('preflight_fail_before_hello', 'back/trendcast.py', [('seq = None\n', "seq = None\n    if error:\n        return shutdown(outbox, {'t': 'fail', 'seq': 1, 'reason': error}, 1)\n")],
     P + 'test_preflight_fail_only_after_hello_and_flush_then_exit'),
    ('shutdown_waits_too_long_on_stdout', 'back/protocol.py', [('terminal_sent.wait(timeout)', 'terminal_sent.wait(5)')],
     P + 'test_stdout_not_drained_still_exits_under_one_second'),
    ('control_waits_for_network_workers', 'back/runtime.py', [('self.jev.close()', 'self.jev.close()\n        for worker in self.jev.workers: worker.join(5)')],
     P + 'test_network_gate_bye_discards_late_results_and_preserves_commits_for_restart'),
    ('network_workers_not_daemon', 'back/jevcast.py', [("name=f'jev-{i}', daemon=True", "name=f'jev-{i}', daemon=False")],
     P + 'test_network_gate_bye_discards_late_results_and_preserves_commits_for_restart'),
    ('late_outbox_admission_guard_removed', 'back/protocol.py', [('if self.closed:\n                return False', 'if False:\n                return False')],
     P + 'test_outbox_concurrent_producers_never_interleave_json_or_write_after_done'),
    ('queued_business_retained_on_close', 'back/protocol.py', [("if item[0]['t'] != 'msg':", 'if True:')],
     P + 'test_writer_gate_terminal_drops_queued_business_and_rejects_late_producer'),
    ('progress_coalescing_removed', 'back/protocol.py', [("if packet.get('t') == 'msg' and packet.get('body', {}).get('op') == 'status':", 'if False:')],
     P + 'test_outbox_coalesces_progress_so_terminal_status_is_not_dropped'),
    ('packet_cap_doubled', 'back/protocol.py', [('MAX_PACKET = 900 * 1024', 'MAX_PACKET = 1800 * 1024')],
     P + 'test_real_day_packet_above_900_kib_is_replaced_by_safe_error'),
    ('local_cancel_generation_guard_removed', 'back/jobs.py',
     [('return self._active is handle and not handle.cancel_event.is_set()\n\n    def _queue', 'return self._active is handle\n\n    def _queue')],
     J + 'test_cancel_during_point_discards_it_and_new_generation_resumes'),
    ('baseline_repredicts_existing_point', 'back/jobs.py', [('if point.predictable and old is None else None', 'if point.predictable else None')],
     J + 'test_all_baselines_persist_outcomes_after_commit_and_resume_only_missing'),
    ('holdout_majority_warmup_removed', 'back/jobs.py', [("iter(handle.engine.candidates('dev')) if handle.split in ('holdout', 'forward') else iter(())", "iter(handle.engine.candidates('dev')) if handle.split == 'forward' else iter(())")],
     J + 'test_holdout_majority_uses_all_eligible_development_labels_but_never_holdout_labels'),
    ('baseline_outcomes_not_committed', 'back/jobs.py', [('persist_outcome(store, point, outcome)', 'pass')],
     J + 'test_all_baselines_persist_outcomes_after_commit_and_resume_only_missing'),
    ('jev_outcomes_not_committed', 'back/jevcast.py', [('persist_outcome(store, point, handle.replay.outcome(point))', 'pass')],
     P + 'test_network_gate_bye_discards_late_results_and_preserves_commits_for_restart'),
    ('sync_late_generation_guard_removed', 'back/jobs.py',
     [('return self._active is handle and not handle.cancel_event.is_set()\n\n    def _write', 'return self._active is handle\n\n    def _write')],
     J + 'test_sync_network_off_writer_commits_on_writer_and_late_cancelled_result_discarded'),
    ('sync_network_thread_writes_sqlite', 'back/jobs.py',
     [('from .store import now_string', 'from .store import Store, now_string'),
      ('return self.writer.submit(guarded).result()', 'return guarded(Store(self.writer.path))')],
     J + 'test_sync_network_off_writer_commits_on_writer_and_late_cancelled_result_discarded'),
    ('cancel_also_stops_sync', 'back/runtime.py', [('self.baselines.cancel()', 'self.baselines.cancel()\n                self.sync.close()')],
     R + 'test_sync_and_replay_can_coexist_cancel_preserves_sync_new_experiment_refused'),
    ('sync_also_cancels_replay', 'back/runtime.py', [('handle = self.sync.start(symbol=self.symbol)', 'self.jev.cancel()\n                handle = self.sync.start(symbol=self.symbol)')],
     R + 'test_sync_and_replay_can_coexist_cancel_preserves_sync_new_experiment_refused'),
    ('create_admission_reservation_removed', 'back/runtime.py', [("reservation = self.gate.claim('create')\n                result = self.writer.submit(lambda store: self._create", 'reservation = None\n                result = self.writer.submit(lambda store: self._create')],
     R + 'test_creation_reserves_gate_before_db_queue_and_rejects_duplicate_or_replay'),
    ('creation_releases_before_experiment_binding', 'back/experiment.py', [('if owned:', 'if True:')],
     R + 'test_creation_reserves_gate_before_db_queue_and_rejects_duplicate_or_replay'),
    ('late_read_generation_guard_removed', 'back/runtime.py',
     [('if self.closed or generation != self._view_generation:\n                        continue', 'if self.closed:\n                        continue')],
     R + 'test_new_experiment_changes_settings_and_discards_already_read_old_report'),
    ('hidden_replay_progress_leaked', 'back/runtime.py',
     [("if progress.get('split') == 'holdout' and self._metadata.get('holdout', {}).get('state') != 'revealed':", 'if False:')],
     R + 'test_holdout_run_progress_and_all_exits_hide_outcomes_until_explicit_reveal'),
    ('stale_experiment_guard_removed', 'back/runtime.py', [("if body.get('experiment_id', current) != current:", 'if False:')],
     R + 'test_reveal_requires_confirmation_current_experiment_and_successful_commit'),
    ('reveal_confirmation_guard_removed', 'back/runtime.py',
     [("if body.get('confirmed') is not True:\n                    raise DataError('confirmation_required')\n                result = self.writer.submit(lambda store: reveal_holdout", 'if False:\n                    raise DataError(\'confirmation_required\')\n                result = self.writer.submit(lambda store: reveal_holdout')],
     R + 'test_reveal_requires_confirmation_current_experiment_and_successful_commit'),
    ('auth_disabled_jev_dispatched', 'back/runtime.py',
     [("if method == 'jev':\n                    state = key_state()['typesafe']\n                    if state != 'available':", "if method == 'jev':\n                    state = key_state()['typesafe']\n                    if False:")],
     R + 'test_missing_and_invalid_keys_block_only_corresponding_operation'),
    ('automatic_fugle_sync_removed', 'back/runtime.py', [("if key_state()['fugle'] == 'available':", 'if False:')],
     R + 'test_enabled_fugle_auto_syncs_on_up_and_jev_still_needs_explicit_run'),
    ('old_sync_progress_accepted', 'back/runtime.py', [("if progress['generation'] < self._sync.get('generation', 0):", 'if False:')],
     R + 'test_old_sync_and_replay_progress_generations_cannot_replace_current_status'),
    ('old_replay_progress_accepted', 'back/runtime.py', [("if progress['generation'] < self._replay.get('generation', 0):", 'if False:')],
     R + 'test_old_sync_and_replay_progress_generations_cannot_replace_current_status'),
]

if __name__ == '__main__':
    mutation_check.MUTATIONS = MUTATIONS
    raise SystemExit(mutation_check.main())
