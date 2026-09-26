"""Atomic experiment snapshots and persistent, cross-experiment exposure history.

Use the same ActivityGate for sync, replay and creation. Call create_experiment
on the DB owner (DBWriter.submit), never on the protocol control thread.
"""
from contextlib import contextmanager
from datetime import datetime, timedelta
import hashlib
import json
import threading

from .data import DataError, MAPPING, TAIPEI, day_value, symbol_value
from .replay import FEATURE_VERSION, ReplayPlan, threshold_value
from .prompts import PROMPT_VERSION, instructions
from .store import now_string

MODEL = 'jev-1.13.0'
FINAL_EXPERIMENT = 1  # SPEC 13.4: chosen from development Brier, p1 < p2.


def require_final_holdout(experiment_id, prompt_version='p1'):
    if type(experiment_id) is not int or experiment_id != FINAL_EXPERIMENT or prompt_version != 'p1':
        raise DataError('holdout_experiment_forbidden')


# Already shown to the user before experiments existed; explicitly grandfathered
# by the block-3 task. This is an exposure record, not a fabricated experiment.
BLOCK2_EXPOSURE = ('2330', '2024-07-26', '2026-01-27', 'block-2-real-dev-audit')


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False,
                      allow_nan=False)


class ActivityGate:
    """Short, in-memory atomic reservations. No lock is held during DB/network I/O.

    Sync and replay have separate counters. Cancellation of replay never touches
    sync; a future shell must reserve sync before dispatching its first job.
    """
    def __init__(self):
        self._lock = threading.Lock()
        self._serial = {'sync': 0, 'replay': 0, 'create': 0}
        self._active = {}

    def claim(self, kind):
        if kind not in self._serial:
            raise DataError('invalid_activity')
        with self._lock:
            if ('create' in self._active or kind in self._active
                    or (kind == 'create' and self._active)):
                raise DataError('busy')
            self._serial[kind] += 1
            token = self._serial[kind]
            self._active[kind] = token
            return token

    def release(self, kind, token):
        with self._lock:
            if self._active.get(kind) == token:
                del self._active[kind]

    def busy(self, kind=None):
        with self._lock:
            return bool(self._active) if kind is None else kind in self._active

    @contextmanager
    def reservation(self, kind, token=None):
        """A supplied token remains caller-owned through post-commit binding."""
        owned = token is None
        if token is None:
            token = self.claim(kind)
        else:
            with self._lock:
                if self._active.get(kind) != token:
                    raise DataError('invalid_reservation')
        try:
            yield token
        finally:
            if owned:
                self.release(kind, token)


def experiment_row(store, experiment_id):
    row = store.db.execute('SELECT * FROM experiments WHERE id=?', (experiment_id,)).fetchone()
    if row is None:
        raise DataError('experiment_not_found')
    return dict(row)


def load_plan(store, experiment_id):
    row = experiment_row(store, experiment_id)
    config = json.loads(row['config_json'])
    days = tuple(r[0] for r in store.db.execute('''SELECT day FROM daily
        WHERE symbol=? AND day BETWEEN ? AND ? ORDER BY day''',
        (config['symbol'], config['warmup_start'], row['hold_end'])))
    return ReplayPlan.from_experiment(row, days)


def data_digest(store, settings):
    """Caller holds a consistent transaction; hash exact stored values in PK order.

    Settings include all split/version/threshold fields and config_json. Fetch
    logs and exposure records are operational metadata, not feature data.
    """
    config = json.loads(settings['config_json'])
    args = (config['symbol'], config['warmup_start'], settings['hold_end'])
    digest = hashlib.sha256()
    digest.update((canonical(settings) + '\n').encode())
    for table in ('bars', 'daily', 'corp_events', 'corp_coverage'):
        if table == 'corp_coverage':
            query = '''SELECT * FROM corp_coverage WHERE symbol=? AND end_day>=?
                AND start_day<=? ORDER BY start_day,end_day'''
        else:
            order = 'day,bar_end' if table == 'bars' else 'day'
            query = f'SELECT * FROM {table} WHERE symbol=? AND day BETWEEN ? AND ? ORDER BY {order}'
        digest.update((table + '\n').encode())
        for row in store.db.execute(query, args):
            digest.update((canonical(dict(row)) + '\n').encode())
    return digest.hexdigest()


def digest_settings(row):
    return {key: row[key] for key in ('dev_start', 'dev_end', 'hold_start', 'hold_end',
        'threshold_permille', 'feature_version', 'prompt_version', 'model', 'config_json')}


def verify_digest(store, row):
    if data_digest(store, digest_settings(row)) != row['data_digest']:
        raise DataError('frozen_data_changed')


def record_exposure(store, symbol, first_day, last_day, source):
    """Internal helper; caller owns the transaction. Source is a local audit ID."""
    symbol_value(symbol)
    if day_value(first_day) > day_value(last_day):
        raise DataError('invalid_exposure_range')
    store.db.execute('INSERT OR IGNORE INTO prior_exposures VALUES (?,?,?,?,?)',
                     (symbol, first_day, last_day, source, now_string()))


def create_experiment(store, gate, *, start, end, symbol='2330', threshold_permille=3,
                      today=None, reservation=None, prompt_version=PROMPT_VERSION):
    """Explicit inclusive evaluation range; exactly 25 prior days are warmed up.

    floor(70% * evaluation trading days) is development. No silent shortening
    of an invalid requested range. Warmup months must themselves be finalized.
    """
    symbol_value(symbol)
    threshold_value(threshold_permille)
    instructions(prompt_version)  # Reject unsupported versions before any write.
    if day_value(start) > day_value(end):
        raise DataError('invalid_experiment_range')
    today = today or datetime.now(TAIPEI).date()
    with gate.reservation('create', token=reservation), store.transaction():
        calendar = tuple(r[0] for r in store.db.execute(
            'SELECT day FROM daily WHERE symbol=? AND day<=? ORDER BY day', (symbol, end)))
        if start not in calendar or end not in calendar:
            raise DataError('range_outside_calendar')
        position = calendar.index(start)
        if position < 25:
            raise DataError('missing_warmup')
        days = calendar[position:]
        cut = len(days) * 7 // 10
        if cut == 0 or cut == len(days):
            raise DataError('insufficient_calendar_for_split')
        warmup = calendar[position - 25]
        scope = calendar[position - 25:]
        # Check every calendar month, including a month with wholly missing data.
        month_day = day_value(warmup).replace(day=1)
        while month_day.isoformat()[:7] <= end[:7]:
            if store.should_fetch(symbol, month_day.isoformat()[:7], today=today):
                raise DataError('unfinalized_month')
            month_day = (month_day.replace(day=28) + timedelta(days=4)).replace(day=1)
        bar_days = {r[0] for r in store.db.execute('''SELECT DISTINCT day FROM bars
            WHERE symbol=? AND day BETWEEN ? AND ?''', (symbol, warmup, end))}
        if bar_days != set(scope):
            raise DataError('incomplete_trading_day')
        if any(store.corp_state(symbol, day)['state'] == 'unknown' for day in scope):
            raise DataError('unknown_corporate_action')
        config = {'symbol': symbol, 'warmup_start': warmup, 'mapping': MAPPING}
        settings = dict(dev_start=days[0], dev_end=days[cut - 1], hold_start=days[cut],
                        hold_end=days[-1], threshold_permille=threshold_permille,
                        feature_version=FEATURE_VERSION, prompt_version=prompt_version,
                        model=MODEL, config_json=canonical(config))
        digest = data_digest(store, settings)
        cursor = store.db.execute('''INSERT INTO experiments
            (created_at,data_digest,dev_start,dev_end,hold_start,hold_end,threshold_permille,
             feature_version,prompt_version,model,config_json) VALUES (?,?,?,?,?,?,?,?,?,?,?)''',
            (now_string(), digest, *(settings[key] for key in ('dev_start', 'dev_end',
             'hold_start', 'hold_end', 'threshold_permille', 'feature_version',
             'prompt_version', 'model', 'config_json'))))
        record_exposure(store, *BLOCK2_EXPOSURE)
        experiment_id = cursor.lastrowid
    return experiment_row(store, experiment_id)


def exposed_days(store, symbol, days):
    """Return dates only, never prices or labels; count each overlapping day once."""
    ranges = [(r['first_day'], r['last_day']) for r in store.db.execute(
        'SELECT * FROM prior_exposures WHERE symbol=?', (symbol,))]
    for row in store.db.execute('SELECT * FROM experiments'):
        if json.loads(row['config_json']).get('symbol', '2330') != symbol:
            continue
        ranges.append((row['dev_start'], row['dev_end']))
        ranges.extend((r[0], r[1]) for r in store.db.execute(
            'SELECT first_day,last_day FROM reveals WHERE experiment_id=?', (row['id'],)))
    return tuple(day for day in sorted(set(days))
                 if any(first <= day <= last for first, last in ranges))


def holdout_overlap(store, experiment_id):
    plan = load_plan(store, experiment_id)
    return len(exposed_days(store, plan.symbol, plan.days_for('holdout')))


def reveal_holdout(store, experiment_id, *, check=None):
    """Explicitly authorized reveal; optional completion check shares its commit."""
    require_final_holdout(experiment_id)
    with store.transaction():
        row = experiment_row(store, experiment_id)
        require_final_holdout(experiment_id, row['prompt_version'])
        if check is not None:
            check(store, row)
        if holdout_revealed(store, row):
            return
        overlap = holdout_overlap(store, experiment_id)
        store.db.execute('INSERT OR IGNORE INTO reveal_context VALUES (?,?)', (experiment_id, overlap))
        store.db.execute('INSERT INTO reveals VALUES (?,?,?,?,?)',
            (experiment_id, now_string(), row['hold_start'], row['hold_end'], 'all'))


def holdout_revealed(store, row):
    """Full access belongs to this experiment; exposure alone grants no access."""
    return bool(store.db.execute('''SELECT 1 FROM reveals WHERE experiment_id=? AND what='all'
        AND first_day<=? AND last_day>=? LIMIT 1''',
        (row['id'], row['hold_start'], row['hold_end'])).fetchone())


def day_access(store, experiment_id, day):
    """Shared guard for the next block's data/report exits; exposure != permission.

    A reveal in a different experiment (or the CLI's labels-only reveal) does
    not silently unlock the entire current experiment's holdout chart.
    """
    day_value(day)
    if experiment_id is None:
        return 'experiment_required'
    plan = load_plan(store, experiment_id)
    split = plan.split_of(day) if day in plan.trading_days else None
    if split == 'dev':
        return 'allowed'
    if split != 'holdout':
        return 'day_outside_experiment'
    revealed = store.db.execute('''SELECT 1 FROM reveals WHERE experiment_id=?
        AND first_day<=? AND last_day>=? AND what='all' LIMIT 1''',
        (experiment_id, day, day)).fetchone()
    return 'allowed' if revealed else 'holdout_locked'
