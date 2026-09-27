"""Append-only prospective cohort: enroll unseen closed dates before replay.

Date discovery never reads OHLC, outcomes, predictions or returns. Enrollment
freezes source data and settings; a separate explicit reveal validates every
answer under the same write transaction. Previously revealed dates remain in
the cumulative cohort, while unexposed new dates are admitted on the next run.
"""
from dataclasses import replace
from datetime import datetime
import hashlib
import json

from .data import DataError, MAPPING, TAIPEI
from .experiment import (MODEL, canonical, digest_settings, experiment_row,
                         exposed_days, load_plan, verify_digest)
from .replay import FEATURE_VERSION, Replay
from .store import now_string

LOCKED = {'state': 'locked', 'message': '前瞻段未揭露'}
FORWARD_METHODS = ('jev', 'always_flat', 'majority', 'momentum', 'reversal', 'vol_prior')


def has_table(store, table):
    return bool(store.db.execute('SELECT 1 FROM sqlite_master WHERE type=? AND name=?', ('table', table)).fetchone())


def selected(store, experiment_id=1):
    row = experiment_row(store, experiment_id)
    config = json.loads(row['config_json'])
    if (type(experiment_id) is not int or experiment_id != 1 or row['prompt_version'] != 'p1'
            or row['model'] != MODEL or row['feature_version'] != FEATURE_VERSION
            or row['threshold_permille'] != 3 or config.get('symbol') != '2330'
            or config.get('mapping') != MAPPING):
        raise DataError('forward_settings_changed')
    return row


def revealed_days(store, experiment_id=1):
    if not has_table(store, 'forward_days'):
        return ()
    return tuple(r[0] for r in store.db.execute('''SELECT f.day FROM forward_days f
        WHERE f.experiment_id=? AND EXISTS (SELECT 1 FROM reveals r
            WHERE r.experiment_id=f.experiment_id AND r.segment='forward' AND r.what='all'
            AND f.day BETWEEN r.first_day AND r.last_day) ORDER BY f.day''', (experiment_id,)))


def discover(store, experiment_id=1, *, now=None):
    """Dates-only preview, safe before any authorization to run or reveal."""
    row = selected(store, experiment_id)
    config = json.loads(row['config_json'])
    now = now or datetime.now(TAIPEI)
    if now.tzinfo is None:
        raise DataError('timezone_required')
    today = now.astimezone(TAIPEI).date().isoformat()
    days = tuple(r[0] for r in store.db.execute('''SELECT day FROM daily WHERE symbol=?
        AND day>? AND day<=? ORDER BY day''', (config['symbol'], row['hold_end'], today)))
    exposed = set(exposed_days(store, config['symbol'], days))
    accepted, excluded = [], []
    # A final month or daily K + closing auction proves the day has closed.
    # Intraday gaps are preserved and assessed point by point by SPEC 5.
    for day in days:
        if day in exposed:
            excluded.append(day)
            continue
        if now < datetime.fromisoformat(day + 'T13:30:00').replace(tzinfo=TAIPEI):
            continue
        observed = {r[0][11:16] for r in store.db.execute(
            'SELECT ts_raw FROM bars WHERE symbol=? AND day=?', (config['symbol'], day))}
        if not observed or ('13:30' not in observed and store.should_fetch(
                config['symbol'], day[:7], today=now.astimezone(TAIPEI).date())):
            continue
        accepted.append(day)
    return {'dates': accepted, 'excluded_exposed_dates': excluded}


def active_days(store, experiment_id=1):
    if not has_table(store, 'forward_days'):
        return ()
    return tuple(r[0] for r in store.db.execute('''SELECT day FROM forward_days f
        WHERE experiment_id=? AND NOT EXISTS (SELECT 1 FROM forward_exclusions x
            WHERE x.experiment_id=f.experiment_id AND x.day=f.day) ORDER BY day''', (experiment_id,)))


def source_digest(store, row, start, end):
    digest = hashlib.sha256(canonical(digest_settings(row)).encode())
    symbol = json.loads(row['config_json'])['symbol']
    for table in ('bars', 'daily', 'corp_events', 'corp_coverage'):
        where = 'end_day>=? AND start_day<=?' if table == 'corp_coverage' else 'day BETWEEN ? AND ?'
        order = 'start_day,end_day' if table == 'corp_coverage' else 'day,bar_end' if table == 'bars' else 'day'
        digest.update(table.encode())
        for r in store.db.execute(f'SELECT * FROM {table} WHERE symbol=? AND {where} ORDER BY {order}', (symbol, start, end)):
            digest.update((canonical(dict(r)) + '\n').encode())
    return digest.hexdigest()


def enroll(store, experiment_id=1, *, now=None):
    """Caller owns BEGIN IMMEDIATE; never call from status/report/date preview."""
    row = selected(store, experiment_id)
    verify_digest(store, row)
    preview = discover(store, experiment_id, now=now)
    own_revealed = set(revealed_days(store, experiment_id))
    for day in preview['excluded_exposed_dates']:
        if day not in own_revealed:
            store.db.execute('INSERT OR IGNORE INTO forward_exclusions VALUES (?,?,?,?)',
                (experiment_id, day, 'already_exposed', now_string()))
    calendar = tuple(r[0] for r in store.db.execute('SELECT day FROM daily WHERE symbol=? ORDER BY day', ('2330',)))
    for day in preview['dates']:
        if store.db.execute('SELECT 1 FROM forward_days WHERE experiment_id=? AND day=?', (experiment_id, day)).fetchone():
            continue
        index = calendar.index(day)
        if index < 25:
            continue
        start = calendar[index - 25]
        digest = source_digest(store, row, start, day)
        store.db.execute('INSERT INTO forward_days VALUES (?,?,?,?,?,?)',
            (experiment_id, day, start, digest, canonical(digest_settings(row)), now_string()))
    return active_days(store, experiment_id)


def plan_for(store, experiment_id=1, *, days=None, verify=False):
    row = selected(store, experiment_id)
    days = active_days(store, experiment_id) if days is None else tuple(days)
    if not days or any(day not in active_days(store, experiment_id) for day in days):
        raise DataError('forward_empty')
    if verify:
        verify_digest(store, row)
        for day in days:
            saved = store.db.execute('SELECT * FROM forward_days WHERE experiment_id=? AND day=?', (experiment_id, day)).fetchone()
            if (saved['settings_json'] != canonical(digest_settings(row))
                    or saved['data_digest'] != source_digest(store, row, saved['warmup_start'], day)):
                raise DataError('frozen_data_changed')
        unopened = set(days) - set(revealed_days(store, experiment_id))
        if exposed_days(store, '2330', unopened):
            raise DataError('forward_exposure_changed')
    base = load_plan(store, experiment_id)
    calendar = tuple(r[0] for r in store.db.execute('''SELECT day FROM daily
        WHERE symbol=? AND day BETWEEN ? AND ? ORDER BY day''', (base.symbol, base.warmup_start, max(days))))
    return replace(base, trading_days=calendar, forward_days=days)


def record_attempt(store, run_id, day, failed):
    store.db.execute('''INSERT INTO forward_attempts VALUES (?,?,?)
        ON CONFLICT(run_id,day) DO UPDATE SET failed=failed+excluded.failed''', (run_id, day, int(failed)))


def require_complete(store, row, days):
    from .score import prediction_answer
    from .evolution_forward import frozen_model
    model = frozen_model(store)
    engine = Replay(store, plan_for(store, row['id'], days=days, verify=True))
    eligible = 0
    for t in engine.candidates('forward'):
        point = engine.prepare(t)
        outcome = engine.outcome(point)
        old = store.db.execute('SELECT * FROM outcomes WHERE experiment_id=? AND t=?', (row['id'], t.isoformat())).fetchone()
        if outcome.scorable:
            if old is None or (old['close_t'], old['close_end'], old['label']) != (point.close_t, outcome.close_end, outcome.label):
                raise DataError('forward_incomplete')
        elif old is not None:
            raise DataError('forward_incomplete')
        if not point.predictable:
            continue
        eligible += int(outcome.scorable)
        for method in FORWARD_METHODS:
            record = store.db.execute('SELECT * FROM predictions WHERE experiment_id=? AND method=? AND t=?',
                (row['id'], method, t.isoformat())).fetchone()
            try:
                if record is None:
                    raise DataError('forward_incomplete')
                prediction_answer(record, point, row['model'])
                if method == 'vol_prior':
                    expected = model.predict(point)
                    if record['answer'] != expected.answer or json.loads(record['probs_json']) != expected.probabilities:
                        raise DataError('forward_incomplete')
            except Exception:
                raise DataError('forward_incomplete') from None
    if not eligible:
        raise DataError('forward_incomplete')
    for method in FORWARD_METHODS:
        latest = store.db.execute('''SELECT r.status,s.full_split FROM runs r LEFT JOIN run_scopes s ON s.run_id=r.id
            WHERE r.experiment_id=? AND r.method=? AND r.split='forward' ORDER BY r.id DESC LIMIT 1''', (row['id'], method)).fetchone()
        if not latest or latest['status'] != 'complete' or latest['full_split'] != 1:
            raise DataError('forward_incomplete')


def reveal_forward(store, experiment_id=1):
    with store.transaction():
        row = selected(store, experiment_id)
        days = active_days(store, experiment_id)
        if not days:
            raise DataError('forward_empty')
        require_complete(store, row, days)
        existing = set(revealed_days(store, experiment_id))
        # One exact-day interval per new member: gaps can never disclose an
        # excluded or newly arriving date merely because it lies in a range.
        for day in days:
            if day not in existing:
                store.db.execute('''INSERT INTO reveals
                    (experiment_id,revealed_at,first_day,last_day,what,segment) VALUES (?,?,?,?,?,'forward')''',
                    (experiment_id, now_string(), day, day, 'all'))
