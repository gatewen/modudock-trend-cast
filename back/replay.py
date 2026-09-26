"""Point-in-time f1 inputs, separate retrospective outcomes, exact labels.

No experiment creation or writes here. ReplayPlan is the boundary for the next
block's frozen experiments. Keep the database snapshot stable during a replay.
"""
from bisect import bisect_left
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from fractions import Fraction
import hashlib
import json

from .data import DataError, MAPPING, TAIPEI, day_value, decimal_value, local_time, symbol_value

FEATURE_VERSION = 'f1'
CLOCKS = tuple(f'{minute // 60:02d}:{minute % 60:02d}' for minute in range(570, 781, 30))
LABELS = ('up', 'flat', 'down')


def threshold_value(value):
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise DataError('invalid_threshold')
    return value


def exact(value, *, positive=True):
    return Fraction(decimal_value(value, positive=positive))


def label_for_prices(close_t, close_end, threshold_permille=3):
    """Fraction comparisons use integer cross-products, never float/Decimal context."""
    k = threshold_value(threshold_permille)
    start, end = exact(close_t), exact(close_end)
    if end * 1000 >= start * (1000 + k):
        return 'up'
    if end * 1000 <= start * (1000 - k):
        return 'down'
    return 'flat'


def relative_permille(value, reference):
    ref = exact(reference)
    # round(Fraction) implements exact integer ROUND_HALF_EVEN, including ties.
    return round((exact(value) - ref) * 1000 / ref)


def volume_ratio(value, samples):
    if not samples:
        return None
    total = sum(samples, Fraction(0))
    if total == 0:
        return None
    return round(exact(value, positive=False) * len(samples) * 100 / total) / 100


def minute_index(ts_raw):
    stamp = local_time(ts_raw)
    return stamp.hour * 60 + stamp.minute - 540


@dataclass(frozen=True)
class ReplayPlan:
    trading_days: tuple[str, ...] = field(repr=False)
    dev_start: str
    dev_end: str
    hold_start: str = field(repr=False)
    hold_end: str = field(repr=False)
    symbol: str = '2330'
    threshold_permille: int = 3
    feature_version: str = FEATURE_VERSION
    experiment_id: int | None = None
    warmup_start: str | None = None

    def __post_init__(self):
        symbol_value(self.symbol)
        threshold_value(self.threshold_permille)
        days = tuple(self.trading_days)
        if not days or days != tuple(sorted(set(days))):
            raise DataError('invalid_trading_calendar')
        for day in days:
            day_value(day)
        for day in (self.dev_start, self.dev_end, self.hold_start, self.hold_end):
            if day not in days:
                raise DataError('split_outside_calendar')
        if not self.dev_start <= self.dev_end < self.hold_start <= self.hold_end:
            raise DataError('invalid_split')
        if self.feature_version != FEATURE_VERSION:
            raise DataError('unsupported_feature_version')
        warmup = self.warmup_start or days[0]
        if warmup not in days or warmup > self.dev_start:
            raise DataError('invalid_warmup_start')
        object.__setattr__(self, 'trading_days', days)
        object.__setattr__(self, 'warmup_start', warmup)

    @classmethod
    def from_experiment(cls, row, trading_days):
        """Adapt the existing experiments schema without creating/finalizing a row."""
        row = dict(row)
        config = json.loads(row['config_json'])
        if config.get('mapping', MAPPING) != MAPPING:
            raise DataError('unsupported_time_mapping')
        return cls(tuple(trading_days), row['dev_start'], row['dev_end'],
                   row['hold_start'], row['hold_end'], symbol=config.get('symbol', '2330'),
                   threshold_permille=row['threshold_permille'], feature_version=row['feature_version'],
                   experiment_id=row['id'], warmup_start=config.get('warmup_start'))

    def split_of(self, day):
        if self.dev_start <= day <= self.dev_end:
            return 'dev'
        if self.hold_start <= day <= self.hold_end:
            return 'holdout'
        return None

    def days_for(self, split):
        if split not in ('dev', 'holdout'):
            raise DataError('invalid_split_name')
        return tuple(day for day in self.trading_days if self.split_of(day) == split)

    def prior_days(self, day, count=25):
        position = bisect_left(self.trading_days, day)
        return tuple(d for d in self.trading_days[max(0, position - count):position]
                     if d >= self.warmup_start)


def temporary_plan(store, symbol='2330', threshold_permille=3):
    """Audit only: floor(70% of stored trading days), including initial warmup days.

    This is not a frozen experiment and performs no holdout reveal or DB writes.
    """
    symbol_value(symbol)
    days = tuple(row[0] for row in store.db.execute(
        'SELECT day FROM daily WHERE symbol=? ORDER BY day', (symbol,)))
    cut = len(days) * 7 // 10
    if cut == 0 or cut == len(days):
        raise DataError('insufficient_calendar_for_split')
    return ReplayPlan(days, days[0], days[cut - 1], days[cut], days[-1],
                      symbol=symbol, threshold_permille=threshold_permille)


@dataclass(frozen=True)
class PreparedPoint:
    t: datetime
    experiment_id: int | None
    symbol: str
    threshold_permille: int
    predictable: bool
    reason: str | None
    input_json: str | None = field(default=None, repr=False)
    input_hash: str | None = None
    close_t: str | None = field(default=None, repr=False)
    visible_bars: tuple = field(default=(), repr=False, compare=False)

    @property
    def state(self):
        # A fresh copy prevents consumers from changing the stored canonical JSON.
        return json.loads(self.input_json) if self.input_json is not None else None


@dataclass(frozen=True)
class Outcome:
    t: datetime
    scorable: bool
    reason: str | None
    revealed_at: datetime
    close_end: str | None = field(default=None, repr=False)
    label: str | None = field(default=None, repr=False)


@dataclass(frozen=True)
class LabelObservation:
    t: datetime
    predictable: bool
    scorable: bool
    label: str | None = field(repr=False)
    experiment_id: int | None = None
    symbol: str = '2330'
    threshold_permille: int = 3


def observation(point, outcome):
    if point.t != outcome.t:
        raise DataError('outcome_point_mismatch')
    return LabelObservation(point.t, point.predictable, outcome.scorable, outcome.label,
                            point.experiment_id, point.symbol, point.threshold_permille)


def persist_outcome(store, point, outcome):
    """Caller owns the write transaction; immutable labels accompany commits."""
    if not outcome.scorable:
        return
    values = (point.close_t, outcome.close_end, outcome.label)
    old = store.db.execute('SELECT close_t,close_end,label FROM outcomes WHERE experiment_id=? AND t=?',
                          (point.experiment_id, point.t.isoformat())).fetchone()
    if old is not None and tuple(old) != values:
        raise DataError('outcome_mismatch')
    store.db.execute('INSERT OR IGNORE INTO outcomes VALUES (?,?,?,?,?)',
                     (point.experiment_id, point.t.isoformat(), *values))


class Replay:
    def __init__(self, store, plan):
        self.store, self.plan = store, plan
        self._past_cache = OrderedDict()
        self._revision = None

    def candidates(self, split='dev'):
        for day in self.plan.days_for(split):
            for clock in CLOCKS:
                yield datetime.fromisoformat(day + 'T' + clock).replace(tzinfo=TAIPEI)

    def _point_time(self, t):
        t = local_time(t)
        day = t.date().isoformat()
        if (day not in self.plan.trading_days or self.plan.split_of(day) is None
                or t.strftime('%H:%M') not in CLOCKS or t.second or t.microsecond):
            raise DataError('invalid_prediction_point')
        return t

    def _refresh_cache(self):
        revision = (self.store.db.total_changes, self.store.db.execute('PRAGMA data_version').fetchone()[0])
        if revision != self._revision:
            self._past_cache.clear()
            self._revision = revision

    def _past_day(self, day, asof_day):
        if day >= asof_day:
            raise DataError('history_not_in_past')
        if day not in self._past_cache:
            daily = self.store.db.execute('SELECT * FROM daily WHERE symbol=? AND day=?',
                                          (self.plan.symbol, day)).fetchone()
            bars = self.store.available_bars(day, day + 'T13:30:00+08:00', self.plan.symbol)
            volumes = {minute_index(bar['ts_raw']): exact(bar['volume'], positive=False) for bar in bars}
            self._past_cache[day] = (dict(daily) if daily else None, volumes)
            if len(self._past_cache) > 64:
                self._past_cache.popitem(last=False)
        self._past_cache.move_to_end(day)
        return self._past_cache[day]

    def _reference(self, day, previous_close):
        status = self.store.corp_state(self.plan.symbol, day)
        if status['state'] == 'unknown':
            return None
        if status['state'] == 'event':
            return status['event']['ref_price']
        return previous_close

    def prepare(self, t):
        """No today daily candle, future bars, outcomes or future availability reads."""
        t = self._point_time(t)
        self._refresh_cache()
        day = t.date().isoformat()
        visible = tuple(self.store.available_bars(day, t, self.plan.symbol))
        close_t = visible[-1]['close'] if visible else None

        def reject(reason):
            return PreparedPoint(t, self.plan.experiment_id, self.plan.symbol,
                                 self.plan.threshold_permille, False, reason,
                                 close_t=close_t, visible_bars=visible)

        if not visible:
            return reject('no_current_bar')
        if local_time(visible[-1]['bar_end']) < t - timedelta(minutes=5):
            return reject('stale_current_price')
        prior = self.plan.prior_days(day, 25)
        if len(prior) != 25:
            return reject('insufficient_warmup')
        history = [self._past_day(d, day) for d in prior]
        if any(daily is None or not volumes for daily, volumes in history):
            return reject('incomplete_prior_day')
        reference = self._reference(day, history[-1][0]['close'])
        if reference is None:
            return reject('unknown_current_reference')
        prev_days = []
        for i in range(20, 25):
            daily = history[i][0]
            prev_reference = self._reference(prior[i], history[i - 1][0]['close'])
            if prev_reference is None:
                return reject('unknown_prior_reference')
            mean_samples = [exact(past[0]['volume'], positive=False) for past in history[i - 20:i]]
            prev_days.append([relative_permille(daily[k], prev_reference) for k in ('open', 'high', 'low', 'close')]
                             + [volume_ratio(daily['volume'], mean_samples)])
        recent = []
        for bar in visible[-60:]:
            index = minute_index(bar['ts_raw'])
            samples = [volumes[index] for _, volumes in history[-20:] if index in volumes]
            ratio = volume_ratio(bar['volume'], samples) if len(samples) >= 10 else None
            recent.append([index, relative_permille(bar['close'], close_t), ratio])
        today = {'open': relative_permille(visible[0]['open'], reference),
                 'high': relative_permille(max(visible, key=lambda b: exact(b['high']))['high'], reference),
                 'low': relative_permille(min(visible, key=lambda b: exact(b['low']))['low'], reference),
                 'close': relative_permille(close_t, reference)}
        state = {'clock': t.strftime('%H:%M'), 'minutes_to_close': 810 - (t.hour * 60 + t.minute),
                 'today': today, 'recent': recent, 'prev_days': prev_days}
        serialized = json.dumps(state, sort_keys=True, separators=(',', ':'), allow_nan=False)
        digest = hashlib.sha256(serialized.encode('utf-8')).hexdigest()
        return PreparedPoint(t, self.plan.experiment_id, self.plan.symbol,
                             self.plan.threshold_permille, True, None, serialized, digest, close_t, visible)

    def outcome(self, point):
        """Retrospective only. Eligibility/state are never changed by this method."""
        if (point.experiment_id != self.plan.experiment_id or point.symbol != self.plan.symbol
                or point.threshold_permille != self.plan.threshold_permille):
            raise DataError('point_plan_mismatch')
        t = self._point_time(point.t)
        end = t + timedelta(minutes=30)

        def reject(reason):
            return Outcome(t, False, reason, end)

        if point.close_t is None:
            return reject('no_start_price')
        bars = self.store.available_bars(t.date().isoformat(), end, self.plan.symbol)
        if not bars or local_time(bars[-1]['bar_end']) < t + timedelta(minutes=25):
            return reject('missing_or_stale_endpoint')
        if t.time() == time(13) and local_time(bars[-1]['ts_raw']).time() != time(13, 30):
            return reject('missing_closing_auction')
        close_end = bars[-1]['close']
        return Outcome(t, True, None, end, close_end,
                       label_for_prices(point.close_t, close_end, self.plan.threshold_permille))
