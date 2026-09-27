"""Offline development-only execution; model/feature/result namespaces are daily."""
from dataclasses import asdict
from fractions import Fraction
import json

from .daily_config import METHODS
from .daily_experiment import load_experiment
from .daily_indicators import frames
from .daily_models import Record, WalkForwardDaily
from .daily_models import prediction
from .daily_replay import DailyReplay, DEV_START, DEV_END, label, horizon
from .data import DataError
from .experiment import canonical


def prepare_development(store, row):
    config=row['config']
    # This cap is at the query boundary, not a filter after outcomes are built.
    if config['dev_end']>DEV_END: raise DataError('daily_dev_only')
    result=tuple(f for f in frames(store,start=config['start'],end=config['dev_end'])
                 if config['dev_start']<=f.day<=config['dev_end'] and f.predictable)
    return result


def development_records(store, row, points, H):
    horizon(H)
    engine=DailyReplay(store,start=row['config']['start'])
    threshold=Fraction(**row['config']['thresholds'][str(H)])
    records=[];outcomes={}
    for point in points:
        if not DEV_START<=point.day<=row['config']['dev_end']<=DEV_END:
            raise DataError('daily_dev_only')
        outcome=engine.outcome(point.day,H,end_limit=row['config']['dev_end'])
        if outcome.scorable:
            truth=label(outcome.adjusted_return,threshold)
            records.append(Record(point,H,outcome.end_day,truth))
            outcomes[point.day]=outcome
    return threshold,tuple(records),outcomes


def exact_insert(store, table, values, keys):
    names=tuple(values)
    existing=store.db.execute(f'SELECT * FROM {table} WHERE '+ ' AND '.join(k+'=?' for k in keys),tuple(values[k] for k in keys)).fetchone()
    if existing is not None:
        if dict(existing)!=values: raise DataError('daily_existing_result_changed')
        return 0
    store.db.execute(f'INSERT INTO {table} ({",".join(names)}) VALUES ({",".join("?" for _ in names)})',tuple(values.values()))
    return 1


def run_horizon(store, experiment_id=4, H=3, *, split='dev', progress=None):
    if split!='dev': raise DataError('daily_dev_only')
    horizon(H)
    row=load_experiment(store,experiment_id)
    points=prepare_development(store,row)
    threshold,records,outcomes=development_records(store,row,points,H)
    record_by_day={r.frame.day:r for r in records}
    expected={(f.day,m) for f in points for m in METHODS}
    existing={(r['day'],r['method']) for r in store.db.execute(
        "SELECT day,method FROM d_predictions WHERE experiment_id=? AND H=? AND method!='jev_ind'",(experiment_id,H))}
    # A complete rerun validates stored outputs via report; no refit or network.
    if existing==expected:
        for f in points:
            saved=store.db.execute('SELECT * FROM d_features WHERE experiment_id=? AND day=?',(experiment_id,f.day)).fetchone()
            if saved is None or saved['input_hash']!=f.digest or saved['input_json']!=f.serialized():
                raise DataError('daily_existing_result_changed')
        by_day={p.day:p for p in points}
        for saved in store.db.execute("SELECT * FROM d_predictions WHERE experiment_id=? AND H=? AND method!='jev_ind'",(experiment_id,H)):
            valid=prediction(saved['method'],json.loads(saved['probabilities_json']))
            if saved['choice']!=valid.answer or saved['input_hash']!=by_day[saved['day']].digest:
                raise DataError('daily_existing_result_changed')
        saved={r['day']:r for r in store.db.execute('SELECT * FROM d_outcomes WHERE experiment_id=? AND H=?',(experiment_id,H))}
        if set(saved)!=set(outcomes): raise DataError('daily_missing_outcomes')
        for day,outcome in outcomes.items():
            r=saved[day]
            if (r['end_day']!=outcome.end_day or r['label']!=record_by_day[day].truth or
                    Fraction(int(r['return_numerator']),int(r['return_denominator']))!=outcome.adjusted_return):
                raise DataError('daily_existing_result_changed')
        return dict(H=H,points=len(points),scorable=len(records),new_predictions=0,refits=0,http_calls=0)
    model=WalkForwardDaily(H,threshold,records,points[0].index)
    written=0
    for n,point in enumerate(points):
        predictions,states=model.predict(point)
        with store.transaction():
            exact_insert(store,'d_features',dict(experiment_id=experiment_id,day=point.day,input_hash=point.digest,input_json=point.serialized()),('experiment_id','day'))
            for method,pred in predictions.items():
                written+=exact_insert(store,'d_predictions',dict(experiment_id=experiment_id,H=H,method=method,day=point.day,
                    choice=pred.answer,probabilities_json=canonical(pred.probabilities),state=states.get(method),input_hash=point.digest),('experiment_id','H','method','day'))
            if point.day in outcomes:
                outcome=outcomes[point.day];record=record_by_day[point.day]
                exact_insert(store,'d_outcomes',dict(experiment_id=experiment_id,H=H,day=point.day,end_day=outcome.end_day,label=record.truth,
                    return_numerator=str(outcome.adjusted_return.numerator),return_denominator=str(outcome.adjusted_return.denominator)),('experiment_id','H','day'))
            if model.model and model.model.fit_day==point.day:
                exact_insert(store,'d_fits',dict(experiment_id=experiment_id,H=H,day=point.day,model_json=canonical(asdict(model.model))),('experiment_id','H','day'))
        if progress and (n%100==0 or n==len(points)-1): progress(dict(H=H,processed=n+1,total=len(points),refits=len(model.fits)))
    return dict(H=H,points=len(points),scorable=len(records),new_predictions=written,refits=len(model.fits),http_calls=0)
