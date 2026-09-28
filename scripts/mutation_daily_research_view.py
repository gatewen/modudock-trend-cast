from scripts import mutation_check as runner
P='back/daily_view.py';T='tests.test_daily_view.DailyViewTests.'
runner.MUTATIONS=[
 ('holdout_gate_removed',P,[("if not revealed(store):", "if False:")],T+'test_holdout_locked_projection_has_no_results_and_never_invokes_report'),
 ('holdout_projection_wrong_horizon',P,[("result['descriptive'][str(H)].items()", "result['descriptive']['3'].items()")],'tests.test_daily_holdout.HoldoutTests.test_complete_report_only_three_primary_all_others_descriptive'),
 ('extension_mean_replaced',P,[("p = average_current(current)", "p = current['majority']")],T+'test_extension_ensemble_matches_saved_current_probabilities_and_partial_not_shortlisted'),
 ('market_cache_ignores_results',P,[(".hexdigest(), extra_hash)", ".hexdigest(), 'constant')")],'tests.test_market.MarketPersistenceTests.test_daily_view_market_projection_matches_report_and_cache_rechecks_content'),
 ('market_rows_hidden',P,[("extra[method] = {d:ScoredPoint", "extra[method] = {} if True else {d:ScoredPoint")],'tests.test_market.MarketPersistenceTests.test_daily_view_market_projection_matches_report_and_cache_rechecks_content'),
]
if __name__=='__main__':raise SystemExit(runner.main())
