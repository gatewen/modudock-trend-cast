"""As-of joins. Prediction date, cutoff date, and row age are separate notions."""
from bisect import bisect_right
from dataclasses import replace
from datetime import date,timedelta
from fractions import Fraction

from .daily_replay import DEV_START,DEV_END
from .data import DataError
from .market_sources import SOURCES,START,positive


class MarketHistory:
    def __init__(self, rows):
        self.rows={s:[] for s in SOURCES}
        for r in rows:self.rows[r['series']].append(dict(r))
        for values in self.rows.values():values.sort(key=lambda r:r['day'])
        self.days={s:[r['day'] for r in rows] for s,rows in self.rows.items()}
        if any(len(days)!=len(set(days)) for days in self.days.values()):raise DataError('market_duplicate_day')

    @classmethod
    def read(cls,store):
        return cls(store.db.execute('SELECT * FROM market_rows WHERE day BETWEEN ? AND ? ORDER BY series,day',(START,DEV_END)))

    def available(self,series,day):
        d=date.fromisoformat(day)
        cutoff=d if series=='taiex' else d-timedelta(days=1)
        i=bisect_right(self.days[series],cutoff.isoformat())-1
        if i<0:return [],dict(day=None,age=None,status='missing')
        row=self.rows[series][i]
        age=(d-date.fromisoformat(row['day'])).days
        info=dict(day=row['day'],age=age,status='stale' if age>5 else 'available')
        if age>5:return [],info
        return self.rows[series][:i+1],info


def quote(row,key='close'):
    value=positive(row.get(key))
    return Fraction(value) if value is not None else None


def window(rows,count):
    if len(rows)<count:return None
    values=[quote(r) for r in rows[-count:]]
    return values if all(v is not None for v in values) else None


def trend(rows,count):
    values=window(rows,count)
    if values is None:return None,'missing'
    mean=sum(values,Fraction())/count
    value=values[-1]/mean-1
    return float(value),'bull' if value>0 else 'bear' if value<0 else 'neutral'


def returns(rows,lag):
    values=window(rows,lag+1)
    return float(values[-1]/values[0]-1) if values is not None else None


def features(point,history,raw_close):
    if not DEV_START<=point.day<=DEV_END:raise DataError('market_features_dev_only')
    return asof_features(point,history,raw_close)


def asof_features(point,history,raw_close):
    """Pure as-of calculation; callers own their explicit split boundary."""
    data={};alignment={}
    for series in SOURCES:data[series],alignment[series]=history.available(series,point.day)
    values={};states={}
    values['mkt_trend'],states['mkt_trend']=trend(data['taiex'],60)
    values['mkt_ret5']=returns(data['taiex'],5)
    values['sox_ret1']=returns(data['sox'],1)
    values['sox_trend'],states['sox_trend']=trend(data['sox'],20)
    adr=quote(data['tsm'][-1]) if data['tsm'] else None
    fx=data['usd_twd'][-1] if data['usd_twd'] else {}
    buy,sell=quote(fx,'spot_buy'),quote(fx,'spot_sell')
    stock=positive(raw_close)
    values['adr_premium']=float((adr*((buy+sell)/2)/5)/Fraction(stock)-1) if all(
        v is not None for v in (adr,buy,sell,stock)) else None
    # Tercile state depends on matured labels; only missing is known here.
    for name in ('mkt_ret5','adr_premium','sox_ret1'):
        if values[name] is None:states[name]='missing'
    return replace(point,values=dict(point.values,**values),states=dict(point.states,**states)),alignment


def augment(store,points):
    history=MarketHistory.read(store)
    raw={r['day']:r['close'] for r in store.db.execute(
        'SELECT day,close FROM d_bars WHERE symbol=? AND day BETWEEN ? AND ?',('2330',DEV_START,DEV_END))}
    enriched=[];alignment={}
    for point in points:
        frame,info=features(point,history,raw.get(point.day))
        enriched.append(frame);alignment[point.day]=info
    return tuple(enriched),alignment
