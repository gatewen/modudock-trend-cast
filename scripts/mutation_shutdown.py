#!/usr/local/bin/python3
from scripts import mutation_check
T='tests.test_shutdown.ShutdownTests.'
MUTATIONS=[
 ('runtime_drains_instead_of_aborting','back/runtime.py',[("self.writer.close(abort=True)","self.writer.close()")],T+'test_close_aborts_queued_news_and_running_job_cannot_write_after_release'),
 ('late_commit_guard_removed','back/db_lifecycle.py',[("self.gate.check();return self.raw.commit()","return self.raw.commit()")],T+'test_inflight_uncommitted_transaction_is_rolled_back_on_close'),
 ('queued_writes_not_cancelled','back/db_writer.py',[("pending.append(item[1])","pass")],T+'test_close_aborts_queued_news_and_running_job_cannot_write_after_release'),
 ('cancelled_summary_can_reopen','back/daily_forward_service.py',[("self.event.set();self.database.stop()","self.event.set()")],T+'test_cancelled_daily_summary_does_not_reopen_database_and_worker_is_joined'),
 ('sqlite_busy_wait_not_interruptible','back/db_lifecycle.py',[("PRAGMA busy_timeout=25","PRAGMA busy_timeout=1000")],T+'test_sqlite_external_lock_shutdown_has_no_late_commit'),
 ('read_connections_not_awaited','back/runtime.py',[("for gate in (self.database,self.daily_forward.database):","for gate in ():")],T+'test_read_constructor_and_connection_close_are_part_of_shutdown_barrier'),
 ('reader_gate_not_closed','back/runtime.py',[("        self.database.stop()\n        self.writer.close", "        # mutation\n        self.writer.close")],T+'test_read_constructor_and_connection_close_are_part_of_shutdown_barrier'),
 ('late_budget_write_allowed','back/daily_forward_client.py',[("def close(self):self.database.stop()","def close(self):pass")],T+'test_closed_budget_rejects_late_network_receipt_and_preserves_reserved_attempt'),
]
if __name__=='__main__':
    mutation_check.MUTATIONS=MUTATIONS
    raise SystemExit(mutation_check.main())
