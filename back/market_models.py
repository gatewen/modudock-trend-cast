"""New 16-indicator logit, preserving all original learning hyperparameters."""
from dataclasses import dataclass,field
import math
from statistics import fmean,pstdev

from .daily_config import LABELS
from .daily_models import matured,probabilities,prediction,cuts,bucket,softmax
from .daily_replay import DEV_START,DEV_END,horizon
from .data import DataError
from .market_config import NUMERIC,INDICATORS,STATES,TERCILES,MARKET_INDICATORS


def boundaries_for(train):
    return {name:cuts(r.frame.values.get(name) for r in train) for name in TERCILES}


def state(frame,name,boundaries):
    return bucket(frame.values.get(name),boundaries.get(name)) if name in TERCILES else frame.states.get(name,'missing')


def design_row(frame,means,scales,boundaries):
    numeric=tuple((frame.values.get(name)-mean)/scale if frame.values.get(name) is not None else 0.
                  for name,mean,scale in zip(NUMERIC,means,scales))
    onehot=[]
    for name in INDICATORS:
        level=state(frame,name,boundaries)
        onehot.extend(float(level==candidate) for candidate in (*STATES[name],'missing'))
    return (1.,*numeric,*onehot)


@dataclass
class MarketLogistic:
    means: tuple
    scales: tuple
    boundaries: dict
    weights: tuple=field(repr=False)
    train_n: int
    train_last_day: str
    train_last_end: str
    fit_day: str
    fit_index: int

    @classmethod
    def fit(cls,train,point):
        if len(train)<250:raise DataError('insufficient_market_training')
        columns=[[r.frame.values.get(name) for r in train if r.frame.values.get(name) is not None] for name in NUMERIC]
        means=tuple(fmean(values) if values else 0. for values in columns)
        scales=tuple(pstdev(values) or 1. if values else 1. for values in columns)
        boundaries=boundaries_for(train)
        rows=tuple(design_row(r.frame,means,scales,boundaries) for r in train)
        columns=tuple(zip(*rows));size=len(rows);width=len(rows[0])
        weights=[[0.]*width for _ in LABELS]
        targets=tuple(tuple(float(r.truth==name) for r in train) for name in LABELS)
        dot=math.sumprod
        for _ in range(300):
            fitted=tuple(softmax(tuple(dot(row,w) for w in weights)) for row in rows)
            residuals=tuple(tuple(p[c]-y for p,y in zip(fitted,targets[c])) for c in range(3))
            weights=[[w[j]-.1*(dot(column,residuals[c])/size+(w[j] if j else 0.))
                      for j,column in enumerate(columns)] for c,w in enumerate(weights)]
        return cls(means,scales,boundaries,tuple(tuple(w) for w in weights),size,
                   max(r.frame.day for r in train),max(r.end_day for r in train),point.day,point.index)

    def predict(self,frame):
        if frame.day<self.fit_day:raise DataError('market_model_from_future')
        row=design_row(frame,self.means,self.scales,self.boundaries)
        return prediction('mkt_logit',dict(zip(LABELS,softmax(tuple(math.sumprod(row,w) for w in self.weights)))))


class WalkForwardMarket:
    def __init__(self,H,records,origin):
        horizon(H)
        self.H,self.records,self.origin=H,tuple(records),origin
        self.model=None;self.last_index=None;self.fits=[]

    def predict(self,point):
        if not point.predictable or not DEV_START<=point.day<=DEV_END:raise DataError('market_prediction_dev_only')
        if self.last_index is not None and point.index<=self.last_index:raise DataError('market_walk_forward_order')
        self.last_index=point.index
        train=matured(self.records,point,self.H)
        majority=probabilities(r.truth for r in train)
        boundaries=boundaries_for(train)
        result={};states={}
        for name in MARKET_INDICATORS:
            method='ind_'+name;level=state(point,name,boundaries)
            matches=[r.truth for r in train if state(r.frame,name,boundaries)==level]
            probs=probabilities(matches) if level!='missing' and len(matches)>=30 else majority
            result[method]=prediction(method,probs);states[method]=level
        if (point.index-self.origin)%20==0 and len(train)>=250:
            self.model=MarketLogistic.fit(train,point);self.fits.append(self.model)
        result['mkt_logit']=self.model.predict(point) if self.model else prediction('mkt_logit',majority)
        return result,states,prediction('majority',majority)
