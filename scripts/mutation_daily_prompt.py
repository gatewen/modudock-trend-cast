#!/usr/local/bin/python3
"""p6 draft: fixed dates, horizon criteria, private fields and causal state."""
from pathlib import Path
import sys
if __package__ in (None,''):sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts import mutation_check
P='back/daily_prompt.py';T='tests.test_daily_prompt.DailyPromptTests.'
MUTATIONS=[
 ('sample_step_changed',P,[('SAMPLE_STEP=5','SAMPLE_STEP=4')],T+'test_sample_calendar_only_fixed_grid_and_common_endpoints'),
 ('sample_origin_shifted',P,[('range(origin,len(days),SAMPLE_STEP)','range(origin+1,len(days),SAMPLE_STEP)')],T+'test_sample_calendar_only_fixed_grid_and_common_endpoints'),
 ('sample_tail_uses_shortest_horizon',P,[('i+max(HORIZONS)<len(days)','i+min(HORIZONS)<len(days)')],T+'test_sample_calendar_only_fixed_grid_and_common_endpoints'),
 ('fixed_threshold_instead_of_frozen',P,[("k=threshold_text(Fraction(**config['thresholds'][str(H)]))","k='1'")],T+'test_three_questions_thresholds_exact_inclusive_from_config'),
 ('up_threshold_excludes_boundary',P,[('R_H ≥ +{k}%','R_H > +{k}%')],T+'test_three_questions_thresholds_exact_inclusive_from_config'),
 ('date_leaks_into_body',P,[('state=dict(daily=daily,indicators=', 'state=dict(day=day,daily=daily,indicators=')],T+'test_payload_bytes_whitelist_no_dates_symbol_absolute_prices'),
 ('raw_price_leaks_into_body',P,[('state=dict(daily=daily,indicators=', 'state=dict(close_t=store.db.execute("SELECT close FROM d_bars WHERE day=?",(day,)).fetchone()[0],daily=daily,indicators=')],T+'test_payload_bytes_whitelist_no_dates_symbol_absolute_prices'),
 ('bias_includes_unmatured',P,[('and r.index+H<=point.index','')],T+'test_bias_cutoffs_per_horizon_maturity_boundary_and_future_invariance'),
 ('bias_excludes_maturity_boundary',P,[('r.index+H<=point.index','r.index+H<point.index')],T+'test_bias_cutoffs_per_horizon_maturity_boundary_and_future_invariance'),
 ('bias_all_horizons_use_three',P,[('r.index+H<=point.index','r.index+3<=point.index')],T+'test_bias_cutoffs_per_horizon_maturity_boundary_and_future_invariance'),
 ('same_day_chips_in_features','back/daily_indicators.py',[('available = calendar[:i]','available = calendar[:i+1]')],T+'test_future_prices_corp_labels_and_same_day_chips_do_not_change_bytes'),
 ('missing_chip_becomes_zero',P,[("result[name]='本期無此資料'","result[name]='0'")],T+'test_reuses_frozen_indicator_text_and_missing_is_not_zero'),
 ('margin_lots_not_converted_to_shares',P,[("('margin_chg',1000)","('margin_chg',1)")],T+'test_chips_only_percent_missing_units_and_previous_20_volume'),
 ('denominator_includes_today',P,[('WHERE day>=? AND day<? ORDER BY day DESC LIMIT 20','WHERE day>=? AND day<=? ORDER BY day DESC LIMIT 20')],T+'test_chips_only_percent_missing_units_and_previous_20_volume'),
 ('field_glossary_loses_volume_lag',P,[('最後一欄的均量不含該列當日','最後一欄的均量含該列當日')],T+'test_reuses_frozen_indicator_text_and_missing_is_not_zero'),
]
if __name__=='__main__':
    mutation_check.MUTATIONS=MUTATIONS
    raise SystemExit(mutation_check.main())
