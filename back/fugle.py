"""Fugle historical candles; credentials are read only from the environment."""
from dataclasses import dataclass
from datetime import date
import os

from .data import candles, day_value, symbol_value, DataError
from .http_client import ClientError, JsonClient

EARLIEST = date(2023, 5, 23)


@dataclass(frozen=True)
class CandleBatch:
    symbol: str
    start: str
    end: str
    timeframe: str
    status: int
    rows: list


class FugleClient:
    def __init__(self, **transport):
        self._http = JsonClient('api.fugle.tw', **transport)

    def candles(self, symbol, start, end, timeframe='1'):
        symbol_value(symbol)
        first, last = day_value(start), day_value(end)
        try:
            anniversary = first.replace(year=first.year + 1)
        except ValueError:
            anniversary = first.replace(year=first.year + 1, day=28)
        if first > last or last >= anniversary or timeframe not in ('1', 'D'):
            raise DataError('invalid_query_range')
        key = os.environ.get('FUGLE_API_KEY', '')
        if not key:
            raise ClientError('missing_key')
        if '\r' in key or '\n' in key:
            raise ClientError('invalid_key')
        params = {'from': start, 'to': end, 'timeframe': timeframe, 'sort': 'asc',
                  'fields': 'open,high,low,close,volume'}
        if timeframe == 'D':
            params['adjusted'] = 'false'
        result = self._http.get('/marketdata/v1.0/stock/historical/candles/' + symbol,
                                params, headers={'X-API-KEY': key}, allow_404=True)
        if result.status == 404:
            rows = []
        else:
            payload = result.payload
            if (not isinstance(payload, dict) or payload.get('symbol') != symbol
                    or payload.get('timeframe') != timeframe
                    or payload.get('adjusted', False) is not False):
                raise DataError('invalid_candles_envelope')
            rows = candles(payload.get('data'), symbol, start, end, timeframe)
        return CandleBatch(symbol, start, end, timeframe, result.status, rows)
