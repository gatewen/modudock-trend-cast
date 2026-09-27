"""Frozen p6 requests, atomic three-horizon commits and development comparisons."""
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from fractions import Fraction
import hashlib
import json
import threading

from .daily_experiment import load_experiment
from .daily_prompt import payload_bytes, questions, sample_dates, ROUND_CALL_LIMIT
from .daily_jev_client import DailyAnswer, validate_daily_response
from .daily_models import prediction
from .daily_replay import DailyReplay, HORIZONS, label
from .daily_run import exact_insert
from .daily_score import paired_blocks
from .data import DataError
from .experiment import MODEL, canonical
from .http_client import ClientError
from .score import ScoredPoint, metrics

SCHEMA='''
CREATE TABLE IF NOT EXISTS d_jev_plan(experiment_id INTEGER PRIMARY KEY,plan_hash TEXT NOT NULL,config_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS d_jev_requests(experiment_id INTEGER NOT NULL,day TEXT NOT NULL,
 body_hash TEXT NOT NULL,body_json TEXT NOT NULL,feature_hash TEXT NOT NULL,
 status TEXT NOT NULL,error TEXT,response_json TEXT,latency REAL,PRIMARY KEY(experiment_id,day));
CREATE TRIGGER IF NOT EXISTS d_jev_plan_no_update BEFORE UPDATE ON d_jev_plan
 BEGIN SELECT RAISE(ABORT,'p6_plan_immutable'); END;
CREATE TRIGGER IF NOT EXISTS d_jev_plan_no_delete BEFORE DELETE ON d_jev_plan
 BEGIN SELECT RAISE(ABORT,'p6_plan_immutable'); END;
CREATE TRIGGER IF NOT EXISTS d_jev_body_no_update BEFORE UPDATE OF body_hash,body_json,feature_hash,day,experiment_id ON d_jev_requests
 BEGIN SELECT RAISE(ABORT,'p6_body_immutable'); END;
CREATE TRIGGER IF NOT EXISTS d_jev_request_dev_only BEFORE INSERT ON d_jev_requests
 WHEN NOT EXISTS(SELECT 1 FROM d_jev_plan p,json_each(p.config_json,'$.days') s
 WHERE p.experiment_id=NEW.experiment_id AND s.value=NEW.day)
 BEGIN SELECT RAISE(ABORT,'p6_outside_sample'); END;
'''


def freeze_plan(store,*,experiment_id=4,split='dev',progress=None):
    if experiment_id!=4 or split!='dev':raise DataError('p6_dev_experiment_4_only')
    row=load_experiment(store,experiment_id);config=row['config']
    sample=sample_dates(store,config);days=sample['days'];requests=[]
    for index,day in enumerate(days):
        # Sampling was already completed using dates. Missing reference data is
        # an error, never a reason to move/replace a chosen sample.
        feature=store.db.execute('SELECT input_hash FROM d_features WHERE experiment_id=4 AND day=?',(day,)).fetchone()
        if feature is None:raise DataError('p6_missing_reference_feature')
        for H in HORIZONS:
            outcome=store.db.execute('SELECT end_day FROM d_outcomes WHERE experiment_id=4 AND H=? AND day=?',(H,day)).fetchone()
            if outcome is None or not day<=outcome['end_day']<=config['dev_end']:raise DataError('p6_missing_dev_outcome')
            for method in ('majority','ind_logit'):
                if store.db.execute('SELECT 1 FROM d_predictions WHERE experiment_id=4 AND H=? AND day=? AND method=?',(H,day,method)).fetchone() is None:
                    raise DataError('p6_missing_reference_answer')
        body=payload_bytes(store,config,day).decode()
        requests.append((day,hashlib.sha256(body.encode()).hexdigest(),body,feature['input_hash']))
        if progress and index%100==0:progress(dict(phase='prepare',done=index+1,total=len(days)))
    definition=dict(method='jev_ind',prompt_version='p6',model=MODEL,experiment_data_digest=row['data_digest'],
        sample_rule=sample['rule'],days=days,step=5,max_calls=ROUND_CALL_LIMIT,
        chip_normalization=dict(volume_unit='shares',foreign_multiplier=1,trust_multiplier=1,margin_multiplier=1000,
            denominator='previous_20_sessions_mean_volume',rounding='HALF_UP_2dp',missing='本期無此資料'),
        questions=questions(config),body_hashes={d:h for d,h,body,feature in requests})
    serialized=canonical(definition);digest=hashlib.sha256(serialized.encode()).hexdigest()
    store.db.executescript(SCHEMA)
    with store.transaction():
        exact_insert(store,'d_jev_plan',dict(experiment_id=4,plan_hash=digest,config_json=serialized),('experiment_id',))
        for day,body_hash,body,feature_hash in requests:
            existing=store.db.execute('SELECT * FROM d_jev_requests WHERE experiment_id=4 AND day=?',(day,)).fetchone()
            if existing:
                if (existing['body_hash'],existing['body_json'],existing['feature_hash'])!=(body_hash,body,feature_hash):
                    raise DataError('p6_request_changed')
            else:store.db.execute('INSERT INTO d_jev_requests VALUES (4,?,?,?,?,?,?,NULL,NULL)',(day,body_hash,body,feature_hash,'pending',None))
    return dict(plan_hash=digest,definition=definition,sample_count=len(days))


def packed(answer):
    body=dict(answers={f'direction_{h}d':dict(choice=a.choice,probabilities=a.probabilities) for h,a in answer.answers.items()})
    if answer.model_reported is not None:body['model']=answer.model_reported
    return body


def stored_complete(store,day):
    r=store.db.execute('SELECT * FROM d_jev_requests WHERE experiment_id=4 AND day=?',(day,)).fetchone()
    saved=list(store.db.execute("SELECT * FROM d_predictions WHERE experiment_id=4 AND method='jev_ind' AND day=?",(day,)))
    if r['status']!='done':
        if saved:raise DataError('p6_partial_commit')
        return False
    if len(saved)!=3 or r['response_json'] is None:raise DataError('p6_partial_commit')
    answer=validate_daily_response(json.loads(r['response_json']))
    for p in saved:
        a=answer.answers[p['H']]
        if p['input_hash']!=r['feature_hash'] or p['choice']!=a.choice or json.loads(p['probabilities_json'])!=a.probabilities:
            raise DataError('p6_stored_answer_mismatch')
    return True


def commit_answer(store,day,answer,cancel):
    answer=DailyAnswer(validate_daily_response(packed(answer)).answers,answer.model_reported,answer.latency_seconds)
    if cancel.is_set():raise ClientError('cancelled')
    with store.transaction():
        row=store.db.execute('SELECT * FROM d_jev_requests WHERE experiment_id=4 AND day=?',(day,)).fetchone()
        if row is None:raise DataError('p6_outside_sample')
        if row['status']=='done':raise DataError('p6_already_completed')
        for H in HORIZONS:
            a=answer.answers[H]
            exact_insert(store,'d_predictions',dict(experiment_id=4,H=H,method='jev_ind',day=day,
                choice=a.choice,probabilities_json=canonical(a.probabilities),state='p6',input_hash=row['feature_hash']),('experiment_id','H','method','day'))
        store.db.execute("UPDATE d_jev_requests SET status='done',error=NULL,response_json=?,latency=? WHERE experiment_id=4 AND day=?",
                         (canonical(packed(answer)),answer.latency_seconds,day))
        if cancel.is_set():raise ClientError('cancelled')


def run_plan(store,client,plan,*,cancel=None,progress=None):
    cancel=cancel if cancel is not None else threading.Event()
    days=tuple(plan['definition']['days']);before={d for d in days if stored_complete(store,d)}
    failures=Counter();new_ok=0
    pending=[d for d in days if d not in before]
    def job(day):
        row=store.db.execute('SELECT body_json,body_hash FROM d_jev_requests WHERE experiment_id=4 AND day=?',(day,)).fetchone()
        body=row['body_json'].encode()
        if hashlib.sha256(body).hexdigest()!=row['body_hash']:raise DataError('p6_body_hash_mismatch')
        return body
    def save(day,future):
        nonlocal new_ok
        try:
            answer=future.result();commit_answer(store,day,answer,cancel);new_ok+=1
        except ClientError as e:
            failures[e.code]+=1
            with store.transaction():store.db.execute("UPDATE d_jev_requests SET status='failed',error=? WHERE experiment_id=4 AND day=? AND status!='done'",(e.code,day))
        if progress:progress(dict(phase='run',ok=len(before)+new_ok,total=len(days),failed_attempts=sum(failures.values()),**client.budget.summary()))
    try:
        # Validate the first real response before opening the full six-worker window.
        first=True
        while pending and not cancel.is_set() and not client.budget.exhausted() and client.enabled():
            queue=iter(pending);active={}
            with ThreadPoolExecutor(max_workers=6) as pool:
                def fill():
                    limit=1 if first else 6
                    while len(active)<limit and not cancel.is_set() and not client.budget.exhausted() and client.enabled():
                        day=next(queue,None)
                        if day is None:break
                        active[pool.submit(client.predict,day,job(day),cancel=cancel)]=day
                fill()
                try:
                    while active:
                        done,_=wait(active,return_when=FIRST_COMPLETED)
                        for future in done:save(active.pop(future),future)
                        if first:
                            first=False
                            if not new_ok and not before:return dict(completed=len(before),missing=len(days)-len(before),new_ok=0,errors=dict(failures))
                        fill()
                except BaseException:
                    cancel.set()
                    for future in active:future.cancel()
                    raise
            pending=[d for d in days if not stored_complete(store,d)]
    except KeyboardInterrupt:
        cancel.set();raise
    return dict(completed=len(days)-len(pending),missing=len(pending),new_ok=new_ok,skipped=len(before),errors=dict(failures))


def report(store):
    row=load_experiment(store,4);config=row['config']
    plan=store.db.execute('SELECT * FROM d_jev_plan WHERE experiment_id=4').fetchone()
    definition=json.loads(plan['config_json']);days=tuple(definition['days'])
    if tuple(sample_dates(store,config)['days'])!=days or definition['questions']!=questions(config):raise DataError('p6_plan_changed')
    complete_days=[d for d in days if stored_complete(store,d)]
    calendar=tuple(r[0] for r in store.db.execute('SELECT day FROM d_calendar WHERE day BETWEEN ? AND ? ORDER BY day',
                                               (config['dev_start'],config['dev_end'])))
    engine=DailyReplay(store,start=config['start']);parts={}
    for H in HORIZONS:
        methods={name:{} for name in ('jev_ind','majority','ind_logit')}
        k=Fraction(**config['thresholds'][str(H)])
        for day in complete_days:
            o=engine.outcome(day,H,end_limit=config['dev_end'])
            if not o.scorable:raise DataError('p6_unscorable_sample')
            truth=label(o.adjusted_return,k)
            stored=store.db.execute('SELECT label,end_day FROM d_outcomes WHERE experiment_id=4 AND H=? AND day=?',(H,day)).fetchone()
            if not stored or (stored['label'],stored['end_day'])!=(truth,o.end_day):raise DataError('p6_outcome_mismatch')
            for method in methods:
                r=store.db.execute('SELECT * FROM d_predictions WHERE experiment_id=4 AND H=? AND day=? AND method=?',(H,day,method)).fetchone()
                if r is None:raise DataError('p6_missing_reference_answer')
                probs=json.loads(r['probabilities_json'])
                if method!='jev_ind' and prediction(method,probs).answer!=r['choice']:raise DataError('p6_reference_choice_mismatch')
                methods[method][day]=ScoredPoint(day,truth,r['choice'],probs)
        parts[str(H)]=dict(n=len(complete_days),jev_ind=metrics(methods['jev_ind'].values()),
            references={m:metrics(methods[m].values()) for m in ('majority','ind_logit')},
            comparisons={m:paired_blocks(methods['jev_ind'],methods[m],calendar) for m in ('majority','ind_logit')},
            choices={label:sum(p.choice==label for p in methods['jev_ind'].values()) for label in ('up','flat','down')})
    return dict(experiment=4,method='jev_ind',prompt_version='p6',sample_count=len(days),complete=len(complete_days)==len(days),
                missing_days=len(days)-len(complete_days),missing_answers=3*(len(days)-len(complete_days)),plan_hash=plan['plan_hash'],horizons=parts)
