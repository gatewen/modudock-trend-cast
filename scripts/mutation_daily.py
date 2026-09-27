#!/usr/local/bin/python3
"""Daily layer causality, exact returns, split boundaries and atomic storage."""
from pathlib import Path
import sys
if __package__ in (None,''):sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts import mutation_check
R='tests.test_daily.DailyReplayTests.'
S='tests.test_daily.DailySourceStoreTests.'
M='back/daily_replay.py'
MUTATIONS=[
 ('warmup_59_allowed',M,[('if len(past)<WARMUP+1:','if len(past)<WARMUP:')],R+'test_sixty_day_warmup_and_missing_history_day_not_weekend'),
 ('feature_includes_future_day',M,[("WHERE day>=? AND day<=? ORDER BY day DESC LIMIT 61","WHERE day>=? AND day<=date(?,'+1 day') ORDER BY day DESC LIMIT 61"),('if not past or past[-1]!=day:','if not past:')],R+'test_future_daily_chips_corporate_randomization_cannot_change_features_or_eligibility'),
 ('same_day_chip_available',M,[('WHERE day>=? AND day<? ORDER BY day DESC LIMIT 60','WHERE day>=? AND day<=? ORDER BY day DESC LIMIT 60')],R+'test_same_day_chip_not_available_and_missing_chip_is_none_not_zero'),
 ('missing_margin_becomes_zero',M,[('margin[0] if margin else None','margin[0] if margin else 0')],R+'test_same_day_chip_not_available_and_missing_chip_is_none_not_zero'),
 ('foreign_dealer_not_summed',M,[("groups['Foreign_Investor']+groups.get('Foreign_Dealer_Self',0)","groups['Foreign_Investor']")],R+'test_foreign_category_change_sums_foreign_dealer_and_refuses_missing_category'),
 ('raw_absolute_ohlc_feature',M,[("(Fraction(row[k])/ref-1)*1000","Fraction(row[k])")],R+'test_relative_features_scale_invariant_and_volume_mean_excludes_today'),
 ('volume_mean_includes_today',M,[('WHERE day>=? AND day<? ORDER BY day DESC LIMIT 20','WHERE day>=? AND day<=? ORDER BY day DESC LIMIT 20')],R+'test_relative_features_scale_invariant_and_volume_mean_excludes_today'),
 ('corporate_reference_ignored',M,[("return Fraction(event['ref_price'])","return Fraction(previous['close'])")],R+'test_real_20100706_ex_dividend_reference_and_exact_compounded_return'),
 ('compound_becomes_sum',M,[("total*=Fraction(bars[i]['close'])/ref","total+=Fraction(bars[i]['close'])/ref-1")],R+'test_real_20100706_ex_dividend_reference_and_exact_compounded_return'),
 ('calendar_missing_day_compressed',M,[("SELECT day FROM d_calendar WHERE day>=? ORDER BY day LIMIT ?","SELECT day FROM d_bars WHERE day>=? ORDER BY day LIMIT ?")],R+'test_calendar_defines_endpoint_gaps_and_unknown_corp_refuse_scoring'),
 ('missing_calendar_day_ignored_in_labels',M,[("if stored_days!=days: return fail('interval_calendar_mismatch')","if False: return fail('interval_calendar_mismatch')")],R+'test_calendar_omission_with_present_candle_cannot_shorten_horizon_or_history'),
 ('missing_calendar_day_ignored_in_features',M,[("if stored_days!=past: return DailyPoint(day,False,'history_calendar_mismatch')","if False: return DailyPoint(day,False,'history_calendar_mismatch')")],R+'test_calendar_omission_with_present_candle_cannot_shorten_horizon_or_history'),
 ('unknown_corporate_treated_as_none',M,[("if status['state']=='unknown': raise DataError('unknown_corporate_action')","if False: raise DataError('unknown_corporate_action')")],R+'test_calendar_defines_endpoint_gaps_and_unknown_corp_refuse_scoring'),
 ('up_boundary_exclusive',M,[('if return_value >= threshold:','if return_value > threshold:')],R+'test_fraction_label_threshold_boundaries_and_population_half_up'),
 ('down_boundary_exclusive',M,[('if return_value <= -threshold:','if return_value < -threshold:')],R+'test_fraction_label_threshold_boundaries_and_population_half_up'),
 ('fraction_boundary_float',M,[('if return_value >= threshold:','if float(return_value) >= float(threshold):')],R+'test_fraction_label_threshold_boundaries_and_population_half_up'),
 ('sample_instead_of_population_sigma',M,[('variance=sum((v*v for v in values),Fraction())/n-mean*mean','variance=(sum((v*v for v in values),Fraction())/n-mean*mean)*n/(n-1)')],R+'test_fraction_label_threshold_boundaries_and_population_half_up'),
 ('half_up_tie_rounds_down',M,[('if 4*scaled.numerator >= (2*steps+1)**2*scaled.denominator:','if 4*scaled.numerator > (2*steps+1)**2*scaled.denominator:')],R+'test_fraction_label_threshold_boundaries_and_population_half_up'),
 ('zero_threshold_accepted',M,[("if steps==0: raise DataError('zero_daily_threshold')","if False: raise DataError('zero_daily_threshold')")],R+'test_fraction_label_threshold_boundaries_and_population_half_up'),
 ('horizon_endpoint_enters_holdout',M,[('engine.outcome(day,H,end_limit=dev_end)','engine.outcome(day,H)')],R+'test_development_boundary_never_reads_holdout_prices_or_chips'),
 ('threshold_allows_holdout_end',M,[('if not DEV_START<=dev_start<=dev_end<=DEV_END:','if not DEV_START<=dev_start<=dev_end:')],R+'test_development_thresholds_never_use_endpoint_after_split_and_future_changes'),
 ('sponsor_all_symbols_request','back/daily_sources.py',[("params['data_id'] = symbol","pass")],S+'test_finmind_free_symbol_full_range_no_auth_and_quota_status_not_coverage'),
 ('finmind_wrong_stock_accepted','back/daily_sources.py',[("if raw.get('stock_id') != symbol:",'if False:')],S+'test_finmind_free_symbol_full_range_no_auth_and_quota_status_not_coverage'),
 ('finmind_free_rate_too_fast','back/http_client.py',[("12.0 if host == 'api.finmindtrade.com' else 2.0","2.0")],S+'test_finmind_free_symbol_full_range_no_auth_and_quota_status_not_coverage'),
 ('twse_daily_throttle_removed','back/daily_sync.py',[('TWSE_LIMITER = RateLimiter(3.0)','TWSE_LIMITER = RateLimiter(2.0)')],S+'test_sync_actual_wiring_annual_ranges_and_corp_limiter'),
 ('twse_two_digit_year_rejected','back/twse.py',[("(\\d{2,3})年","(\\d{3})年")],R+'test_real_20100706_ex_dividend_reference_and_exact_compounded_return'),
 ('daily_rollback_replaced_with_commit','back/daily_store.py',[('self.db.rollback()','self.db.commit()')],S+'test_invalid_or_empty_bar_batch_never_erases_data_and_mid_write_rolls_back'),
 ('corp_rollback_replaced_with_commit','back/daily_store.py',[('self.db.rollback()','self.db.commit()')],S+'test_new_tables_only_atomic_range_replacement_and_three_states'),
 ('corp_unknown_coverage_accepted','back/daily_store.py',[("if known is None: return {'state':'unknown','event':None}","if False: return {'state':'unknown','event':None}")],S+'test_new_tables_only_atomic_range_replacement_and_three_states'),
 ('already_fetched_not_skipped','back/daily_sync.py',[("store.fetched(kind,symbol,first,last)",'False')],S+'test_sync_skip_ranges_annual_calls_no_jev_and_independent_calendar_missing'),
]
if __name__=='__main__':
    mutation_check.MUTATIONS=MUTATIONS
    raise SystemExit(mutation_check.main())
