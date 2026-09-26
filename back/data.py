"""Validated, exact decimal market data; no provider text in exceptions."""
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, InvalidOperation
import calendar
import re

TAIPEI = timezone(timedelta(hours=8))
MAPPING = 'raw-start-plus-one-minute-auction-1330-v1'


class DataError(ValueError):
    pass


def symbol_value(value):
    if not isinstance(value, str) or not re.fullmatch(r'[0-9A-Z]{4,6}', value):
        raise DataError('invalid_symbol')
    return value


def day_value(value):
    try:
        if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
            raise ValueError
        return date.fromisoformat(value)
    except (ValueError, TypeError):
        raise DataError('invalid_day') from None


def month_bounds(month):
    first = day_value(month + '-01')
    return first, first.replace(day=calendar.monthrange(first.year, first.month)[1])


def decimal_value(value, *, positive=True):
    # Reject bools, exponent bombs, and non-finite numbers before SQLite/JSON.
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        raise DataError('invalid_number')
    try:
        text = str(value)
        if len(text) > 64:
            raise ValueError
        number = Decimal(text)
        if not number.is_finite() or abs(number.adjusted()) > 18:
            raise ValueError
        if (positive and number <= 0) or (not positive and number < 0):
            raise ValueError
        # Decimal.normalize() applies the current context precision and can
        # silently round a provider's original digits before labeling.
        plain = format(number, 'f')
        return plain.rstrip('0').rstrip('.') if '.' in plain else plain
    except (ValueError, InvalidOperation):
        raise DataError('invalid_number') from None


def prices(row):
    if not isinstance(row, dict):
        raise DataError('invalid_candle')
    try:
        result = {k: decimal_value(row[k]) for k in ('open', 'high', 'low', 'close')}
        result['volume'] = decimal_value(row['volume'], positive=False)
    except KeyError:
        raise DataError('missing_candle_field') from None
    o, h, l, c = (Decimal(result[k]) for k in ('open', 'high', 'low', 'close'))
    if not l <= min(o, c) <= max(o, c) <= h:
        raise DataError('invalid_ohlc')
    return result


def local_time(value):
    try:
        if isinstance(value, str):
            if len(value) > 40:
                raise ValueError
            value = datetime.fromisoformat(value)
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise ValueError
        return value.astimezone(TAIPEI)
    except (TypeError, ValueError, OverflowError):
        raise DataError('invalid_timestamp') from None


def bar_end(ts_raw):
    stamp = local_time(ts_raw)
    if stamp.second or stamp.microsecond or not time(9) <= stamp.time() <= time(13, 30):
        raise DataError('invalid_session_timestamp')
    end = stamp if stamp.time() == time(13, 30) else stamp + timedelta(minutes=1)
    return end.isoformat(timespec='seconds')


def candles(rows, symbol, start, end, timeframe):
    symbol_value(symbol)
    first, last = day_value(start), day_value(end)
    if first > last or timeframe not in ('1', 'D') or not isinstance(rows, list):
        raise DataError('invalid_candles')
    output, seen = [], set()
    for row in rows:
        values = prices(row)
        stamp = row.get('date')
        if timeframe == '1':
            if not isinstance(stamp, str):
                raise DataError('invalid_timestamp')
            end_time = bar_end(stamp)
            day = local_time(stamp).date()
            identity = end_time
            values.update(ts_raw=stamp, bar_end=end_time)
        else:
            day = day_value(stamp)
            identity = day.isoformat()
        if not first <= day <= last:
            raise DataError('candle_outside_range')
        if identity in seen:
            raise DataError('duplicate_timestamp')
        seen.add(identity)
        output.append(dict(symbol=symbol, day=day.isoformat(), **values))
    return sorted(output, key=lambda r: r.get('bar_end', r['day']))
