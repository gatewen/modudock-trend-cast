#!/usr/local/bin/python3
"""Each removed guard must kill its targeted behavioral test."""
from scripts import mutation_check
T='tests.test_daily_forward.'
P='back/daily_forward.py';B='back/daily_forward_client.py';S='back/daily_forward_service.py';V='back/daily_forward_view.py'
C=T+'ForwardCoreTests.';R=T+'ForwardRetryTests.';W=T+'ForwardServiceTests.'
MUTATIONS=[
 ('freeze_date_bypassed',P,[("start=parsed(row['created_at']).date().isoformat();end=closed_end(now)","start='2026-09-23';end=closed_end(now)")],C+'test_candidates_freeze_holiday_and_1630_boundary'),
 ('1630_gate_removed',P,[("if now.time()<time(16,30):day-=timedelta(days=1)","if False:day-=timedelta(days=1)")],C+'test_candidates_freeze_holiday_and_1630_boundary'),
 ('next_0900_inclusive',P,[("parsed(recorded_at)<deadline", "parsed(recorded_at)<=deadline")],C+'test_original_recorded_at_next_session_strict_boundary_and_unknown'),
 ('unknown_calendar_assumed_ontime',P,[("if deadline is None:return 'unconfirmed'", "if deadline is None:return 'ontime'")],C+'test_original_recorded_at_next_session_strict_boundary_and_unknown'),
 ('timing_recomputed_from_now',P,[("timing(store,day,p['recorded_at'])", "timing(store,day,stamp(now))")],C+'test_original_recorded_at_next_session_strict_boundary_and_unknown'),
 ('feature_reads_future',P,[("end=day,symbol=c['symbol']", "end='2026-10-23',symbol=c['symbol']")],C+'test_future_and_same_day_chips_cannot_change_input_answers_or_predictability'),
 ('chip_lag_removed','back/daily_indicators.py',[("available = calendar[:i]", "available = calendar[:i+1]")],C+'test_future_and_same_day_chips_cannot_change_input_answers_or_predictability'),
 ('frozen_model_guard_removed',P,[("old['model_hash']!=sha(model)","False")],C+'test_changed_methods_prompt_and_model_rejected'),
 ('questions_not_pinned',P,[("plan.get('questions')!=questions(c)","False")],C+'test_changed_methods_prompt_and_model_rejected'),
 ('reservation_before_commit_removed',P,[("row['jev_state']!='reserved'", "False")],C+'test_reservation_atomic_three_answers_and_commit_rollback'),
 ('reserve_done_allowed',P,[("if row['jev_state'] not in ('pending','retry_wait'):return None", "if False:return None")],C+'test_reservation_atomic_three_answers_and_commit_rollback'),
 ('only_two_answers_saved',P,[("for H,a in validated.answers.items():", "for H,a in list(validated.answers.items())[:2]:")],C+'test_reservation_atomic_three_answers_and_commit_rollback'),
 ('unclosed_maturity_allowed',P,[("end_limit=end", "end_limit='2026-10-23'")],C+'test_exact_maturity_and_score_only_post_freeze'),
 ('label_forced_flat',P,[("label(result.adjusted_return,threshold)","'flat'")],C+'test_exact_maturity_and_score_only_post_freeze'),
 ('retry_limit_increased',B,[("previous['attempt']>=3", "previous['attempt']>=4")],R+'test_only_429_529_three_across_restart_and_all_charged'),
 ('unknown_status_retried',B,[("if previous['status'] not in (429,529):return 'uncertain'", "if False:return 'uncertain'")],R+'test_only_429_529_three_across_restart_and_all_charged'),
 ('retry_deadline_inclusive',B,[("self.now()<cutoff", "self.now()<=cutoff")],R+'test_deadline_strict_and_unknown_does_not_guess_holiday'),
 ('unknown_deadline_guessed',B,[("else 'wait_calendar'", "else 'retry'")],R+'test_deadline_strict_and_unknown_does_not_guess_holiday'),
 ('budget_charge_removed',B,[("db.execute('UPDATE budget SET used=used+1 WHERE campaign=?',(CAMPAIGN,))", "pass")],R+'test_only_429_529_three_across_restart_and_all_charged'),
 ('campaign_cap_removed',B,[("if used>=LIMIT:","if False:")],R+'test_campaign_cap_and_no_reimbursement'),
 ('restart_backoff_removed',B,[("if delay:","if False:")],R+'test_restart_transport_backoff_then_success_all_attempts_charged'),
 ('post_sync_forecast_skipped',S,[("days=self._read(lambda s:candidates(s,row,self.now()))", "days=()")],W+'test_incremental_sync_then_auto_generate_with_fake_sources'),
 ('startup_worker_not_triggered','back/runtime.py',[("        self.daily_forward.trigger()", "        pass")],T+'ForwardRuntimeTests.test_startup_and_each_terminal_sync_schedule_worker'),
 ('sync_worker_not_triggered','back/runtime.py',[("            self.daily_forward.trigger(sync=True)", "            pass")],T+'ForwardRuntimeTests.test_startup_and_each_terminal_sync_schedule_worker'),
 ('small_sample_conclusion_allowed',V,[("complete=comp['blocks']>=2", "complete=comp['blocks']>=1")],C+'test_exact_maturity_and_score_only_post_freeze'),
 ('missing_coverage_hidden',V,[("missing=len(origins-present)", "missing=0")],C+'test_view_empty_forbidden_inputs_and_missing_coverage'),
]
if __name__=='__main__':
    mutation_check.MUTATIONS=MUTATIONS
    raise SystemExit(mutation_check.main())
