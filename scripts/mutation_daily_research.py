#!/usr/local/bin/python3
"""Daily experiment / causal indicators / walk-forward / logit / scoring mutations."""
from pathlib import Path
import sys
if __package__ in (None,''):sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts import mutation_check
I='back/daily_indicators.py';M='back/daily_models.py';E='back/daily_experiment.py';R='back/daily_run.py';S='back/daily_score.py'
TI='tests.test_daily_research.IndicatorTests.'
TM='tests.test_daily_research.DailyModelTests.'
TE='tests.test_daily_research.DailyExperimentTests.'
TS='tests.test_daily_research.DailyScoreTests.'
MUTATIONS=[
 ('same_day_chips',I,[('available = calendar[:i]','available = calendar[:i+1]')],TI+'test_chip_lag_window_missing_and_category_transition'),
 ('chip_three_days_becomes_two',I,[('available[-3:]','available[-2:]')],TI+'test_chip_lag_window_missing_and_category_transition'),
 ('margin_five_intervals_becomes_four',I,[('margins.get(available[-6])','margins.get(available[-5])')],TI+'test_chip_lag_window_missing_and_category_transition'),
 ('foreign_dealer_omitted',I,[("row['Foreign_Investor']+row.get('Foreign_Dealer_Self',0)","row['Foreign_Investor']")],TI+'test_chip_lag_window_missing_and_category_transition'),
 ('future_close_leaks_into_prefix',I,[("values=dict(ma_cross=spread/mean","values=dict(ma_cross=float(bars[days[-1]]['close'])/mean")],TI+'test_future_price_corp_chips_and_same_day_chips_do_not_change_prefix'),
 ('corporate_reference_ignored',I,[("ref=Fraction(event['ref_price'])","ref=Fraction(previous['close'])")],TI+'test_adjusted_indicator_reference_and_exact_momentum'),
 ('unknown_corp_accepted',I,[("row is None or status['state']=='unknown'","row is None")],TI+'test_future_price_corp_chips_and_same_day_chips_do_not_change_prefix'),
 ('missing_calendar_not_guarded',I,[("if i and any(days[i-1]<unexpected<day for unexpected in extra): valid=False","if False: valid=False")],TI+'test_missing_calendar_bar_and_unknown_corp_refuse_prefix'),
 ('warmup_59',I,[('i>=WARMUP','i>=WARMUP-1')],TI+'test_trend_wilder_kd_macd_and_inclusive_volume_mean'),
 ('vol_19_returns',I,[('gross[-20:]','gross[-19:]')],TI+'test_trend_wilder_kd_macd_and_inclusive_volume_mean'),
 ('volume_mean_excludes_today',I,[('fmean(volume[-20:])','fmean(volume[-21:-1])')],TI+'test_trend_wilder_kd_macd_and_inclusive_volume_mean'),
 ('rsi_wilder_wrong',I,[('(avg_gain*13+gains[-1])/14','(avg_gain*12+gains[-1])/13')],TI+'test_recursive_indicator_regression_and_latest_cross'),
 ('kd_smoothing_wrong',I,[('(2*k+rsv)/3','(k+rsv)/2')],TI+'test_recursive_indicator_regression_and_latest_cross'),
 ('ema_period_wrong',I,[('(c-ema12)*2/13','(c-ema12)*2/12')],TI+'test_recursive_indicator_regression_and_latest_cross'),
 ('latest_cross_replaced_first',I,[("ma_cross=crosses[-1][1]","ma_cross=crosses[0][1]")],TI+'test_recursive_indicator_regression_and_latest_cross'),
 ('maturity_boundary_excluded',M,[('r.frame.index+H > point.index','r.frame.index+H >= point.index')],TM+'test_maturity_boundary_future_truth_and_features_invariant'),
 ('maturity_guards_removed',M,[('if r.frame.index+H > point.index or r.end_day > point.day: continue','if False: continue')],TM+'test_all_learners_future_records_randomized_after_sufficient_history'),
 ('conditional_cuts_include_future',M,[('conditional(method,point,train,majority)','conditional(method,point,self.records,majority)')],TM+'test_all_learners_future_records_randomized_after_sufficient_history'),
 ('laplace_removed',M,[('(counts[name]+1)/n','counts[name]/n')],TM+'test_smoothing_fallback_missing_and_30_sample_boundary'),
 ('fallback_30_exclusive',M,[('len(matches)>=30','len(matches)>30')],TM+'test_smoothing_fallback_missing_and_30_sample_boundary'),
 ('quantile_tie_higher',M,[('bisect_left(boundaries,value)','__import__("bisect").bisect_right(boundaries,value)')],TM+'test_tercile_interpolation_tie_lower_and_matured_only'),
 ('quantile_wrong_cut',M,[('percentile(values,1/3)','percentile(values,.25)')],TM+'test_tercile_interpolation_tie_lower_and_matured_only'),
 ('refit_19_not_20',M,[('(point.index-self.origin)%20==0','(point.index-self.origin)%19==0')],TM+'test_logit_refit_every_20_sessions_and_initial_majority'),
 ('minimum_training_249',M,[('if len(train)<250:','if len(train)<249:')],TM+'test_logit_train_only_scaler_cuts_and_future_labels'),
 ('logit_mean_uses_current',M,[('fmean(values) if values else 0.','fmean(values+[point.values.get(NUMERIC[i],0.)]) if values else 0.'),('for values in columns)\n        scales','for i,values in enumerate(columns))\n        scales')],TM+'test_logit_train_only_scaler_cuts_and_future_labels'),
 ('logit_scale_sample_sigma',M,[('pstdev(values) or 1.','pstdev(values)*(len(values)/(len(values)-1))**.5 or 1.')],TM+'test_logit_missing_training_imputation_onehot_and_standardization'),
 ('logit_imputation_not_train_mean',M,[('else 0.\n                  for name,mean,scale','else 10.\n                  for name,mean,scale')],TM+'test_logit_missing_training_imputation_onehot_and_standardization'),
 ('logit_299_steps',M,[('range(300)','range(299)')],TM+'test_logit_train_only_scaler_cuts_and_future_labels'),
 ('logit_learning_rate_changed',M,[('w[j]-.1*','w[j]-.2*')],TM+'test_logit_train_only_scaler_cuts_and_future_labels'),
 ('logit_l2_removed',M,[('(w[j] if j else 0.)','0.')],TM+'test_logit_train_only_scaler_cuts_and_future_labels'),
 ('logit_bias_penalized',M,[('(w[j] if j else 0.)','w[j]')],TM+'test_logit_train_only_scaler_cuts_and_future_labels'),
 ('momentum_threshold_not_fraction',M,[('label(point.past_returns[self.H],self.threshold)','label(point.past_returns[self.H],self.threshold*2)')],TM+'test_exact_momentum_reversal_and_no_holdout_prediction'),
 ('holdout_prediction_allowed',M,[('not point.predictable or not DEV_START<=point.day<=DEV_END','not point.predictable')],TM+'test_exact_momentum_reversal_and_no_holdout_prediction'),
 ('raw_frozen_overwrite',E,[("WHERE {' OR '.join(matches)})","WHERE 0 AND ({' OR '.join(matches)}))")],TE+'test_freeze_digest_sources_settings_and_legacy_isolation'),
 ('source_digest_check_removed',E,[("if verify and digest(config,source_summary(store,config,DEV_END))!=row['dev_digest']:",'if False:')],TE+'test_digest_validation_detects_bypass_and_protocol_change'),
 ('calendar_bar_check_removed',E,[("if days!=bar_days: raise DataError('daily_missing_calendar_or_bar')",'if False: pass')],TE+'test_creation_refuses_missing_warmup_bar_unknown_corp_and_rolls_back'),
 ('corp_creation_unknown_allowed',E,[("if any(store.corp_state(d)['state']=='unknown' for d in days):",'if False:')],TE+'test_creation_refuses_missing_warmup_bar_unknown_corp_and_rolls_back'),
 ('outcome_crosses_dev_end',R,[("engine.outcome(point.day,H,end_limit=row['config']['dev_end'])","engine.outcome(point.day,H)")],TE+'test_run_offline_rerun_no_refit_and_dev_cap'),
 ('missing_outcome_called_complete',S,[('len(common)==len(expected)','len(common)==len(outcomes)')],TE+'test_report_missing_outcome_incomplete_and_all_states_visible'),
 ('bootstrap_one_session',S,[('range(0,len(calendar),20)','range(0,len(calendar),1)'),('calendar[offset:offset+20]','calendar[offset:offset+1]')],TS+'test_twenty_session_blocks_tail_pairing_and_seed'),
 ('bootstrap_drops_tail',S,[('range(0,len(calendar),20)','range(0,len(calendar)-19,20)')],TS+'test_twenty_session_blocks_tail_pairing_and_seed'),
 ('bootstrap_reblocks_intersection',S,[('blocks=[]','calendar=tuple(d for d in calendar if d in keys)\n    blocks=[]')],TS+'test_blocks_follow_full_calendar_not_filtered_intersection'),
 ('zero_upper_bound_qualifies',S,[("comparison['ci95'][1]<0","comparison['ci95'][1]<=0")],TE+'test_shortlisting_requires_strictly_negative_upper_bound'),
 ('lower_bound_selects_losers',S,[("comparison['ci95'][1]<0","comparison['ci95'][0]<0")],TE+'test_shortlisting_requires_strictly_negative_upper_bound'),
]
if __name__=='__main__':
    mutation_check.MUTATIONS=MUTATIONS
    raise SystemExit(mutation_check.main())
