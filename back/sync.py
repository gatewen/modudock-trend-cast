"""Synchronous data-layer orchestration; run outside the protocol control loop."""
import calendar
from datetime import datetime, timedelta

from .data import TAIPEI, DataError, day_value, month_bounds, symbol_value
from .fugle import EARLIEST
from .http_client import ClientError


def history_start(today):
    index = today.year * 12 + today.month - 1 - 26
    year, month = divmod(index, 12)
    month += 1
    return max(EARLIEST, today.replace(year=year, month=month,
               day=min(today.day, calendar.monthrange(year, month)[1])))


def month_ranges(start, end):
    first, last = day_value(start), day_value(end)
    if first > last:
        raise DataError('invalid_query_range')
    result = []
    while first <= last:
        _, month_end = month_bounds(first.strftime('%Y-%m'))
        stop = min(month_end, last)
        result.append((first.isoformat(), stop.isoformat()))
        first = stop + timedelta(days=1)
    return result


def sync_history(store, fugle, twse, *, symbol='2330', today=None, start=None):
    """Newest first establishes the last-seven-trading-day window before finalizing.

    A 404/empty month never terminates this loop. Each dataset's month/range is
    validated before its atomic write. No network I/O occurs inside a transaction.
    """
    symbol_value(symbol)
    today = today or datetime.now(TAIPEI).date()
    start = start or history_start(today).isoformat()
    results = []
    for begin, end in reversed(month_ranges(start, today.isoformat())):
        if not store.should_fetch(symbol, begin[:7], today=today):
            results.append({'month': begin[:7], 'status': 'finalized'})
            continue
        result = {'month': begin[:7]}
        for kind in ('daily', 'corp', 'bars'):
            try:
                if kind == 'corp':
                    store.write_corp(twse.events(symbol, begin, end))
                else:
                    batch = fugle.candles(symbol, begin, end, 'D' if kind == 'daily' else '1')
                    store.write_candles(batch, today=today)
                result[kind] = 'ok'
            except (ClientError, DataError) as exc:
                store.record_failure(symbol, begin, end, kind, getattr(exc, 'status', None))
                result[kind] = 'failed'
        results.append(result)
    return results
