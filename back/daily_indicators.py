"""Forward-adjusted indicators: one chronological pass, no backward adjustment.

A frame depends only on prices <= day and chip releases strictly before day.
Tercile states are deliberately absent here: their cuts require matured labels.
"""
from dataclasses import dataclass, field
from fractions import Fraction
import hashlib
import math
from statistics import fmean, pstdev

from .daily_replay import WARMUP
from .experiment import canonical


@dataclass(frozen=True)
class Frame:
    day: str
    index: int
    predictable: bool
    values: dict = field(repr=False)
    states: dict = field(repr=False)
    volatility: float | None = None
    past_returns: dict = field(default_factory=dict, repr=False)

    def serialized(self):
        return canonical(dict(day=self.day, index=self.index, predictable=self.predictable,
                              values=self.values, states=self.states, volatility=self.volatility,
                              past_returns={str(h): [v.numerator,v.denominator] for h,v in self.past_returns.items()}))

    @property
    def digest(self):
        return hashlib.sha256(self.serialized().encode()).hexdigest()


def sign(value):
    return 'missing' if value is None else 'positive' if value>0 else 'negative' if value<0 else 'zero'


def chip_values(calendar, i, institutional, margins):
    # t=13:30; same-day evening releases are unavailable.
    available = calendar[:i]
    groups = []
    for day in available[-3:]:
        row = institutional.get(day, {})
        foreign = None
        if 'Foreign_Investor' in row and (day<'2017-12-18' or 'Foreign_Dealer_Self' in row):
            foreign = row['Foreign_Investor']+row.get('Foreign_Dealer_Self',0)
        groups.append((foreign,row.get('Investment_Trust')))
    net = [sum(r[c] for r in groups) if len(groups)==3 and all(r[c] is not None for r in groups) else None for c in (0,1)]
    change = None
    if len(available)>=6:
        a,b=margins.get(available[-1]),margins.get(available[-6])
        if a is not None and b is not None: change=a-b
    return dict(foreign_net=net[0],trust_net=net[1],margin_chg=change)


def frames(store, *, start='2010-01-04', end, symbol='2330'):
    """Read a bounded prefix; a later row can never affect an earlier frame."""
    db=store.db
    days=tuple(r[0] for r in db.execute('SELECT day FROM d_calendar WHERE day BETWEEN ? AND ? ORDER BY day',(start,end)))
    bars={r['day']:dict(r) for r in db.execute('SELECT * FROM d_bars WHERE symbol=? AND day BETWEEN ? AND ?',(symbol,start,end))}
    extra=set(bars)-set(days)
    inst={}
    for r in db.execute('SELECT * FROM d_institutional WHERE symbol=? AND day BETWEEN ? AND ?',(symbol,start,end)):
        inst.setdefault(r['day'],{})[r['name']]=r['buy']-r['sell']
    margins={r['day']:r['margin_balance'] for r in db.execute('SELECT * FROM d_margin WHERE symbol=? AND day BETWEEN ? AND ?',(symbol,start,end))}
    close=[];high=[];low=[];volume=[];gross=[];crosses=[];result=[]
    ema12=ema26=signal=None
    k=d=50.
    avg_gain=avg_loss=None
    gains=[];losses=[]
    previous_spread=None;previous_hist=None
    valid=True
    for i,day in enumerate(days):
        row=bars.get(day)
        status=store.corp_state(day,symbol)
        if row is None or status['state']=='unknown': valid=False
        if i and any(days[i-1]<unexpected<day for unexpected in extra): valid=False
        if not valid:
            result.append(Frame(day,i,False,{},{}));continue
        raw=Fraction(row['close'])
        if i:
            previous=bars[days[i-1]]
            if status['state']=='event':
                event=status['event']
                if Fraction(event['prev_close'])!=Fraction(previous['close']):
                    valid=False;result.append(Frame(day,i,False,{},{}));continue
                ref=Fraction(event['ref_price'])
            else: ref=Fraction(previous['close'])
            gross.append(raw/ref)
            scale=close[-1]/float(ref)
        else: scale=1./float(raw)
        c=float(raw)*scale
        close.append(c);high.append(float(row['high'])*scale);low.append(float(row['low'])*scale)
        volume.append(float(row['volume']))
        ema12=c if ema12 is None else ema12+(c-ema12)*2/13
        ema26=c if ema26 is None else ema26+(c-ema26)*2/27
        dif=ema12-ema26
        signal=dif if signal is None else signal+(dif-signal)*2/10
        hist=dif-signal
        macd=('bull_flip' if previous_hist is not None and previous_hist<0<hist else
              'bear_flip' if previous_hist is not None and previous_hist>0>hist else
              'positive' if hist>0 else 'negative' if hist<0 else 'zero')
        previous_hist=hist
        if i:
            change=c-close[-2]
            gains.append(max(change,0));losses.append(max(-change,0))
            if len(gains)==14: avg_gain=fmean(gains);avg_loss=fmean(losses)
            elif len(gains)>14:
                avg_gain=(avg_gain*13+gains[-1])/14;avg_loss=(avg_loss*13+losses[-1])/14
        rsi=(50. if avg_gain==avg_loss==0 else 100. if avg_loss==0 else
             100-100/(1+avg_gain/avg_loss)) if avg_gain is not None else None
        prev_k,prev_d=k,d
        if len(close)>=9:
            hi=max(high[-9:]);lo=min(low[-9:])
            rsv=50. if hi==lo else (c-lo)/(hi-lo)*100
            k=(2*k+rsv)/3;d=(2*d+k)/3
        kd=('bull_cross' if k<20 and prev_k<=prev_d and k>d else
            'bear_cross' if k>80 and prev_k>=prev_d and k<d else
            'low' if k<20 else 'high' if k>80 else 'middle')
        if len(close)<20:
            result.append(Frame(day,i,False,{},{}));continue
        mean=fmean(close[-20:]);sd=pstdev(close[-20:]);spread=fmean(close[-5:])-mean
        crossing=('bull' if previous_spread is not None and previous_spread<=0<spread else
                  'bear' if previous_spread is not None and previous_spread>=0>spread else None)
        if crossing: crosses.append((i,crossing))
        previous_spread=spread
        ma_cross=crosses[-1][1] if crosses and i-crosses[-1][0]<3 else 'neutral'
        mean60=fmean(close[-60:]) if len(close)>=60 else None
        avg_volume=fmean(volume[-20:])
        ratio=volume[-1]/avg_volume if avg_volume else None
        ret=float(gross[-1]-1) if gross else 0.
        direction='up' if ret>0 else 'down' if ret<0 else 'flat'
        chips=chip_values(days,i,inst,margins)
        values=dict(ma_cross=spread/mean, ma_trend=c/mean60-1 if mean60 else None,
                    rsi14=rsi,kd_k=k,kd_d=d,macd_dif=dif/c,macd_signal=signal/c,macd_hist=hist/c,
                    bollinger=(c-mean)/sd if sd else 0.,bias20=c/mean-1,
                    vol_price_return=ret,vol_price_ratio=ratio,**chips)
        states=dict(ma_cross=ma_cross,ma_trend='bull' if mean60 and c>mean60 else 'bear' if mean60 and c<mean60 else 'neutral',
                    rsi14='oversold' if rsi is not None and rsi<30 else 'overbought' if rsi is not None and rsi>70 else 'middle',
                    kd=kd,macd=macd,bollinger='above' if c>mean+2*sd else 'below' if c<mean-2*sd else 'inside',
                    vol_price=direction+('_high' if ratio>=1 else '_low') if ratio is not None else 'missing',
                    **{name:sign(value) for name,value in chips.items()})
        vol=pstdev([float(g-1) for g in gross[-20:]]) if len(gross)>=20 else None
        past={h:math.prod(gross[-h:],start=Fraction(1))-1 for h in (3,7,14) if len(gross)>=h}
        result.append(Frame(day,i,i>=WARMUP,values,states,vol,past))
    return tuple(result)
