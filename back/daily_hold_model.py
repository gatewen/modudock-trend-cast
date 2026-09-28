"""SPEC 16.5: all fitting stops at development; held-out labels are never inputs."""
from fractions import Fraction
import json

from .daily_config import METHODS as ORIGINAL_METHODS,INDICATORS as ORIGINAL_INDICATORS,LABELS
from .daily_ensemble import average_current,COMPONENTS
from .daily_models import Logistic,probabilities,prediction,cuts,bucket
from .daily_replay import DEV_END,HOLD_START,HOLD_END,HORIZONS,horizon,label
from .daily_run import prepare_development,development_records
from .data import DataError
from .experiment import canonical
from .market_config import METHODS as MARKET_METHODS,INDICATORS,STATES,PROTOCOL as MARKET_PROTOCOL
from .market_features import augment
from .market_models import MarketLogistic,boundaries_for,state
from .market_store import load_snapshot

METHODS=ORIGINAL_METHODS+MARKET_METHODS+('ens_avg',)
PRIMARY=((3,'ens_avg'),(7,'ens_avg'),(3,'mkt_logit'))
POLICY=dict(version='daily-holdout-v1',spec_commit='37755a0',methods=METHODS,primary=PRIMARY,
    first=HOLD_START,last=HOLD_END,horizons=HORIZONS,market=MARKET_PROTOCOL,
    frequency='all_scorable_development_endpoints',logit='last_persisted_development_fit_no_refit',
    update_on_holdout=False,bootstrap=dict(block_sessions=20,iterations=2000,seed=20260927),
    comparisons=3,expected_lucky=.075)


def last_fit(store,table,H):
    if table not in ('d_fits','market_fits'):raise DataError('holdout_bad_fit_table')
    r=store.db.execute(f'''SELECT day,model_json FROM {table} WHERE experiment_id=4 AND H=?
        AND day<=? ORDER BY day DESC LIMIT 1''',(H,DEV_END)).fetchone()
    if r is None:raise DataError('holdout_missing_development_fit')
    model=json.loads(r['model_json'])
    if model['fit_day']!=r['day'] or not model['train_last_day']<=model['train_last_end']<=model['fit_day']<=DEV_END:
        raise DataError('holdout_invalid_development_fit')
    return model


def build(store):
    snapshot=load_snapshot(store,MARKET_PROTOCOL);row=snapshot['original']
    old=prepare_development(store,row);points,_=augment(store,old)
    result={}
    for H in HORIZONS:
        _,records,_=development_records(store,row,points,H)
        if not records:raise DataError('holdout_missing_training')
        majority=probabilities(r.truth for r in records)
        bounds=boundaries_for(records);vol=cuts(r.frame.volatility for r in records)
        groups={}
        for name in ('vol_prior_d',*INDICATORS):
            levels=('low','middle','high') if name=='vol_prior_d' else STATES[name]
            groups[name]={}
            for level in levels:
                truths=[r.truth for r in records if (bucket(r.frame.volatility,vol) if name=='vol_prior_d' else state(r.frame,name,bounds))==level]
                groups[name][level]=probabilities(truths) if len(truths)>=30 else majority
        result[str(H)]=dict(majority=majority,boundaries=bounds,vol_boundaries=vol,groups=groups,
            threshold=row['config']['thresholds'][str(H)],train_n=len(records),
            ind_logit=last_fit(store,'d_fits',H),mkt_logit=last_fit(store,'market_fits',H))
    return dict(horizons=result,data_digest=row['data_digest'],dev_digest=row['dev_digest'],market_digest=snapshot['market_digest'])


class FrozenDaily:
    def __init__(self,bundle):
        # Copy by value; no cursor, outcome callback or train/update API exists.
        self.bundle=json.loads(canonical(bundle))

    def predict(self,point,H):
        horizon(H)
        if not point.predictable or not HOLD_START<=point.day<=HOLD_END:
            raise DataError('holdout_prediction_boundary')
        m=self.bundle['horizons'][str(H)];majority=m['majority'];out={};states={}
        for method in ('always_flat','momentum_H','reversal_H'):
            answer='flat' if method=='always_flat' else label(point.past_returns[H],Fraction(**m['threshold']))
            if method=='reversal_H':answer={'up':'down','down':'up','flat':'flat'}[answer]
            out[method]=prediction(method,{name:float(name==answer) for name in LABELS})
        out['majority']=prediction('majority',majority)
        for name in ('vol_prior_d',*INDICATORS):
            method=name if name=='vol_prior_d' else 'ind_'+name
            level=bucket(point.volatility,m['vol_boundaries']) if name=='vol_prior_d' else state(point,name,m['boundaries'])
            out[method]=prediction(method,m['groups'][name].get(level,majority));states[method]=level
        out['ind_logit']=Logistic(**m['ind_logit']).predict(point)
        out['mkt_logit']=MarketLogistic(**m['mkt_logit']).predict(point)
        out['ens_avg']=average_current({name:out[name] for name in COMPONENTS})
        if set(out)!=set(METHODS):raise DataError('holdout_method_set_changed')
        return out,states
