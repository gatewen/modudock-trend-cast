#!/usr/local/bin/python3
"""Inspect data quality without exposing prices or unopened holdout labels."""
import argparse
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from decimal import Decimal
import json
from pathlib import Path
import sqlite3
import statistics
import sys

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from back.data import DataError, MAPPING, decimal_value, local_time, prices, symbol_value
from back.store import DEFAULT_DB, Store, now_string


def _distribution(values):
    if not values:
        return {'count': 0, 'min': None, 'median': None, 'max': None}
    return {'count': len(values), 'min': str(min(values)),
            'median': str(statistics.median(values)), 'max': str(max(values))}


def _valid_prices(row):
    try:
        prices(row)
        return True
    except DataError:
        return False


def _valid_positive_prices(row):
    try:
        for key in ('open', 'high', 'low', 'close'):
            decimal_value(row[key])
        return True
    except (KeyError, DataError):
        return False


def _labels(store, experiment, start, end):
    counts = Counter(row[0] for row in store.db.execute('''SELECT label FROM outcomes
        WHERE experiment_id=? AND substr(t,1,10) BETWEEN ? AND ?''', (experiment, start, end)))
    total = sum(counts.values())
    return {'n': total, 'counts': {k: counts[k] for k in ('up', 'flat', 'down')},
            'percent': {k: round(100 * counts[k] / total, 2) if total else None
                        for k in ('up', 'flat', 'down')}}


def build_report(store, *, symbol='2330', experiment_id=None, include_holdout=False):
    """One consistent read snapshot; a reveal is durable before output is returned."""
    symbol_value(symbol)
    store.db.execute('BEGIN IMMEDIATE' if include_holdout else 'BEGIN')
    try:
        result = _build_report(store, symbol, experiment_id, include_holdout)
        store.db.commit()
        return result
    except BaseException:
        store.db.rollback()
        raise


def _build_report(store, symbol, experiment_id, include_holdout):
    if experiment_id is None:
        experiment = store.db.execute('SELECT * FROM experiments ORDER BY id DESC LIMIT 1').fetchone()
    else:
        experiment = store.db.execute('SELECT * FROM experiments WHERE id=?', (experiment_id,)).fetchone()
        if experiment is None:
            raise DataError('experiment_not_found')
    if experiment is not None and json.loads(experiment['config_json']).get('symbol', '2330') != symbol:
        raise DataError('experiment_symbol_mismatch')
    if include_holdout and experiment is None:
        raise DataError('experiment_required')
    grouped = defaultdict(list)
    daily = {r['day']: dict(r) for r in store.db.execute('SELECT * FROM daily WHERE symbol=? ORDER BY day', (symbol,))}
    invalid_ohlc = nonpositive = invalid_time = 0
    for r in store.db.execute('SELECT * FROM bars WHERE symbol=? ORDER BY day,bar_end', (symbol,)):
        row = dict(r)
        grouped[row['day']].append(row)
        invalid_ohlc += int(not _valid_prices(row))
        nonpositive += int(not _valid_positive_prices(row))
    all_days = sorted(set(daily) | set(grouped))
    first_times, last_regular, missing, counts = Counter(), Counter(), {}, Counter()
    open_matches = Counter()
    ratios = []
    # A diagnostic expectation, not evidence establishing timestamp semantics.
    expected = {(datetime(2000, 1, 1, 9) + timedelta(minutes=i)).strftime('%H:%M')
                for i in range(265)} | {'13:30'}
    auction_days = 0
    for day in all_days:
        rows = grouped.get(day, [])
        counts[len(rows)] += 1
        observed = set()
        for row in rows:
            try:
                observed.add(local_time(row['ts_raw']).strftime('%H:%M'))
            except DataError:
                invalid_time += 1
        missing[day] = len(expected - observed)
        if observed:
            first_times[min(observed)] += 1
            regular = observed - {'13:30'}
            if regular:
                last_regular[max(regular)] += 1
            auction_days += int('13:30' in observed)
        if rows and day in daily and _valid_prices(daily[day]) and all(_valid_prices(row) for row in rows):
            open_matches['match' if Decimal(rows[0]['open']) == Decimal(daily[day]['open']) else 'mismatch'] += 1
            volume = Decimal(daily[day]['volume'])
            if volume > 0:
                ratios.append(sum(Decimal(r['volume']) for r in rows) / volume)
    duplicate_count = store.db.execute('''SELECT coalesce(sum(n-1),0) FROM
        (SELECT count(*) n FROM bars WHERE symbol=? GROUP BY ts_raw HAVING count(*)>1)''', (symbol,)).fetchone()[0]
    warnings = [dict(r) for r in store.db.execute('''SELECT month,kind,count(*) AS count
        FROM fetch_log WHERE symbol=? AND note='frozen_difference' GROUP BY month,kind''', (symbol,))]
    failures = [dict(r) for r in store.db.execute('''SELECT month,kind,note,count(*) AS count
        FROM fetch_log WHERE symbol=? AND note IN ('empty','fetch_failed') GROUP BY month,kind,note''', (symbol,))]
    coverage = [dict(r) for r in store.db.execute('''SELECT start_day,end_day,source FROM corp_coverage
        WHERE symbol=? ORDER BY start_day,end_day''', (symbol,))]
    states = Counter(store.corp_state(symbol, day)['state'] for day in daily)
    result = {
        '1_calendar': {'first_day': all_days[0] if all_days else None,
            'last_day': all_days[-1] if all_days else None, 'trading_days': len(daily),
            'missing_bar_days': sorted(set(daily) - set(grouped)),
            'bars_without_daily': sorted(set(grouped) - set(daily)),
            'fetch_issues': failures, 'frozen_warnings': warnings},
        '2_timestamps': {'mapping': MAPPING, 'semantics_proven': False,
            'first_raw_time_counts': dict(first_times), 'last_regular_raw_time_counts': dict(last_regular),
            'days_with_1330': auction_days, 'first_open_matches_daily': dict(open_matches)},
        '3_minutes': {'bar_count_distribution': dict(sorted(counts.items())),
            'expected_raw_grid': '09:00..13:24 + 13:30 (start-label assumption; no gap filling)',
            'missing_minutes_by_day': missing},
        '4_validation': {'duplicate_timestamps': duplicate_count, 'invalid_ohlc': invalid_ohlc,
                         'nonpositive_prices': nonpositive, 'invalid_timestamps': invalid_time,
                         'invalid_daily_ohlc': sum(not _valid_prices(row) for row in daily.values()),
                         'nonpositive_daily_prices': sum(not _valid_positive_prices(row) for row in daily.values())},
        '5_volume': {'minute_sum_over_daily': _distribution(ratios),
            'documented_units': 'minute=lots; daily=shares; nominal ratio=0.001 (sessions may differ)'},
        '6_corporate_actions': {'events': [dict(r) for r in store.db.execute(
            'SELECT day,source FROM corp_events WHERE symbol=? ORDER BY day', (symbol,))],
            'coverage': coverage, 'states': {s: states[s] for s in ('event', 'none', 'unknown')}},
    }
    if experiment is not None:
        result['7_dev_labels'] = _labels(store, experiment['id'], experiment['dev_start'], experiment['dev_end'])
    if include_holdout:
        store.db.execute('INSERT INTO reveals VALUES (?,?,?,?,?)',
            (experiment['id'], now_string(), experiment['hold_start'], experiment['hold_end'], 'check_data_labels'))
        result['8_holdout_labels'] = _labels(store, experiment['id'], experiment['hold_start'], experiment['hold_end'])
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', type=Path, default=DEFAULT_DB)
    parser.add_argument('--symbol', default='2330')
    parser.add_argument('--experiment', type=int)
    parser.add_argument('--include-holdout', action='store_true', help='Reveal holdout labels and persist exposure')
    args = parser.parse_args(argv)
    if not args.db.is_file():
        print('database_missing', file=sys.stderr)
        return 2
    try:
        with Store(args.db, readonly=not args.include_holdout) as store:
            report = build_report(store, symbol=args.symbol, experiment_id=args.experiment,
                                  include_holdout=args.include_holdout)
        print(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False))
        return 0
    except (DataError, sqlite3.Error, ValueError, OSError):
        print('data_check_failed', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
