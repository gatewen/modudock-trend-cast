"""Walk-forward frequencies and standard-library multiclass logistic regression."""
from bisect import bisect_left
from collections import Counter
from dataclasses import dataclass, field
import math
from statistics import fmean, pstdev

from .baselines import Prediction
from .daily_config import INDICATORS, LABELS, TIE_ORDER, STATES, NUMERIC
from .daily_replay import DEV_START, DEV_END, label, horizon
from .data import DataError
from .score import percentile


@dataclass(frozen=True)
class Record:
    frame: object = field(repr=False)
    H: int
    end_day: str
    truth: str = field(repr=False)


def matured(records, point, H):
    horizon(H)
    result=[];seen=set()
    for r in records:
        if not r.frame.predictable or not DEV_START<=r.frame.day<=r.end_day<=DEV_END: continue
        if r.frame.index+H > point.index or r.end_day > point.day: continue
        if r.H!=H or r.truth not in LABELS or r.frame.day in seen:
            raise DataError('invalid_daily_training_record')
        seen.add(r.frame.day);result.append(r)
    return tuple(result)


def probabilities(truths):
    counts=Counter(truths);n=sum(counts.values())+3
    return {name:(counts[name]+1)/n for name in LABELS}


def prediction(method, probs):
    if set(probs)!=set(LABELS) or any(not math.isfinite(p) or p<0 or p>1 for p in probs.values()) or abs(sum(probs.values())-1)>1e-10:
        raise DataError('invalid_daily_probabilities')
    return Prediction(method,max(TIE_ORDER,key=lambda name:probs[name]),probs)


def cuts(values):
    values=tuple(v for v in values if v is not None)
    return (percentile(values,1/3),percentile(values,2/3)) if values else None


def bucket(value, boundaries):
    return ('low','middle','high')[bisect_left(boundaries,value)] if value is not None and boundaries is not None else 'missing'


def indicator_state(frame, name, boundaries=None):
    return bucket(frame.values.get(name),boundaries) if name=='bias20' else frame.states.get(name,'missing')


def conditional(method, point, train, majority):
    if method=='vol_prior_d':
        boundaries=cuts(r.frame.volatility for r in train)
        state=bucket(point.volatility,boundaries)
        matches=[r.truth for r in train if bucket(r.frame.volatility,boundaries)==state]
    else:
        name=method.removeprefix('ind_')
        boundaries=cuts(r.frame.values.get('bias20') for r in train) if name=='bias20' else None
        state=indicator_state(point,name,boundaries)
        matches=[r.truth for r in train if indicator_state(r.frame,name,boundaries)==state]
    # Missing chip history is not a purported signal, even after 30 missing days.
    probs=probabilities(matches) if state!='missing' and len(matches)>=30 else majority
    return prediction(method,probs),state


def design_row(frame, means, scales, boundaries):
    numeric=tuple((frame.values.get(name)-mean)/scale if frame.values.get(name) is not None else 0.
                  for name,mean,scale in zip(NUMERIC,means,scales))
    onehot=[]
    for name in INDICATORS:
        state=indicator_state(frame,name,boundaries)
        onehot.extend(float(state==level) for level in (*STATES[name],'missing'))
    return (1.,*numeric,*onehot)


def softmax(values):
    maximum=max(values)
    exps=[math.exp(v-maximum) for v in values];total=sum(exps)
    return tuple(v/total for v in exps)


@dataclass
class Logistic:
    means: tuple
    scales: tuple
    boundaries: tuple | None
    weights: tuple = field(repr=False)
    train_n: int
    train_last_day: str
    train_last_end: str
    fit_day: str
    fit_index: int

    @classmethod
    def fit(cls, train, point):
        if len(train)<250: raise DataError('insufficient_logit_training')
        # All fitting quantities, including dynamic categorical cuts, share train.
        columns=[[r.frame.values.get(name) for r in train if r.frame.values.get(name) is not None] for name in NUMERIC]
        means=tuple(fmean(values) if values else 0. for values in columns)
        scales=tuple(pstdev(values) or 1. if values else 1. for values in columns)
        boundaries=cuts(r.frame.values.get('bias20') for r in train)
        rows=tuple(design_row(r.frame,means,scales,boundaries) for r in train)
        columns=tuple(zip(*rows));size=len(rows);width=len(rows[0])
        weights=[[0.]*width for _ in LABELS]
        targets=tuple(tuple(float(r.truth==name) for r in train) for name in LABELS)
        # math.sumprod performs the inner products in C (Python 3.12 standard lib).
        dot=math.sumprod
        for _ in range(300):
            fitted=tuple(softmax(tuple(dot(row,w) for w in weights)) for row in rows)
            residuals=tuple(tuple(p[c]-y for p,y in zip(fitted,targets[c])) for c in range(3))
            weights=[[w[j]-.1*(dot(column,residuals[c])/size+(w[j] if j else 0.))
                      for j,column in enumerate(columns)] for c,w in enumerate(weights)]
        return cls(means,scales,boundaries,tuple(tuple(w) for w in weights),size,
                   max(r.frame.day for r in train),max(r.end_day for r in train),point.day,point.index)

    def predict(self, frame):
        if frame.day<self.fit_day: raise DataError('logit_model_from_future')
        row=design_row(frame,self.means,self.scales,self.boundaries)
        probs=dict(zip(LABELS,softmax(tuple(math.sumprod(row,w) for w in self.weights))))
        return prediction('ind_logit',probs)


class WalkForwardDaily:
    def __init__(self, H, threshold, records, origin):
        horizon(H)
        self.H,self.threshold,self.records,self.origin=H,threshold,tuple(records),origin
        self.model=None;self.last_index=None;self.fits=[]

    def predict(self, point):
        if not point.predictable or not DEV_START<=point.day<=DEV_END:
            raise DataError('daily_prediction_dev_only')
        if self.last_index is not None and point.index<=self.last_index:
            raise DataError('daily_walk_forward_order')
        self.last_index=point.index
        train=matured(self.records,point,self.H)
        majority=probabilities(r.truth for r in train)
        result={};states={}
        for method in ('always_flat','momentum_H','reversal_H'):
            answer='flat' if method=='always_flat' else label(point.past_returns[self.H],self.threshold)
            if method=='reversal_H': answer={'up':'down','down':'up','flat':'flat'}[answer]
            result[method]=prediction(method,{name:float(name==answer) for name in LABELS})
        result['majority']=prediction('majority',majority)
        for method in ('vol_prior_d',*('ind_'+name for name in INDICATORS)):
            result[method],states[method]=conditional(method,point,train,majority)
        if (point.index-self.origin)%20==0 and len(train)>=250:
            self.model=Logistic.fit(train,point)
            self.fits.append(self.model)
        result['ind_logit']=self.model.predict(point) if self.model else prediction('ind_logit',majority)
        return result,states
