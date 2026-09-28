"""SPEC 16.1A: averaging, time isolation, pairing, qualification and budget."""
from scripts import mutation_check

E='back/daily_ensemble.py'
T='tests.test_daily_ensemble.'
R=T+'EnsembleReportTests.'
MUTATIONS=[
    ('average_only_majority',E,[('math.fsum(current[m].probabilities[label] for m in COMPONENTS) / 3',
                               "current['majority'].probabilities[label]")],T+'EnsembleTests.test_equal_probability_average_not_vote_or_renormalized_weights'),
    ('average_divide_by_two',E,[('for m in COMPONENTS) / 3','for m in COMPONENTS) / 2')],T+'EnsembleTests.test_equal_probability_average_not_vote_or_renormalized_weights'),
    ('tie_prefers_up','back/daily_config.py',[("TIE_ORDER = ('flat', 'up', 'down')","TIE_ORDER = ('up', 'down', 'flat')")],T+'EnsembleTests.test_ties_flat_then_up_then_down_and_invalid_probabilities'),
    ('read_future_prediction',E,[('AND H=? AND day=?','AND H=? AND day>=?')],R+'test_exact_day_horizon_experiment_no_outcome_access_or_future_rows'),
    ('read_other_horizon',E,[('AND H=? AND day=?','AND H>=? AND day=?')],R+'test_exact_day_horizon_experiment_no_outcome_access_or_future_rows'),
    ('allow_holdout_point',E,[('experiment_id != 4 or not DEV_START <= point.day <= DEV_END','experiment_id != 4')],R+'test_exact_day_horizon_experiment_no_outcome_access_or_future_rows'),
    ('train_with_future_labels','back/daily_models.py',[('if r.frame.index+H > point.index or r.end_day > point.day: continue','if False: continue')],T+'EnsembleTests.test_future_labels_and_features_do_not_change_walk_forward_ensemble'),
    ('ignore_component_hash',E,[("r['input_hash'] != point.digest or p.answer != r['choice']","p.answer != r['choice']")],R+'test_strict_negative_ci_and_metadata_validation'),
    ('ignore_stored_choice',E,[("r['input_hash'] != point.digest or p.answer != r['choice']","r['input_hash'] != point.digest")],R+'test_tampered_choice_and_outcome_rejected'),
    ('ignore_outcome_truth',E,[("r['label'] != expected[d].truth or r['end_day'] != expected[d].end_day","r['end_day'] != expected[d].end_day")],R+'test_tampered_choice_and_outcome_rejected'),
    ('incomplete_still_shortlist',E,[("complete and comparison['ci95']","comparison['ci95']")],R+'test_missing_component_or_outcome_stays_incomplete'),
    ('ci_zero_qualifies',E,[("comparison['ci95'][1] < 0","comparison['ci95'][1] <= 0")],R+'test_strict_negative_ci_and_metadata_validation'),
    ('ci_lower_bound_qualifies',E,[("comparison['ci95'][1] < 0","comparison['ci95'][0] < 0")],R+'test_strict_negative_ci_and_metadata_validation'),
    ('bootstrap_one_day','back/daily_score.py',[('range(0,len(calendar),20)','range(0,len(calendar),1)'),('calendar[offset:offset+20]','calendar[offset:offset+1]')],'tests.test_daily_research.DailyScoreTests.test_twenty_session_blocks_tail_pairing_and_seed'),
    ('bootstrap_reblocks_intersection','back/daily_score.py',[('blocks=[]','calendar=tuple(d for d in calendar if d in keys)\n    blocks=[]')],'tests.test_daily_research.DailyScoreTests.test_blocks_follow_full_calendar_not_filtered_intersection'),
    ('old_budget_cap','back/evolution_budget.py',[('LIMIT = 2222','LIMIT = 3000')],T+'CampaignBudgetTests.test_new_cap_keeps_existing_used_and_shared_forward_budget'),
]
if __name__=='__main__':
    mutation_check.MUTATIONS=MUTATIONS
    raise SystemExit(mutation_check.main())
