#!/usr/local/bin/python3
"""Budget durability, three-answer validation, atomic persistence and dev isolation."""
from pathlib import Path
import sys
if __package__ in (None,''):sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts import mutation_check
C='back/daily_jev_client.py';R='back/daily_jev.py';T='tests.test_daily_jev.'
MUTATIONS=[
 ('round_650_guard_removed',C,[('if count>=ROUND_CALL_LIMIT:','if False:')],T+'DailyClientTests.test_650_round_and_3000_campaign_limits_are_atomic'),
 ('campaign_guard_removed',C,[('if used>=LIMIT:','if False:')],T+'DailyClientTests.test_650_round_and_3000_campaign_limits_are_atomic'),
 ('campaign_not_charged',C,[('SET used=used+1','SET used=used')],T+'DailyClientTests.test_actual_http_three_questions_and_no_absolute_chip_units'),
 ('per_run_limit_removed',C,[('if self.calls>=self.max_calls:','if False:')],T+'DailyClientTests.test_retry_quota_durable_and_no_double_campaign_charge'),
 ('retry_not_charged',C,[('self.local.seq=self.budget.reserve(self.local.day)','self.local.seq=self.budget.reserve(self.local.day) if self.local.seq is None else self.local.seq'),('self._check(cancel);self.local.seq=None;http_start=self._clock()','self._check(cancel);http_start=self._clock()')],T+'DailyClientTests.test_retry_quota_durable_and_no_double_campaign_charge'),
 ('only_two_answers_validated',C,[('for H in HORIZONS:\n        one=','for H in (3,7):\n        one=')],T+'DailyClientTests.test_all_three_validate_model_shape_probabilities_choice_and_sum'),
 ('model_not_checked',C,[("if 'model' in payload:one['model']=payload['model']","if False:one['model']=payload['model']")],T+'DailyClientTests.test_all_three_validate_model_shape_probabilities_choice_and_sum'),
 ('auth_latch_removed',C,[('if response.status in (401,403):_AUTH_DISABLED.set()','if False:_AUTH_DISABLED.set()')],T+'DailyClientTests.test_auth_closes_client_cancel_zero_http_invalid_answer_not_complete'),
 ('retry_disabled',C,[('response.status in (429,529) and attempt<2','False')],T+'DailyClientTests.test_retry_quota_durable_and_no_double_campaign_charge'),
 ('skip_completed_removed',R,[("if r['status']!='done':","if True:")],T+'DailyRunnerTests.test_run_resume_zero_http_and_baseline_runner_unchanged'),
 ('commit_rollback_removed','back/daily_store.py',[('self.db.rollback()','self.db.commit()')],T+'DailyRunnerTests.test_atomic_three_answer_commit_cancel_and_rollback'),
 ('cancel_guard_removed',R,[("if cancel.is_set():raise ClientError('cancelled')","if False:raise ClientError('cancelled')")],T+'DailyRunnerTests.test_atomic_three_answer_commit_cancel_and_rollback'),
 ('holdout_run_allowed',R,[("experiment_id!=4 or split!='dev'","experiment_id!=4")],T+'DailyRunnerTests.test_plan_body_is_immutable_and_only_dev'),
]
# Two identical cancel checks are deliberately removed together, with one unique
# contiguous function anchor, so the common mutation harness stays strict.
for i,(name,path,replacements,test) in enumerate(MUTATIONS):
 if name=='cancel_guard_removed':
  text=(Path(__file__).resolve().parents[1]/path).read_text()
  first=text.index('def commit_answer(');last=text.index('\n\ndef run_plan(',first)
  original=text[first:last]
  MUTATIONS[i]=(name,path,[(original,original.replace("if cancel.is_set():raise ClientError('cancelled')","if False:raise ClientError('cancelled')"))],test)
if __name__=='__main__':
 mutation_check.MUTATIONS=MUTATIONS
 raise SystemExit(mutation_check.main())
