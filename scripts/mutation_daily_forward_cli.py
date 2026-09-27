#!/usr/local/bin/python3
from scripts import mutation_check
T='tests.test_daily_forward_cli.';C=T+'DailyCliTests.';L=T+'DailyLockTests.'
S='back/daily_forward_service.py';P='scripts/daily_forward.py';K='back/daily_lock.py'
MUTATIONS=[
 ('flock_removed',K,[("fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)","pass")],L+'test_nonblocking_exclusion_symlink_alias_release_and_stale_lease'),
 ('alias_lock_not_canonical',K,[("    path=Path(db_path).resolve()", "    path=Path(db_path)")],L+'test_nonblocking_exclusion_symlink_alias_release_and_stale_lease'),
 ('lock_file_unlinked',K,[("if fd is not None:os.close(fd)","if fd is not None:os.close(fd)\n        lock_path.unlink(missing_ok=True)")],L+'test_nonblocking_exclusion_symlink_alias_release_and_stale_lease'),
 ('lease_validation_removed',K,[("if self.fd is None or Path(db_path).resolve()!=self.db_path:","if False:")],L+'test_nonblocking_exclusion_symlink_alias_release_and_stale_lease'),
 ('cli_lock_before_writer_removed',P,[("import argparse", "from contextlib import nullcontext\nimport argparse"),("with forward_lock(db_path) as lease:","with nullcontext(None) as lease:")],C+'test_cli_and_shell_share_process_lock_and_persistent_reservation'),
 ('shell_lock_removed',S,[("from datetime import timedelta", "from contextlib import nullcontext\nfrom datetime import timedelta"),("with forward_lock(self.writer.path):return", "with nullcontext():return")],C+'test_shell_respects_cli_lock_before_any_db_work'),
 ('missing_key_guard_removed',P,[("if missing:return", "if False:return")],C+'test_missing_each_key_no_writer_no_network_no_traceback'),
 ('sync_not_requested',P,[("service.cycle(sync=True,lease=lease)","service.cycle(sync=False,lease=lease)")],C+'test_no_new_day_zero_jev_shared_sync_happens_first'),
 ('total_predictions_instead_of_new',S,[("new_predictions=after[0]-before[0]", "new_predictions=after[0]")],C+'test_cli_and_shell_share_process_lock_and_persistent_reservation'),
 ('http_counter_zero',S,[("jev_http_calls=(self.budget.calls if self.budget else 0)-calls", "jev_http_calls=0")],C+'test_exact_summary_counts_include_committed_predictions_and_maturity'),
 ('maturity_counter_zero',S,[("scored_outcomes=after[1]-before[1]", "scored_outcomes=0")],C+'test_exact_summary_counts_include_committed_predictions_and_maturity'),
 ('uncertain_reservation_retried',S,[("if state=='initial':state='uncertain'", "if state=='initial':state='retry'")],C+'test_record_reservation_survives_process_failure_without_http'),
 ('oneshot_background_worker_started',S,[("if autostart:", "if True:")],C+'test_missing_db_not_created_and_oneshot_no_background_thread'),
 ('raw_exception_in_summary',S,[("code=str(error) if str(error) in SAFE else sync_reason(error)\n            self._emit", "code=str(error)\n            self._emit")],C+'test_failure_safe_one_line_and_lock_released'),
]
if __name__=='__main__':
    mutation_check.MUTATIONS=MUTATIONS
    raise SystemExit(mutation_check.main())
