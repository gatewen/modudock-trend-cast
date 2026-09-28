"""Offline market extension of experiment 4; all reads capped at development end."""
from collections import Counter
from dataclasses import asdict
import hashlib
import json

from .daily_models import prediction
from .daily_replay import DEV_START,DEV_END,HORIZONS,horizon
from .daily_run import prepare_development,development_records,exact_insert
from .daily_score import paired_blocks
from .data import DataError
from .experiment import canonical
from .market_config import METHODS,MARKET_INDICATORS,PROTOCOL
from .market_features import augment
from .market_models import WalkForwardMarket
from .market_store import load_snapshot,source_summary
from .score import ScoredPoint,metrics


def inputs(store):
    snapshot=load_snapshot(store,PROTOCOL)
    original=prepare_development(store,snapshot['original'])
    points,alignment=augment(store,original)
    if not points:raise DataError('market_no_predictable_days')
    return snapshot,original,points,alignment


def saved_reference(store,H,original):
    by_day={p.day:p for p in original};result={}
    for r in store.db.execute('''SELECT * FROM d_predictions WHERE experiment_id=4
            AND H=? AND method='majority' AND day BETWEEN ? AND ?''',(H,DEV_START,DEV_END)):
        if r['day'] not in by_day:raise DataError('market_unexpected_reference')
        p=prediction('majority',json.loads(r['probabilities_json']))
        if p.answer!=r['choice'] or r['input_hash']!=by_day[r['day']].digest:
            raise DataError('market_reference_mismatch')
        result[r['day']]=p
    return result


def saved_candidates(store,H,points,original,alignment):
    by_day={p.day:p for p in points};old={p.day:p for p in original}
    result={m:{} for m in METHODS};states={m:Counter() for m in METHODS}
    features={r['day']:r for r in store.db.execute('''SELECT * FROM market_features
        WHERE experiment_id=4 AND day BETWEEN ? AND ?''',(DEV_START,DEV_END))}
    for day,r in features.items():
        if day not in by_day or (r['original_hash'],r['input_hash'],r['input_json'],r['alignment_json'])!=(
                old[day].digest,by_day[day].digest,by_day[day].serialized(),canonical(alignment[day])):
            raise DataError('market_feature_mismatch')
    for r in store.db.execute('''SELECT * FROM market_predictions WHERE experiment_id=4
            AND H=? AND day BETWEEN ? AND ?''',(H,DEV_START,DEV_END)):
        day,method=r['day'],r['method']
        if method not in result or day not in by_day or day not in features:
            raise DataError('market_unexpected_prediction')
        p=prediction(method,json.loads(r['probabilities_json']))
        if p.answer!=r['choice'] or r['input_hash']!=by_day[day].digest:
            raise DataError('market_prediction_mismatch')
        result[method][day]=p
        if r['state'] is not None:states[method][r['state']]+=1
    return result,states


def verified_outcomes(store,H,records):
    expected={r.frame.day:r for r in records}
    actual={r['day']:r for r in store.db.execute('''SELECT day,end_day,label FROM d_outcomes
        WHERE experiment_id=4 AND H=? AND day BETWEEN ? AND ? AND end_day<=?''',
        (H,DEV_START,DEV_END,DEV_END))}
    if set(actual)-set(expected) or any(r['label']!=expected[d].truth or
            r['end_day']!=expected[d].end_day for d,r in actual.items()):
        raise DataError('market_outcome_mismatch')
    return actual


def run_horizon(store,H,*,split='dev',progress=None):
    if split!='dev':raise DataError('market_dev_only')
    horizon(H)
    snapshot,original,points,alignment=inputs(store)
    _,records,_=development_records(store,snapshot['original'],points,H)
    outcomes=verified_outcomes(store,H,records)
    references=saved_reference(store,H,original)
    if len(outcomes)!=len(records) or len(references)!=len(points):
        raise DataError('market_original_results_incomplete')
    saved,_=saved_candidates(store,H,points,original,alignment)
    if all(len(v)==len(points) for v in saved.values()):
        return dict(H=H,points=len(points),scorable=len(records),new_predictions=0,refits=0,http_calls=0)
    model=WalkForwardMarket(H,records,points[0].index);written=0
    for n,(point,old) in enumerate(zip(points,original)):
        predictions,states,reference=model.predict(point)
        if reference!=references[point.day]:raise DataError('market_recomputed_majority_changed')
        with store.transaction():
            exact_insert(store,'market_features',dict(experiment_id=4,day=point.day,
                original_hash=old.digest,input_hash=point.digest,input_json=point.serialized(),
                alignment_json=canonical(alignment[point.day])),('experiment_id','day'))
            for method,p in predictions.items():
                written+=exact_insert(store,'market_predictions',dict(experiment_id=4,H=H,method=method,
                    day=point.day,choice=p.answer,probabilities_json=canonical(p.probabilities),
                    state=states.get(method),input_hash=point.digest),('experiment_id','H','method','day'))
            if model.model and model.model.fit_day==point.day:
                exact_insert(store,'market_fits',dict(experiment_id=4,H=H,day=point.day,
                    model_json=canonical(asdict(model.model))),('experiment_id','H','day'))
        if progress and (n%100==0 or n==len(points)-1):progress(dict(H=H,processed=n+1,total=len(points),refits=len(model.fits)))
    return dict(H=H,points=len(points),scorable=len(records),new_predictions=written,refits=len(model.fits),http_calls=0)


def development_report(store,*,split='dev'):
    if split!='dev':raise DataError('market_dev_only')
    snapshot,original,points,alignment=inputs(store)
    calendar=tuple(r[0] for r in store.db.execute(
        'SELECT day FROM d_calendar WHERE day BETWEEN ? AND ? ORDER BY day',(DEV_START,DEV_END)))
    quality={n:dict(missing=sum(p.values.get(n) is None for p in points),total=len(points)) for n in MARKET_INDICATORS}
    report=dict(experiment=4,split='dev',dev_start=DEV_START,dev_end=DEV_END,
        data_digest=snapshot['original_data_digest'],dev_digest=snapshot['original_dev_digest'],
        market_digest=snapshot['market_digest'],sources=source_summary(store),feature_quality=quality,
        comparisons=18,expected_lucky=.45,http_calls=0,horizons={})
    for H in HORIZONS:
        _,records,_=development_records(store,snapshot['original'],points,H)
        outcomes=verified_outcomes(store,H,records)
        references=saved_reference(store,H,original)
        candidates,states=saved_candidates(store,H,points,original,alignment)
        results={}
        for method,forecasts in candidates.items():
            common=sorted(set(forecasts)&set(references)&set(outcomes))
            def scored(preds):return {d:ScoredPoint(d,outcomes[d]['label'],preds[d].answer,preds[d].probabilities) for d in common}
            candidate,reference=scored(forecasts),scored(references)
            comparison=paired_blocks(candidate,reference,calendar,repetitions=2000,seed=20260927)
            complete=bool(records) and len(common)==len(records) and len(forecasts)==len(references)==len(points)
            shortlisted=bool(complete and comparison['ci95'] and comparison['ci95'][1]<0)
            audit=[dict(day=d,choice=p.answer,probabilities=p.probabilities) for d,p in sorted(forecasts.items())]
            results[method]=dict(predictable=len(points),expected_scorable=len(records),n=len(common),
                complete=complete,coverage=len(common)/len(records) if records else None,
                missing_predictions=len(points)-len(forecasts),missing_reference=len(points)-len(references),
                missing_outcomes=len(records)-len(outcomes),states=dict(states[method]),
                forecast_digest=hashlib.sha256(canonical(audit).encode()).hexdigest(),
                candidate=metrics(candidate.values()),majority=metrics(reference.values()),
                brier_vs_majority=comparison,shortlisted=shortlisted,
                verdict='入圍：值得再驗證，尚非證明有效' if shortlisted else '未入圍' if complete else '結果不完整，不下結論')
        report['horizons'][str(H)]=results
    return report


def markdown(report):
    lines=['# 市場環境指標：實驗 4 開發段','',
        'SPEC §16.3；差值為考生 − majority。20 交易日不重疊區塊、保留尾塊，2000 次，seed=20260927。',
        '本輪 18 個比較，預期約 0.45 個因運氣入圍。入圍只代表值得再驗證；保留段未使用。','',
        '| 天期 | 考生 | n | Brier 差 | 95% 區間 | 判讀 |','|---|---|---:|---:|---|---|']
    def number(v):return f'{v:+.9f}' if v is not None else '無樣本'
    for H,results in report['horizons'].items():
        for method,p in results.items():
            c=p['brier_vs_majority'];ci='['+', '.join(number(v) for v in c['ci95'])+']' if c['ci95'] else '無樣本'
            lines.append(f'| {H} | {method} | {p["n"]} | {number(c["difference"])} | {ci} | {p["verdict"]} |')
    return '\n'.join(lines)+'\n'
