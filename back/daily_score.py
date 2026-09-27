"""Development intersections and paired nonoverlapping 20-session bootstrap."""
from collections import Counter
import json
import math
import random

from .daily_config import METHODS, INDICATORS, LABELS, CLAIMS, PROTOCOL
from .daily_experiment import load_experiment
from .daily_models import prediction
from .daily_replay import DEV_END, HORIZONS
from .daily_run import prepare_development, development_records
from .data import DataError
from .score import ScoredPoint, metrics, percentile


def paired_blocks(candidate, reference, calendar, *, repetitions=2000, seed=20260927):
    keys=set(candidate)&set(reference)
    if any(day not in calendar for day in keys): raise DataError('daily_bootstrap_calendar')
    blocks=[]
    for offset in range(0,len(calendar),20):
        days=[d for d in calendar[offset:offset+20] if d in keys]
        if not days: continue
        if any(candidate[d].truth!=reference[d].truth for d in days): raise DataError('daily_pair_truth_mismatch')
        blocks.append((len(days),math.fsum(candidate[d].brier-reference[d].brier for d in days)))
    if not blocks: return dict(n=0,difference=None,ci95=None,blocks=0)
    rng=random.Random(seed);distribution=[]
    for _ in range(repetitions):
        sample=[blocks[rng.randrange(len(blocks))] for _ in blocks]
        distribution.append(math.fsum(b[1] for b in sample)/sum(b[0] for b in sample))
    return dict(n=len(keys),difference=math.fsum(b[1] for b in blocks)/sum(b[0] for b in blocks),
        ci95=[percentile(distribution,.025),percentile(distribution,.975)],blocks=len(blocks),
        block_sessions=20,repetitions=repetitions,seed=seed,layout='nonoverlapping_keep_tail')


def development_report(store, experiment_id=4, *, split='dev'):
    if split!='dev': raise DataError('daily_dev_only')
    row=load_experiment(store,experiment_id);config=row['config']
    first,last=config['dev_start'],config['dev_end']
    if last>DEV_END: raise DataError('daily_dev_only')
    calendar=tuple(r[0] for r in store.db.execute('SELECT day FROM d_calendar WHERE day BETWEEN ? AND ? ORDER BY day',(first,last)))
    prepared=prepare_development(store,row)
    result=dict(experiment=experiment_id,split='dev',data_digest=row['data_digest'],protocol=config['protocol'],horizons={})
    for H in HORIZONS:
        _,expected_records,_=development_records(store,row,prepared,H)
        expected={r.frame.day:r for r in expected_records}
        outcomes={r['day']:r for r in store.db.execute('''SELECT * FROM d_outcomes WHERE experiment_id=? AND H=?
          AND day BETWEEN ? AND ? AND end_day<=?''',(experiment_id,H,first,last,last))}
        if set(outcomes)-set(expected): raise DataError('daily_unexpected_outcome')
        if any(r['label']!=expected[d].truth or r['end_day']!=expected[d].end_day for d,r in outcomes.items()):
            raise DataError('daily_outcome_mismatch')
        by_method={m:{} for m in METHODS};states={}
        for r in store.db.execute('''SELECT * FROM d_predictions WHERE experiment_id=? AND H=? AND day BETWEEN ? AND ?''',(experiment_id,H,first,last)):
            if r['method'] not in by_method: raise DataError('daily_unknown_method')
            probs=json.loads(r['probabilities_json']);valid=prediction(r['method'],probs)
            if valid.answer!=r['choice']: raise DataError('daily_invalid_choice')
            if r['day'] not in outcomes: continue
            by_method[r['method']][r['day']]=ScoredPoint(r['day'],outcomes[r['day']]['label'],r['choice'],probs)
            states[r['day'],r['method']]=r['state']
        common=set(outcomes).intersection(*(set(p) for p in by_method.values()))
        complete=bool(expected) and len(common)==len(expected)
        reference={d:by_method['majority'][d] for d in common}
        methods={};shortlist=[]
        for method,points in by_method.items():
            intersection={d:points[d] for d in sorted(common)}
            comparison=paired_blocks(intersection,reference,calendar)
            shortlisted=bool(complete and method!='majority' and comparison['ci95'] and comparison['ci95'][1]<0)
            if shortlisted: shortlist.append(method)
            methods[method]=dict(**metrics(intersection.values()),brier_vs_majority=comparison,
                verdict='入圍：值得再驗證，尚非證明有效' if shortlisted else '未入圍' if complete else '結果不完整，不下結論',
                coverage=len(points)/len(expected) if expected else None,missing=len(set(expected)-set(points)))
        claims=[]
        for name in INDICATORS:
            method='ind_'+name
            groups={state:Counter() for state in (*PROTOCOL['states'][name],'missing')}
            for day in sorted(common): groups.setdefault(states.get((day,method),'missing'),Counter())[outcomes[day]['label']]+=1
            for state,counts in sorted(groups.items()):
                n=sum(counts.values())
                claims.append(dict(indicator=name,state=state,claim=CLAIMS[name].get(state,'無預設方向'),n=n,
                    counts={k:counts[k] for k in LABELS},frequencies={k:counts[k]/n if n else None for k in LABELS},
                    note='描述性、未平滑；bias20 使用當時已揭曉樣本的切點'))
        result['horizons'][str(H)]=dict(threshold=config['thresholds'][str(H)],n=len(common),expected_scorable=len(expected),complete=complete,
            methods=methods,shortlist=shortlist,claims_vs_frequency=claims)
    result['multiplicity']=dict(indicators_per_horizon=11,indicator_tests=33,all_nonreference_tests=48,
        expected_lucky_indicators_per_horizon=11*.025,expected_lucky_indicators_all_horizons=33*.025,
        expected_lucky_all_nonreference_tests=48*.025,
        note='每個天期測了 11 個指標，預期約 11×2.5%=0.275 個因運氣看起來較好；三天期共 33 次為 0.825 個。含基準與 logit 共 48 個非參考比較，約 1.2 個。此為名目估算，未作多重比較校正。')
    return result


def markdown(report):
    text=['# 多日實驗 4：開發段（0 次 Jev）','', '開發段勝出只代表值得再驗證，不是證明有效。差值為方法 − majority；負值較小。', '',report['multiplicity']['note'],'']
    for H,part in report['horizons'].items():
        text += [f'## {H} 個交易日（交集 n={part["n"]}）','', '| 方法 | 準確率 | Brier | 差值 | 95% 區間 | 判讀 |','|---|---:|---:|---:|---|---|']
        for method,r in part['methods'].items():
            comp=r['brier_vs_majority'];ci=comp['ci95']
            accuracy=f'{r["accuracy"]:.4%}' if r['accuracy'] is not None else '無樣本'
            brier=f'{r["brier"]:.6f}' if r['brier'] is not None else '無樣本'
            difference=f'{comp["difference"]:+.6f}' if comp['difference'] is not None else '無樣本'
            interval=f'[{ci[0]:+.6f}, {ci[1]:+.6f}]' if ci else '無樣本'
            text.append(f'| {method} | {accuracy} | {brier} | {difference} | {interval} | {r["verdict"]} |')
        text += ['', '入圍：'+('、'.join(part['shortlist']) or '無'),'','### 網路說法（待驗假說）與開發段實際頻率','',
                 '下列是預先列出的常見訊號解讀，並非證實的網路主張；頻率為同一交集的描述性統計，未平滑。','',
                 '| 指標／狀態 | 常見說法 | n | 漲 | 盤整 | 跌 |','|---|---|---:|---:|---:|---:|']
        for r in part['claims_vs_frequency']:
            p=r['frequencies'];rates=[f'{p[k]:.2%}' if p[k] is not None else '無樣本' for k in LABELS]
            text.append(f'| {r["indicator"]} / {r["state"]} | {r["claim"]} | {r["n"]} | {" | ".join(rates)} |')
        text.append('')
    return '\n'.join(text)+'\n'
