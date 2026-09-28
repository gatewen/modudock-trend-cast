"""SPEC 16.3 incl. d322a30, fixed before any round-2 score."""
from .daily_config import INDICATORS as ORIGINAL_INDICATORS, NUMERIC as ORIGINAL_NUMERIC
from .daily_config import STATES as ORIGINAL_STATES, PROTOCOL as ORIGINAL_PROTOCOL

MARKET_INDICATORS=('mkt_trend','mkt_ret5','adr_premium','sox_ret1','sox_trend')
METHODS=tuple('ind_'+n for n in MARKET_INDICATORS)+('mkt_logit',)
INDICATORS=ORIGINAL_INDICATORS+MARKET_INDICATORS
NUMERIC=ORIGINAL_NUMERIC+MARKET_INDICATORS
TERCILES=('bias20','mkt_ret5','adr_premium','sox_ret1')
STATES=dict(ORIGINAL_STATES,**{n:('low','middle','high') if n in TERCILES else ('bull','bear','neutral')
                             for n in MARKET_INDICATORS})
PROTOCOL=dict(version='market-v1',spec_commit='d322a30',original=ORIGINAL_PROTOCOL,
    indicators=INDICATORS,numeric=NUMERIC,states=STATES,methods=METHODS,
    source='FinMind',prices='Close_not_Adj_Close',fx='(spot_buy+spot_sell)/2',
    adr_ratio=5,max_age_calendar_days=5,age_anchor='prediction_day',
    cutoff=dict(taiex='day<=d',tsm='us_day<d',sox='us_day<d',usd_twd='day<d'),
    missing='latest_row_no_backfill_nonpositive_or_invalid_is_missing',
    mkt_trend=dict(window=60,numeric='close/MA-1',equal='neutral',mean_includes_latest=True),
    mkt_ret5=dict(lag_sessions=5),sox_ret1=dict(lag_sessions=1),
    sox_trend=dict(window=20,numeric='close/MA-1',equal='neutral',mean_includes_latest=True),
    history='consecutive_source_rows_any_missing_in_window_is_missing',
    terciles=dict(names=TERCILES,fit='matured_only',logit='fixed_at_refit',ties='lower'),
    comparisons=18,expected_lucky=.45)
