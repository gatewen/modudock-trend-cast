"""Round-2 as-of alignment, ADR, missingness, fitting, freezing and scoring."""
from scripts import mutation_check

F='back/market_features.py';M='back/market_models.py';S='back/market_sources.py'
R='back/market_run.py';D='back/market_store.py'
T='tests.test_market.';FT=T+'MarketFeatureTests.';MT=T+'MarketModelTests.';PT=T+'MarketPersistenceTests.'
MUTATIONS=[
    ('same_day_tsm',F,[("series=='taiex'","series in ('taiex','tsm')")],FT+'test_future_us_and_same_day_fx_invariant_past_changes'),
    ('same_day_sox',F,[("series=='taiex'","series in ('taiex','sox')")],FT+'test_future_us_and_same_day_fx_invariant_past_changes'),
    ('same_day_fx',F,[("series=='taiex'","series in ('taiex','usd_twd')")],FT+'test_future_us_and_same_day_fx_invariant_past_changes'),
    ('age_from_cutoff',F,[("age=(d-date.fromisoformat(row['day'])).days","age=(cutoff-date.fromisoformat(row['day'])).days")],FT+'test_age_anchored_at_prediction_day_five_included_six_missing'),
    ('reject_age_five',F,[("if age>5:return [],info","if age>=5:return [],info")],FT+'test_age_anchored_at_prediction_day_five_included_six_missing'),
    ('allow_age_six',F,[("if age>5:return [],info","if age>6:return [],info")],FT+'test_age_anchored_at_prediction_day_five_included_six_missing'),
    ('backfill_missing_close',F,[("return self.rows[series][:i+1],info","return [r for r in self.rows[series][:i+1] if series=='usd_twd' or quote(r) is not None],info")],FT+'test_missing_latest_never_backfills'),
    ('adr_ratio_one',F,[("adr*((buy+sell)/2)/5","adr*((buy+sell)/2)/1")],FT+'test_adr_midpoint_one_to_five_raw_close'),
    ('fx_buy_only',F,[("adr*((buy+sell)/2)/5","adr*buy/5")],FT+'test_adr_midpoint_one_to_five_raw_close'),
    ('taiex_ma_twenty',F,[("trend(data['taiex'],60)","trend(data['taiex'],20)")],FT+'test_trend_windows_include_latest_and_equality_neutral'),
    ('sox_ma_sixty',F,[("trend(data['sox'],20)","trend(data['sox'],60)")],FT+'test_trend_windows_include_latest_and_equality_neutral'),
    ('mkt_return_four',F,[("returns(data['taiex'],5)","returns(data['taiex'],4)")],FT+'test_trend_windows_include_latest_and_equality_neutral'),
    ('sox_return_two',F,[("returns(data['sox'],1)","returns(data['sox'],2)")],FT+'test_trend_windows_include_latest_and_equality_neutral'),
    ('ma_excludes_latest',F,[("rows[-count:]","rows[-count-1:-1]")],FT+'test_trend_windows_include_latest_and_equality_neutral'),
    ('neutral_bull',F,[("'bull' if value>0","'bull' if value>=0")],FT+'test_trend_windows_include_latest_and_equality_neutral'),
    ('zero_quote_valid',S,[("v <= 0","v < 0")],T+'MarketSourceTests.test_missing_codes_and_close_not_adjusted_close'),
    ('use_adjusted_adr_close',S,[("else 'Close'","else 'Adj_Close'")],T+'MarketSourceTests.test_missing_codes_and_close_not_adjusted_close'),
    ('permit_holdout_fetch',S,[("start <= end <= DEV_END","start <= end")],T+'MarketSourceTests.test_reject_invalid_response_and_holdout_before_network'),
    ('future_training',M,[("train=matured(self.records,point,self.H)","train=self.records")],MT+'test_future_labels_features_do_not_change_fitted_model'),
    ('conditional_twenty_nine',M,[("len(matches)>=30","len(matches)>=29")],MT+'test_all_horizon_maturity_and_single_minimum_thirty'),
    ('refit_every_ten',M,[("%20==0","%10==0")],MT+'test_refit_clock_minimum_training_and_order_guards'),
    ('fit_at_249',M,[("len(train)<250","len(train)<249")],MT+'test_refit_clock_minimum_training_and_order_guards'),
    ('optimizer_299_steps',M,[("range(300)","range(299)")],MT+'test_optimizer_exactly_matches_original_with_original_columns'),
    ('optimizer_half_l2',M,[("(w[j] if j else 0.)","(.5*w[j] if j else 0.)")],MT+'test_optimizer_exactly_matches_original_with_original_columns'),
    ('impute_raw_mean',M,[("is not None else 0.","is not None else mean")],MT+'test_train_only_terciles_lower_ties_and_missing_design'),
    ('omit_market_onehot',M,[("for name in INDICATORS:","for name in INDICATORS[:11]:")],MT+'test_train_only_terciles_lower_ties_and_missing_design'),
    ('unfreeze_rows',D,[("('market_rows','market_fetches')","('market_fetches',)")],PT+'test_freeze_preserves_original_and_rejects_source_or_policy_mutations'),
    ('ignore_market_digest',D,[("if row['market_digest']!=snapshot_digest(config,source_summary(store)):","if False:")],PT+'test_freeze_preserves_original_and_rejects_source_or_policy_mutations'),
    ('incomplete_qualifies',R,[("complete and comparison['ci95']","comparison['ci95']")],PT+'test_missing_forecast_reference_outcome_never_qualifies'),
    ('ci_zero_qualifies',R,[("comparison['ci95'][1]<0","comparison['ci95'][1]<=0")],PT+'test_interval_strict_negative_and_tampering_rejected'),
    ('ignore_alignment_hash',R,[("r['alignment_json']","canonical(alignment[day])")],PT+'test_interval_strict_negative_and_tampering_rejected'),
    ('wrong_comparison_count',R,[("comparisons=18","comparisons=6")],PT+'test_offline_roundtrip_idempotence_old_results_unchanged_and_dev_guards'),
]

if __name__=='__main__':
    mutation_check.MUTATIONS=MUTATIONS
    raise SystemExit(mutation_check.main())
