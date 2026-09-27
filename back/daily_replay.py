"""Daily, causal features and exact corporate-action-adjusted return labels.

prepare() never calls outcome(). Calendar membership, not present candles,
defines H trading days. Threshold fitting is bounded at both development ends.
"""
from collections import Counter
from dataclasses import dataclass, field
from fractions import Fraction
import hashlib
from math import isqrt

from .data import DataError, day_value
from .experiment import canonical
from .daily_sync import START

HORIZONS = (3, 7, 14)
DEV_START = '2010-04-01'
DEV_END = '2021-12-31'
HOLD_START = '2022-01-03'
HOLD_END = '2024-07-25'
WARMUP = 60


def horizon(value):
    if type(value) is not int or value not in HORIZONS:
        raise DataError('invalid_daily_horizon')
    return value


def label(return_value, threshold):
    if not isinstance(return_value, Fraction) or not isinstance(threshold, Fraction) or threshold <= 0:
        raise DataError('invalid_daily_label_arguments')
    if return_value >= threshold: return 'up'
    if return_value <= -threshold: return 'down'
    return 'flat'


def threshold_for_returns(values):
    """Population sigma / 2, HALF_UP to .005; exact squared comparisons, no float.

    Rounded step count = round_half_up(100 * sqrt(population variance)).
    Compare to (floor + 1/2)^2 before deciding the rounding direction.
    """
    values=tuple(values)
    if len(values)<2 or any(not isinstance(v,Fraction) for v in values):
        raise DataError('insufficient_development_returns')
    n=len(values)
    mean=sum(values,Fraction())/n
    variance=sum((v*v for v in values),Fraction())/n-mean*mean
    scaled=variance*10000
    steps=isqrt(scaled.numerator//scaled.denominator)
    if 4*scaled.numerator >= (2*steps+1)**2*scaled.denominator:
        steps+=1
    if steps==0: raise DataError('zero_daily_threshold')
    return Fraction(steps,200)


@dataclass(frozen=True)
class DailyPoint:
    day: str
    predictable: bool
    reason: str | None = None
    input_json: str | None = field(default=None,repr=False)
    input_hash: str | None = None


@dataclass(frozen=True)
class DailyOutcome:
    day: str
    H: int
    scorable: bool
    reason: str | None = None
    end_day: str | None = None
    adjusted_return: Fraction | None = field(default=None,repr=False)


class DailyReplay:
    def __init__(self,store,*,symbol='2330',start=START):
        day_value(start)
        self.store,self.symbol,self.start=store,symbol,start

    def calendar(self,first,last):
        return tuple(r[0] for r in self.store.db.execute(
            'SELECT day FROM d_calendar WHERE day BETWEEN ? AND ? ORDER BY day',(first,last)))

    def bar(self,day):
        return self.store.db.execute('SELECT * FROM d_bars WHERE symbol=? AND day=?',(self.symbol,day)).fetchone()

    def reference(self,day,previous):
        status=self.store.corp_state(day,self.symbol)
        if status['state']=='unknown': raise DataError('unknown_corporate_action')
        if status['state']=='event':
            event=status['event']
            if Fraction(event['prev_close'])!=Fraction(previous['close']):
                raise DataError('corporate_previous_close_mismatch')
            return Fraction(event['ref_price'])
        return Fraction(previous['close'])

    def chips(self,day):
        """13:30 forecast: previous trading day or older ONLY, never same-day data.

        Foreign includes foreign dealers, maintaining pre/post-2017 continuity.
        A missing required category is None, never a fictitious zero net trade.
        """
        prior=tuple(r[0] for r in self.store.db.execute('''SELECT day FROM d_calendar
            WHERE day>=? AND day<? ORDER BY day DESC LIMIT 60''',(self.start,day)))[::-1]
        result=[]
        for d in prior:
            groups={r['name']:r['buy']-r['sell'] for r in self.store.db.execute(
                'SELECT name,buy,sell FROM d_institutional WHERE symbol=? AND day=?',(self.symbol,d))}
            foreign=None
            if 'Foreign_Investor' in groups and (d<'2017-12-18' or 'Foreign_Dealer_Self' in groups):
                foreign=groups['Foreign_Investor']+groups.get('Foreign_Dealer_Self',0)
            margin=self.store.db.execute('SELECT margin_balance FROM d_margin WHERE symbol=? AND day=?',(self.symbol,d)).fetchone()
            result.append([foreign,groups.get('Investment_Trust'),margin[0] if margin else None])
        return result

    def prepare(self,day):
        day_value(day)
        past=tuple(r[0] for r in self.store.db.execute('''SELECT day FROM d_calendar
            WHERE day>=? AND day<=? ORDER BY day DESC LIMIT 61''',(self.start,day)))[::-1]
        if not past or past[-1]!=day: return DailyPoint(day,False,'not_trading_day')
        if len(past)<WARMUP+1: return DailyPoint(day,False,'insufficient_warmup')
        bars=[self.bar(d) for d in past]
        if any(row is None for row in bars): return DailyPoint(day,False,'missing_history_day')
        stored_days=tuple(r[0] for r in self.store.db.execute('''SELECT day FROM d_bars
            WHERE symbol=? AND day BETWEEN ? AND ? ORDER BY day''',(self.symbol,past[0],past[-1])))
        if stored_days!=past: return DailyPoint(day,False,'history_calendar_mismatch')
        features=[]
        try:
            for i in range(1,len(past)):
                row,previous=bars[i],bars[i-1]
                ref=self.reference(past[i],previous)
                # Use the actual preceding 20 trading sessions for each row.
                before=tuple(r[0] for r in self.store.db.execute('''SELECT day FROM d_calendar
                    WHERE day>=? AND day<? ORDER BY day DESC LIMIT 20''',(self.start,past[i])))
                volumes=[self.bar(d) for d in before]
                avg=(sum((Fraction(r['volume']) for r in volumes),Fraction())/20
                     if len(volumes)==20 and all(r is not None for r in volumes) else None)
                relative_volume=float(Fraction(row['volume'])/avg) if avg else None
                features.append([round(float((Fraction(row[k])/ref-1)*1000),8)
                    for k in ('open','high','low','close')]+[relative_volume])
        except DataError as exc:
            return DailyPoint(day,False,str(exc))
        state={'daily':features,'chips_previous_sessions':self.chips(day)}
        serialized=canonical(state)
        return DailyPoint(day,True,None,serialized,hashlib.sha256(serialized.encode()).hexdigest())

    def outcome(self,day,H,*,end_limit=None):
        day_value(day);horizon(H)
        def fail(reason): return DailyOutcome(day,H,False,reason)
        if end_limit is None:
            days=tuple(r[0] for r in self.store.db.execute('SELECT day FROM d_calendar WHERE day>=? ORDER BY day LIMIT ?',(day,H+1)))
        else:
            day_value(end_limit)
            days=tuple(r[0] for r in self.store.db.execute('''SELECT day FROM d_calendar
                WHERE day>=? AND day<=? ORDER BY day LIMIT ?''',(day,end_limit,H+1)))
        if not days or days[0]!=day: return fail('not_trading_day')
        if len(days)!=H+1: return fail('endpoint_unavailable')
        bars=[self.bar(d) for d in days]
        if any(r is None for r in bars): return fail('missing_interval_day')
        stored_days=tuple(r[0] for r in self.store.db.execute('''SELECT day FROM d_bars
            WHERE symbol=? AND day BETWEEN ? AND ? ORDER BY day''',(self.symbol,days[0],days[-1])))
        if stored_days!=days: return fail('interval_calendar_mismatch')
        if self.store.corp_state(day,self.symbol)['state']=='unknown':return fail('unknown_corporate_action')
        total=Fraction(1)
        try:
            for i in range(1,len(days)):
                ref=self.reference(days[i],bars[i-1])
                total*=Fraction(bars[i]['close'])/ref
        except DataError as exc:
            return fail(str(exc))
        return DailyOutcome(day,H,True,None,days[-1],total-1)


def development_thresholds(engine,*,dev_start=DEV_START,dev_end=DEV_END):
    if not DEV_START<=dev_start<=dev_end<=DEV_END:
        raise DataError('daily_threshold_outside_development')
    returns={H:[] for H in HORIZONS}
    dates={H:[] for H in HORIZONS}
    for day in engine.calendar(dev_start,dev_end):
        if not engine.prepare(day).predictable: continue
        for H in HORIZONS:
            outcome=engine.outcome(day,H,end_limit=dev_end)
            if outcome.scorable:
                returns[H].append(outcome.adjusted_return);dates[H].append(day)
    result={}
    for H in HORIZONS:
        k=threshold_for_returns(returns[H])
        counts=Counter(label(value,k) for value in returns[H])
        result[H]=dict(k_numerator=k.numerator,k_denominator=k.denominator,k_percent=float(k*100),
            n=len(returns[H]),first_point=dates[H][0],last_point=dates[H][-1],
            labels={name:counts[name] for name in ('up','flat','down')})
    return result
