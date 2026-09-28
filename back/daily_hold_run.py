"""Predict every method/horizon first; only then commit outcomes and reveal."""
from fractions import Fraction
import json

from .daily_hold_model import FrozenDaily,METHODS,PRIMARY,POLICY
from .daily_hold_store import load_models,verify_original,verify_market,sha
from .daily_indicators import frames
from .daily_replay import DEV_END,HOLD_START,HOLD_END,HORIZONS,DailyReplay,label
from .daily_reveal import revealed,require_revealed
from .daily_run import exact_insert
from .daily_score import paired_blocks
from .data import DataError
from .experiment import canonical
from .market_config import PROTOCOL as MARKET_PROTOCOL
from .market_features import MarketHistory,asof_features
from .market_store import load_snapshot
from .score import ScoredPoint,metrics,conclusion
from .store import now_string


def prepare_inputs(store):
    frozen=load_models(store);row=verify_original(store);verify_market(store)
    market=load_snapshot(store,MARKET_PROTOCOL)
    if any(frozen['model'][k]!=v for k,v in (('data_digest',row['data_digest']),('dev_digest',row['dev_digest']),('market_digest',market['market_digest']))):
        raise DataError('holdout_source_binding_changed')
    history=frames(store,start=row['config']['start'],end=HOLD_END)
    points=tuple(p for p in history if HOLD_START<=p.day<=HOLD_END)
    if not points or any(not p.predictable for p in points):raise DataError('holdout_inputs_incomplete')
    sources=[dict(r) for r in store.db.execute('SELECT * FROM market_rows WHERE day<=?',(DEV_END,))]
    sources.extend(dict(r) for r in store.db.execute('SELECT * FROM d_hold_market_rows WHERE day BETWEEN ? AND ?',(HOLD_START,HOLD_END)))
    markets=MarketHistory(sources)
    raw={r['day']:r['close'] for r in store.db.execute("SELECT day,close FROM d_bars WHERE symbol='2330' AND day BETWEEN ? AND ?",(HOLD_START,HOLD_END))}
    values=[];alignment={}
    for point in points:
        enriched,info=asof_features(point,markets,raw.get(point.day))
        values.append(enriched);alignment[point.day]=info
    return frozen,tuple(values),alignment


def table_rows(store,table):
    if table not in ('d_hold_features','d_hold_predictions','d_hold_outcomes'):raise DataError('holdout_bad_table')
    return [dict(r) for r in store.db.execute(f'SELECT * FROM {table} WHERE experiment_id=4 ORDER BY day'+(',H,method' if table=='d_hold_predictions' else ',H' if table=='d_hold_outcomes' else ''))]


def prediction_rows(model,point,H,model_hash):
    answers,states=model.predict(point,H)
    return [dict(experiment_id=4,H=H,method=method,day=point.day,choice=p.answer,
        probabilities_json=canonical(p.probabilities),state=states.get(method),input_hash=point.digest,model_hash=model_hash)
        for method,p in sorted(answers.items())]


def validate_predictions(store,frozen,points,alignment):
    expected_features=[dict(experiment_id=4,day=p.day,input_json=p.serialized(),input_hash=p.digest,
                            alignment_json=canonical(alignment[p.day])) for p in points]
    model=FrozenDaily(frozen['model'])
    expected=[r for p in points for H in HORIZONS for r in prediction_rows(model,p,H,frozen['model_hash'])]
    actual=table_rows(store,'d_hold_predictions');features=table_rows(store,'d_hold_features')
    if actual!=expected or features!=expected_features:raise DataError('holdout_predictions_incomplete_or_changed')
    return dict(n_days=len(points),n_predictions=len(expected),prediction_digest=sha(actual),feature_digest=sha(features))


def predict_all(store,progress=None):
    frozen,points,alignment=prepare_inputs(store);model=FrozenDaily(frozen['model'])
    written=0
    if not store.db.execute('SELECT 1 FROM d_hold_completion').fetchone():
        for i,p in enumerate(points):
            with store.transaction():
                exact_insert(store,'d_hold_features',dict(experiment_id=4,day=p.day,input_json=p.serialized(),
                    input_hash=p.digest,alignment_json=canonical(alignment[p.day])),('experiment_id','day'))
                for H in HORIZONS:
                    for row in prediction_rows(model,p,H,frozen['model_hash']):
                        written+=exact_insert(store,'d_hold_predictions',row,('experiment_id','H','method','day'))
            # Before reveal even progress exports contain no holdout numbers.
            if progress and i==0:progress(dict(stage='predicting'))
        with store.transaction():
            complete=validate_predictions(store,frozen,points,alignment)
            store.db.execute('INSERT INTO d_hold_completion VALUES (4,?,?,?,?,?)',
                (now_string(),complete['n_days'],complete['n_predictions'],complete['prediction_digest'],complete['feature_digest']))
    complete=validate_predictions(store,frozen,points,alignment)
    saved=store.db.execute('SELECT * FROM d_hold_completion WHERE experiment_id=4').fetchone()
    if any(saved[k]!=v for k,v in complete.items()):raise DataError('holdout_completion_changed')
    if progress:progress(dict(stage='predictions_sealed'))
    return dict(new_predictions=written,**complete)


def settle_and_reveal(store):
    if revealed(store):return False
    frozen,points,alignment=prepare_inputs(store)
    complete=validate_predictions(store,frozen,points,alignment)
    saved=store.db.execute('SELECT * FROM d_hold_completion WHERE experiment_id=4').fetchone()
    if saved is None or any(saved[k]!=v for k,v in complete.items()):raise DataError('holdout_predictions_incomplete')
    # First holdout outcome call is strictly after the all-method/all-H seal.
    engine=DailyReplay(store);outcomes=[]
    for H in HORIZONS:
        threshold=Fraction(**frozen['model']['horizons'][str(H)]['threshold'])
        for i,p in enumerate(points):
            o=engine.outcome(p.day,H,end_limit=HOLD_END)
            if i+H>=len(points):
                if o.scorable:raise DataError('holdout_endpoint_past_boundary')
                continue
            if not o.scorable or o.end_day!=points[i+H].day:raise DataError('holdout_outcomes_incomplete')
            outcomes.append(dict(experiment_id=4,H=H,day=p.day,end_day=o.end_day,label=label(o.adjusted_return,threshold),
                return_numerator=str(o.adjusted_return.numerator),return_denominator=str(o.adjusted_return.denominator)))
    with store.transaction():
        # A failed transaction leaves no outcomes, seal or reveal visible.
        validate_predictions(store,frozen,points,alignment)
        for r in outcomes:exact_insert(store,'d_hold_outcomes',r,('experiment_id','H','day'))
        actual=table_rows(store,'d_hold_outcomes')
        if len(actual)!=len(outcomes):raise DataError('holdout_unexpected_outcomes')
        stamp=now_string()
        store.db.execute('INSERT INTO d_hold_seal VALUES (4,?,?,?)',(stamp,len(actual),sha(actual)))
        store.db.execute('''INSERT INTO reveals(experiment_id,revealed_at,first_day,last_day,what,segment,namespace)
            VALUES (4,?,?,?,'all','holdout','daily')''',(stamp,HOLD_START,HOLD_END))
    return True


def verdict(method,comparison,expected,complete=True):
    return conclusion('majority',comparison['difference'],comparison['ci95'],
        common_n=comparison['n'],eligible_n=expected,complete=complete).replace('jev 比',method+' 比').replace('顯示 jev 比','顯示 '+method+' 比')


def report(store):
    require_revealed(store)  # Before every feature, outcome or score read.
    frozen=load_models(store)
    complete=store.db.execute('SELECT * FROM d_hold_completion WHERE experiment_id=4').fetchone()
    seal=store.db.execute('SELECT * FROM d_hold_seal WHERE experiment_id=4').fetchone()
    saved=table_rows(store,'d_hold_predictions');outcomes=table_rows(store,'d_hold_outcomes')
    if sha(saved)!=complete['prediction_digest'] or sha(outcomes)!=seal['outcome_digest']:
        raise DataError('holdout_sealed_results_changed')
    calendar=tuple(r[0] for r in store.db.execute('SELECT day FROM d_calendar WHERE day BETWEEN ? AND ? ORDER BY day',(HOLD_START,HOLD_END)))
    result=dict(experiment=4,split='holdout',revealed_at=seal['revealed_at'],first_day=HOLD_START,last_day=HOLD_END,
        model_hash=frozen['model_hash'],policy=POLICY,n_days=complete['n_days'],missing_predictions=0,
        prediction_digest=complete['prediction_digest'],outcome_digest=seal['outcome_digest'],
        primary_comparisons=[],descriptive={},promoted=[],http_calls=0)
    for H in HORIZONS:
        truth={r['day']:r for r in outcomes if r['H']==H}
        methods={m:{} for m in METHODS}
        for r in saved:
            if r['H']!=H or r['day'] not in truth:continue
            methods[r['method']][r['day']]=ScoredPoint(r['day'],truth[r['day']]['label'],r['choice'],json.loads(r['probabilities_json']))
        expected=len(calendar)-H
        result['descriptive'][str(H)]={m:dict(**metrics(ps.values()),coverage=len(ps)/expected if expected else None,
            missing=expected-len(ps),note='描述性成績，不作優劣結論') for m,ps in methods.items()}
        for h,method in PRIMARY:
            if h!=H:continue
            comparison=paired_blocks(methods[method],methods['majority'],calendar,repetitions=2000,seed=20260927)
            full=len(methods[method])==len(methods['majority'])==expected
            comparison.update(H=H,method=method,baseline='majority',expected_scorable=expected,complete=full,
                verdict=verdict(method,comparison,expected,full))
            result['primary_comparisons'].append(comparison)
            if full and comparison['ci95'] and comparison['ci95'][1]<0:result['promoted'].append(dict(H=H,method=method))
    result['multiplicity']=dict(comparisons=3,expected_lucky=.075,
        note='3 個事先指定比較，預期約 0.075 個因運氣顯著較好；名目估算，未作多重比較校正。其餘成績只描述，不增列比較。')
    return result


def markdown(result):
    lines=['# 實驗 4：多日保留段一次性考試','',
        '2022-01-03～2024-07-25；已揭露、永久已使用。所有模型凍結於開發段，保留段不更新統計或權重。',
        result['multiplicity']['note'],'',
        '| 天期 | 考生 | n | Brier 差（−majority） | 95% 區間 | 結論 |',
        '|---|---|---:|---:|---|---|']
    for c in result['primary_comparisons']:
        ci='['+', '.join(f'{v:+.9f}' for v in c['ci95'])+']' if c['ci95'] else '無樣本'
        delta=f'{c["difference"]:+.9f}' if c['difference'] is not None else '無樣本'
        lines.append(f'| {c["H"]} | {c["method"]} | {c["n"]} | {delta} | {ci} | {c["verdict"]} |')
    lines+=['','20 交易日不重疊區塊、保留尾塊，2000 次，seed=20260927。','',
        '## 全部方法描述性成績','',
        '下表不作優劣結論；完整 JSON 另含 Wilson 區間、各類 precision／recall、混淆矩陣與覆蓋率。','',
        '| 天期 | 方法 | n | 準確率 | Brier | 缺答 |','|---|---|---:|---:|---:|---:|']
    for H,methods in result['descriptive'].items():
        for method,r in methods.items():lines.append(f'| {H} | {method} | {r["n"]} | {r["accuracy"]:.4%} | {r["brier"]:.9f} | {r["missing"]} |')
    return '\n'.join(lines)+'\n'
