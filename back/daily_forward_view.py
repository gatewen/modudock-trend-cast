"""Separate, read-only forward exit. Never reads research outcomes after dev."""
import json
import math

from .daily_forward import METHODS,EMPTY,DISCLAIMER,exists,parsed,load_model
from .daily_experiment import load_experiment
from .daily_replay import horizon
from .daily_score import paired_blocks
from .data import DataError
from .score import ScoredPoint,metrics,conclusion
from .news_digest import exists as has_table

DISPLAY_METHODS=(*METHODS,'jev_news')


def news_comparisons(points,calendar,cohort):
    result={}
    for baseline in ('jev_ind','majority'):
        comp=paired_blocks(points['jev_news'],points[baseline],calendar)
        complete=comp['n']>=60 and comp['blocks']>=2 and cohort!='unconfirmed'
        comp['verdict']=conclusion(baseline,comp['difference'],comp['ci95'],common_n=comp['n'],
            eligible_n=comp['n'],complete=complete).replace('jev 比','jev_news 比').replace('顯示 jev 比','顯示 jev_news 比')
        comp['small_sample']=comp['n']<60
        result[baseline]=comp
    return result


def forward_view(store,body):
    if (set(body)-{'op','H','experiment_id','request_id'} or body.get('op')!='daily_forward'
        or type(body.get('experiment_id',4)) is not int or body.get('experiment_id',4)!=4):
        raise DataError('invalid_request')
    H=horizon(body.get('H',7));row=load_experiment(store)
    floor=parsed(row['created_at']).date().isoformat()
    result=dict(status='ok',experiment_id=4,H=H,split='forward',frozen_day=floor,
        message=EMPTY,disclaimer=DISCLAIMER,latest=None,cohorts=[],pending=[],missing=[],recorded_days=0)
    # Only post-freeze dates may reach the forward view; older data stays unnamed.
    last=store.db.execute('SELECT max(day) FROM d_bars WHERE symbol=?',(row['config']['symbol'],)).fetchone()[0]
    result['data_through']=last if last and last>floor else None
    if not exists(store):return result
    load_model(store)  # Changed config/models cannot silently produce a report.
    days=[dict(r) for r in store.db.execute('SELECT day,jev_state,error FROM d_forward_days WHERE day>? ORDER BY day',(floor,))]
    if not days:return result
    allowed={d['day'] for d in days}
    outcomes={(r['day'],r['H']):dict(r) for r in store.db.execute('SELECT * FROM d_forward_outcomes WHERE day>? AND end_day>day',(floor,)) if r['day'] in allowed}
    predictions=[]
    for r in store.db.execute('SELECT * FROM d_forward_predictions WHERE day>? ORDER BY day,H,method',(floor,)):
        if r['day'] not in allowed:continue
        p=dict(r);probs=json.loads(p.pop('probabilities_json'))
        if (p['method'] not in DISPLAY_METHODS or p['timing'] not in ('ontime','backfill','unconfirmed')
            or set(probs)!={'up','flat','down'} or any(type(v) not in (int,float) or not math.isfinite(v) or not 0<=v<=1 for v in probs.values())
            or abs(sum(probs.values())-1)>.01 or p['choice'] not in probs or probs[p['choice']]!=max(probs.values())):
            raise DataError('forward_invalid_prediction')
        p['probabilities']=probs;predictions.append(p)
    last=days[-1]['day']
    public=('H','method','choice','probabilities','recorded_at','timing')
    result.update(message='',recorded_days=len(days),latest=dict(day=last,jev_state=days[-1]['jev_state'],
        predictions=[{k:p[k] for k in public} for p in predictions if p['day']==last]))
    news_rows={r['day']:dict(r) for r in store.db.execute('SELECT day,jev_state FROM news_forward_requests WHERE day>?',(floor,))} if has_table(store,'news_forward_requests') else {}
    result['latest']['news_state']=news_rows.get(last,{}).get('jev_state','no_news')
    calendar=tuple(r[0] for r in store.db.execute('SELECT day FROM d_calendar WHERE day>? ORDER BY day',(floor,)))
    for cohort in ('ontime','backfill','unconfirmed'):
        points={m:{} for m in DISPLAY_METHODS};expected={m:0 for m in DISPLAY_METHODS}
        for p in predictions:
            if p['H']!=H or p['timing']!=cohort:continue
            expected[p['method']]+=1;outcome=outcomes.get((p['day'],H))
            if outcome:points[p['method']][p['day']]=ScoredPoint(p['day'],outcome['label'],p['choice'],p['probabilities'])
        comp=paired_blocks(points['jev_ind'],points['majority'],calendar)
        # A single 20-day block cannot support a sampling-uncertainty claim.
        eligible=len(points['majority']);complete=comp['blocks']>=2 and cohort!='unconfirmed'
        verdict=conclusion('majority',comp['difference'],comp['ci95'],common_n=comp['n'],eligible_n=eligible,complete=complete)
        comp['verdict']=verdict;comp['small_sample']=comp['blocks']<2
        stats=[]
        for method in DISPLAY_METHODS:
            m=metrics(points[method].values())
            # Coverage uses majority's cohort dates, including absent jev responses.
            origins={p['day'] for p in predictions if p['H']==H and p['method']=='majority' and p['timing']==cohort}
            present={p['day'] for p in predictions if p['H']==H and p['method']==method and p['timing']==cohort}
            stats.append(dict(method=method,**m,recorded=expected[method],
                coverage=len(origins&present)/len(origins) if origins else None,missing=len(origins-present)))
        result['cohorts'].append(dict(timing=cohort,methods=stats,comparison=comp,
            news_comparisons=news_comparisons(points,calendar,cohort)))
    for d in days:
        future=[v for v in calendar if v>d['day']]
        if d['jev_state']!='done':result['missing'].append(dict(day=d['day'],state=d['jev_state']))
        for h in (3,7,14):
            if (d['day'],h) not in outcomes:
                result['pending'].append(dict(day=d['day'],H=h,end_day=future[h-1] if len(future)>=h else None,
                    remaining=max(0,h-len(future))))
    result['pending_total']=len(result['pending']);result['missing_total']=len(result['missing'])
    # Per-horizon totals are counted before the list is truncated for transport.
    result['pending_counts']={str(h):sum(p['H']==h for p in result['pending']) for h in (3,7,14)}
    result['pending']=result['pending'][-120:];result['missing']=result['missing'][-40:]
    return result
