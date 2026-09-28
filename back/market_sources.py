"""Bounded, token-free FinMind market inputs. Never fetch the holdout."""
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import hashlib
import json

from .daily_replay import DEV_END
from .data import DataError, day_value
from .http_client import ClientError, JsonClient

START = '2009-01-01'
SOURCES = {'taiex': ('TaiwanStockPrice','TAIEX'),
           'tsm': ('USStockPrice','TSM'), 'sox': ('USStockPrice','^SOX'),
           'usd_twd': ('TaiwanExchangeRate','USD')}


def positive(value):
    """Missing/zero/negative quote codes stay missing, never backfilled."""
    if value is None or isinstance(value, bool): return None
    try:
        v = Decimal(str(value))
        if not v.is_finite() or v <= 0: return None
        return format(v.normalize(), 'f')
    except (InvalidOperation, ValueError): return None


def bounds(start, end):
    day_value(start); day_value(end)
    if not START <= start <= end <= DEV_END:
        raise DataError('market_dev_only')


@dataclass(frozen=True)
class MarketBatch:
    series: str
    start: str
    end: str
    rows: tuple
    payload_hash: str


def parse(payload, series, start=START, end=DEV_END):
    bounds(start,end)
    if series not in SOURCES: raise DataError('market_unknown_series')
    if not isinstance(payload,dict) or payload.get('status')!=200 or payload.get('msg')!='success':
        raise DataError('market_unconfirmed_response')
    if not isinstance(payload.get('data'),list): raise DataError('market_bad_schema')
    rows=[];seen=set()
    for r in payload['data']:
        if not isinstance(r,dict): raise DataError('market_bad_row')
        day=r.get('date');day_value(day)
        if not start<=day<=end or day in seen: raise DataError('market_invalid_date')
        if r.get('currency' if series=='usd_twd' else 'stock_id')!=SOURCES[series][1]:
            raise DataError('market_wrong_symbol')
        seen.add(day)
        close=positive(r.get('close' if series=='taiex' else 'Close')) if series!='usd_twd' else None
        buy=positive(r.get('spot_buy')) if series=='usd_twd' else None
        sell=positive(r.get('spot_sell')) if series=='usd_twd' else None
        rows.append(dict(series=series,day=day,close=close,spot_buy=buy,spot_sell=sell))
    encoded=json.dumps(payload,sort_keys=True,separators=(',',':'),ensure_ascii=False,default=str,allow_nan=False)
    return MarketBatch(series,start,end,tuple(sorted(rows,key=lambda r:r['day'])),hashlib.sha256(encoded.encode()).hexdigest())


class MarketClient:
    def __init__(self, **transport): self.http=JsonClient('api.finmindtrade.com',**transport)
    def fetch(self, series, start=START, end=DEV_END):
        bounds(start,end)
        if series not in SOURCES: raise DataError('market_unknown_series')
        dataset,symbol=SOURCES[series]
        response=self.http.get('/api/v4/data',dict(dataset=dataset,data_id=symbol,start_date=start,end_date=end))
        if isinstance(response.payload,dict) and response.payload.get('status')==402:
            raise ClientError('finmind_quota_exhausted',402)
        return parse(response.payload,series,start,end)
