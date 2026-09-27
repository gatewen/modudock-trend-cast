#!/usr/local/bin/python3
from scripts import mutation_check
P='back/news_forward.py';S='back/daily_forward_service.py';B='back/daily_forward_client.py';V='back/daily_forward_view.py'
T='tests.test_news_forward.NewsForwardTests.'
MUTATIONS=[
 ('p6_indicators_dropped',P,[("body['state']['news']=projection(digest)","body['state'].pop('indicators',None);body['state']['news']=projection(digest)")],T+'test_complete_p6_projection_same_day_only_and_no_identifiers'),
 ('news_metadata_sent',P,[("value=copy.deepcopy({k:digest[k] for k in NEWS_FIELDS})","value=copy.deepcopy(digest)")],T+'test_complete_p6_projection_same_day_only_and_no_identifiers'),
 ('news_instructions_missing',P,[("q['instructions']+='\\n\\n'+INSTRUCTIONS","q['instructions']+=''")],T+'test_complete_p6_projection_same_day_only_and_no_identifiers'),
 ('numeric_theme_redaction_removed',P,[("re.sub(r'\\d+(?:[.,/\\-]\\d+)*','〔數字略〕',theme['name'])","theme['name']")],T+'test_free_text_numeric_identifiers_dates_prices_redacted'),
 ('latest_instead_of_cutoff',P,[("snapshot=news.snapshot(store,day)\n        body=","snapshot=json.loads(store.db.execute('SELECT payload_json FROM news_digests WHERE id=1').fetchone()[0])\n        body=")],T+'test_complete_p6_projection_same_day_only_and_no_identifiers'),
 ('source_snapshot_not_checked',P,[("row['snapshot_json']!=canonical(snapshot)","False")],T+'test_snapshot_source_change_rejected'),
 ('policy_change_allowed',P,[("row['policy_hash']!=sha(policy(json.loads(base['body_json']),base['model_hash']))","False")],T+'test_forward_only_frozen_policy_and_input_reject_tampering'),
 ('body_change_allowed',P,[("row['body_json']!=canonical(request_body(json.loads(base['body_json']),snapshot))","False"),("row['body_hash']!=hashlib.sha256(row['body_json'].encode()).hexdigest()","False")],T+'test_forward_only_frozen_policy_and_input_reject_tampering'),
 ('reservation_guard_removed',P,[("if row['jev_state']!='reserved':raise DataError('forward_request_not_reserved')","if False:raise DataError('forward_request_not_reserved')")],T+'test_atomic_three_answers_reserved_before_commit_and_maturity'),
 ('only_two_answers_saved',P,[("for H,a in validated.answers.items():","for H,a in list(validated.answers.items())[:2]:")],T+'test_atomic_three_answers_reserved_before_commit_and_maturity'),
 ('done_claim_allowed',P,[("if row is None or row['jev_state'] not in ('pending','retry_wait'):return None","if row is None:return None")],T+'test_complete_p6_projection_same_day_only_and_no_identifiers'),
 ('p5_not_scheduled',S,[("self._run_request(day,'jev_news')","pass")],T+'test_actual_http_payload_one_day_once_and_restart'),
 ('p5_budget_shares_p6_day',B,[("self.table='daily_forward_http' if method=='jev_ind' else 'daily_forward_news_http'","self.table='daily_forward_http'")],T+'test_news_retries_separate_from_p6_and_all_charged'),
 ('news_charge_removed',B,[("db.execute('UPDATE budget SET used=used+1 WHERE campaign=?',(CAMPAIGN,))","pass")],T+'test_news_retries_separate_from_p6_and_all_charged'),
 ('news_retry_deadline_inclusive',B,[("self.now()<cutoff","self.now()<=cutoff")],T+'test_news_retry_restart_deadline_and_uncertain_failure'),
 ('news_59_days_conclusion_allowed',V,[("comp['n']>=60","comp['n']>=59")],'tests.test_news_forward.NewsComparisonTests.test_both_intersections_20_day_blocks_60_matured_minimum'),
 ('one_comparison_dropped',V,[("for baseline in ('jev_ind','majority'):","for baseline in ('majority',):")],'tests.test_news_forward.NewsComparisonTests.test_both_intersections_20_day_blocks_60_matured_minimum'),
]
if __name__=='__main__':
    mutation_check.MUTATIONS=MUTATIONS
    raise SystemExit(mutation_check.main())
