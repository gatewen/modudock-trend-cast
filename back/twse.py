"""TWSE TWT49U: checked range coverage is committed together with its events."""
from dataclasses import dataclass
from datetime import date
import re

from .data import DataError, day_value, decimal_value, symbol_value
from .http_client import JsonClient

SOURCE = 'TWSE:TWT49U'
FIELDS = ('資料日期', '股票代號', '除權息前收盤價', '除權息參考價')


@dataclass(frozen=True)
class CorpBatch:
    symbol: str
    start: str
    end: str
    events: list
    source: str = SOURCE


def roc_day(value):
    if not isinstance(value, str):
        raise DataError('invalid_twse_day')
    match = re.fullmatch(r'(\d{3})年(\d{2})月(\d{2})日', value)
    if not match:
        raise DataError('invalid_twse_day')
    try:
        return date(int(match[1]) + 1911, int(match[2]), int(match[3])).isoformat()
    except ValueError:
        raise DataError('invalid_twse_day') from None


def parse_events(payload, symbol, start, end):
    # A network error, 404, bad schema or unknown status is never known-no-event.
    if not isinstance(payload, dict) or payload.get('stat') != 'OK':
        raise DataError('unconfirmed_corp_coverage')
    if payload.get('strDate') != start.replace('-', '') or payload.get('endDate') != end.replace('-', ''):
        raise DataError('corp_range_mismatch')
    fields, data = payload.get('fields'), payload.get('data')
    if (not isinstance(fields, list) or not all(isinstance(f, str) for f in fields)
            or len(set(fields)) != len(fields) or not all(f in fields for f in FIELDS)
            or not isinstance(data, list)):
        raise DataError('invalid_corp_schema')
    events, seen = [], set()
    for row in data:
        if not isinstance(row, list) or len(row) != len(fields):
            raise DataError('invalid_corp_row')
        values = dict(zip(fields, row))
        day = roc_day(values['資料日期'])
        code = symbol_value(values['股票代號'])
        if not start <= day <= end:
            raise DataError('corp_event_outside_range')
        if code != symbol:
            continue
        if day in seen:
            raise DataError('duplicate_corp_event')
        seen.add(day)
        try:
            previous = values['除權息前收盤價'].replace(',', '')
            reference = values['除權息參考價'].replace(',', '')
        except AttributeError:
            raise DataError('invalid_corp_price') from None
        events.append(dict(symbol=symbol, day=day, prev_close=decimal_value(previous),
                           ref_price=decimal_value(reference), source=SOURCE))
    return sorted(events, key=lambda event: event['day'])


class TwseClient:
    def __init__(self, **transport):
        self._http = JsonClient('www.twse.com.tw', **transport)

    def events(self, symbol, start, end):
        symbol_value(symbol)
        if day_value(start) > day_value(end):
            raise DataError('invalid_query_range')
        result = self._http.get('/rwd/zh/exRight/TWT49U', {
            'startDate': start.replace('-', ''), 'endDate': end.replace('-', ''),
            'response': 'json'})
        return CorpBatch(symbol, start, end, parse_events(result.payload, symbol, start, end))
