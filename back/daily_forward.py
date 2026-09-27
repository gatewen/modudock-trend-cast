"""Frozen experiment-4 forward forecasts, immutable inputs and exact maturity.

Training is ONLY development. Post-freeze observations are features, never fits.
The separate tables do not weaken the development/holdout write guards.
"""
from dataclasses import asdict
from datetime import datetime, time, timedelta
from fractions import Fraction
import hashlib
import json
from pathlib import Path

from .daily_config import PROTOCOL
from .daily_experiment import load_experiment
from .daily_indicators import frames
from .daily_models import Logistic, probabilities, prediction, cuts, bucket
from .daily_prompt import questions, chip_percentages, describe_indicators
from .daily_replay import DailyReplay, DEV_END, HORIZONS, label
from .daily_run import prepare_development, development_records
from .daily_jev import packed
from .daily_jev_client import validate_daily_response
from .data import DataError, TAIPEI, day_value
from .experiment import canonical, MODEL

METHODS=('majority','ind_logit','vol_prior_d','jev_ind')
EMPTY='尚無前瞻預測，下一個交易日收盤後自動產生'
DISCLAIMER='這是方法的機率判斷，不是投資建議；過去在開發段沒有勝過簡單方法'
NORMALIZATION=dict(volume_unit='shares',foreign_multiplier=1,trust_multiplier=1,margin_multiplier=1000,
    denominator='previous_20_sessions_mean_volume',rounding='HALF_UP_2dp',missing='本期無此資料')
SCHEMA='''
CREATE TABLE IF NOT EXISTS d_forward_model(
 experiment_id INTEGER PRIMARY KEY CHECK(experiment_id=4),policy_json TEXT NOT NULL,policy_hash TEXT NOT NULL,
 model_json TEXT NOT NULL,model_hash TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS d_forward_days(
 day TEXT PRIMARY KEY,model_hash TEXT NOT NULL,input_json TEXT NOT NULL,input_hash TEXT NOT NULL,
 body_json TEXT NOT NULL,body_hash TEXT NOT NULL,created_at TEXT NOT NULL,
 jev_state TEXT NOT NULL DEFAULT 'pending',error TEXT,response_json TEXT);
CREATE TABLE IF NOT EXISTS d_forward_predictions(
 day TEXT NOT NULL,H INTEGER NOT NULL CHECK(H IN (3,7,14)),method TEXT NOT NULL,
 choice TEXT NOT NULL,probabilities_json TEXT NOT NULL,recorded_at TEXT NOT NULL,
 timing TEXT NOT NULL,model_hash TEXT NOT NULL,input_hash TEXT NOT NULL,
 PRIMARY KEY(day,H,method));
CREATE TABLE IF NOT EXISTS d_forward_outcomes(
 day TEXT NOT NULL,H INTEGER NOT NULL,end_day TEXT NOT NULL,label TEXT NOT NULL,
 return_numerator TEXT NOT NULL,return_denominator TEXT NOT NULL,scored_at TEXT NOT NULL,
 PRIMARY KEY(day,H));
CREATE TRIGGER IF NOT EXISTS d_forward_model_immutable BEFORE UPDATE ON d_forward_model
 BEGIN SELECT RAISE(ABORT,'forward_model_immutable'); END;
CREATE TRIGGER IF NOT EXISTS d_forward_model_no_delete BEFORE DELETE ON d_forward_model
 BEGIN SELECT RAISE(ABORT,'forward_model_immutable'); END;
CREATE TRIGGER IF NOT EXISTS d_forward_input_immutable BEFORE UPDATE OF day,model_hash,input_json,input_hash,body_json,body_hash,created_at ON d_forward_days
 BEGIN SELECT RAISE(ABORT,'forward_input_immutable'); END;
CREATE TRIGGER IF NOT EXISTS d_forward_day_boundary BEFORE INSERT ON d_forward_days
 WHEN NOT EXISTS(SELECT 1 FROM d_experiments e WHERE e.id=4 AND NEW.day>substr(e.created_at,1,10))
 BEGIN SELECT RAISE(ABORT,'forward_day_before_freeze'); END;
CREATE TRIGGER IF NOT EXISTS d_forward_predictions_boundary BEFORE INSERT ON d_forward_predictions
 WHEN NOT EXISTS(SELECT 1 FROM d_forward_days d WHERE d.day=NEW.day AND d.model_hash=NEW.model_hash AND d.input_hash=NEW.input_hash)
 BEGIN SELECT RAISE(ABORT,'forward_unknown_input'); END;
CREATE TRIGGER IF NOT EXISTS d_forward_prediction_immutable BEFORE UPDATE OF day,H,method,choice,probabilities_json,recorded_at,model_hash,input_hash ON d_forward_predictions
 BEGIN SELECT RAISE(ABORT,'forward_prediction_immutable'); END;
CREATE TRIGGER IF NOT EXISTS d_forward_outcome_boundary BEFORE INSERT ON d_forward_outcomes
 WHEN NEW.end_day<=NEW.day OR NOT EXISTS(SELECT 1 FROM d_forward_days d WHERE d.day=NEW.day)
 BEGIN SELECT RAISE(ABORT,'forward_unknown_input'); END;
CREATE TRIGGER IF NOT EXISTS d_forward_outcome_immutable BEFORE UPDATE ON d_forward_outcomes
 BEGIN SELECT RAISE(ABORT,'forward_outcome_immutable'); END;
'''


def clock_now(): return datetime.now(TAIPEI)
def stamp(value):
    if not isinstance(value,datetime) or value.tzinfo is None:raise DataError('forward_clock_timezone')
    return value.astimezone(TAIPEI).isoformat(timespec='microseconds')
def parsed(value):
    result=datetime.fromisoformat(value)
    if result.tzinfo is None:raise DataError('forward_clock_timezone')
    return result.astimezone(TAIPEI)
def sha(value):return hashlib.sha256(canonical(value).encode()).hexdigest()
def exists(store):return store.db.execute("SELECT 1 FROM sqlite_master WHERE name='d_forward_days'").fetchone() is not None

def ensure_schema(store):store.db.executescript(SCHEMA)

def policy(store):
    row=load_experiment(store);c=row['config']
    p=store.db.execute('SELECT * FROM d_jev_plan WHERE experiment_id=4').fetchone()
    if p is None:raise DataError('forward_missing_p6_plan')
    plan=json.loads(p['config_json'])
    if (sha(plan)!=p['plan_hash'] or plan.get('questions')!=questions(c) or plan.get('model')!=MODEL
        or plan.get('experiment_data_digest')!=row['data_digest'] or plan.get('chip_normalization')!=NORMALIZATION):
        raise DataError('forward_frozen_settings_changed')
    # Pin code implementing the declared parameters, in addition to the config.
    code={name:hashlib.sha256((Path(__file__).parent/name).read_bytes()).hexdigest() for name in
        ('daily_config.py','daily_models.py','daily_indicators.py','daily_replay.py','daily_prompt.py')}
    definition=dict(experiment_data_digest=row['data_digest'],dev_digest=row['dev_digest'],created_at=row['created_at'],
        methods=METHODS,protocol=PROTOCOL,thresholds=c['thresholds'],questions=questions(c),normalization=NORMALIZATION,
        training='all_scorable_development_endpoints',prompt_version='p6',model=MODEL,code=code)
    return row,definition


def load_model(store):
    row,definition=policy(store)
    old=store.db.execute('SELECT * FROM d_forward_model WHERE experiment_id=4').fetchone()
    if old is None:return row,None
    model=json.loads(old['model_json'])
    if old['policy_json']!=canonical(definition) or old['policy_hash']!=sha(definition) or old['model_hash']!=sha(model):
        raise DataError('forward_frozen_settings_changed')
    return row,dict(model=model,model_hash=old['model_hash'],policy=definition)


def build_model(store):
    row,definition=policy(store);points=prepare_development(store,row)
    if not points:raise DataError('forward_missing_training')
    model=dict(horizons={})
    for H in HORIZONS:
        _,records,_=development_records(store,row,points,H)
        majority=probabilities(r.truth for r in records)
        boundaries=cuts(r.frame.volatility for r in records)
        groups={}
        for state in ('low','middle','high'):
            truths=[r.truth for r in records if bucket(r.frame.volatility,boundaries)==state]
            groups[state]=probabilities(truths) if len(truths)>=30 else majority
        fitted=asdict(Logistic.fit(records,points[-1])) if len(records)>=250 else None
        model['horizons'][str(H)]=dict(majority=majority,vol_boundaries=boundaries,vol_groups=groups,
            bias_boundaries=cuts(r.frame.values.get('bias20') for r in records),logit=fitted,
            training_digest=sha([(r.frame.digest,r.end_day,r.truth) for r in records]),train_n=len(records))
    return dict(policy=definition,model=model,model_hash=sha(model))


def install_model(store,bundle):
    _,definition=policy(store)
    if canonical(definition)!=canonical(bundle['policy']) or sha(bundle['model'])!=bundle['model_hash']:
        raise DataError('forward_frozen_settings_changed')
    with store.transaction():
        store.db.execute('INSERT OR IGNORE INTO d_forward_model VALUES (4,?,?,?,?)',
            (canonical(definition),sha(definition),canonical(bundle['model']),bundle['model_hash']))
    _,loaded=load_model(store)
    if loaded['model_hash']!=bundle['model_hash']:raise DataError('forward_model_changed')


def closed_end(now):
    now=parsed(stamp(now));day=now.date()
    if now.time()<time(16,30):day-=timedelta(days=1)
    return day.isoformat()

def candidates(store,row,now):
    start=parsed(row['created_at']).date().isoformat();end=closed_end(now)
    return tuple(r[0] for r in store.db.execute('''SELECT c.day FROM d_calendar c JOIN d_bars b ON b.day=c.day
        WHERE b.symbol=? AND c.day>? AND c.day<=? ORDER BY c.day''',(row['config']['symbol'],start,end)))

def next_deadline(store,day):
    row=store.db.execute('SELECT min(day) FROM d_calendar WHERE day>?',(day,)).fetchone()
    return datetime.combine(day_value(row[0]),time(9),TAIPEI) if row and row[0] else None

def timing(store,day,recorded_at):
    deadline=next_deadline(store,day)
    if deadline is None:return 'unconfirmed'
    return 'ontime' if parsed(recorded_at)<deadline else 'backfill'


def prepare_day(store,row,bundle,day):
    c=row['config'];day_value(day)
    if day<=parsed(row['created_at']).date().isoformat():raise DataError('forward_day_before_freeze')
    history=frames(store,start=c['start'],end=day,symbol=c['symbol'])
    if not history or history[-1].day!=day or not history[-1].predictable:raise DataError('forward_not_predictable')
    point=history[-1];prepared=DailyReplay(store,start=c['start'],symbol=c['symbol']).prepare(day)
    if not prepared.predictable:raise DataError('forward_not_predictable')
    answers={};groups={}
    for H in HORIZONS:
        m=bundle['model']['horizons'][str(H)];groups[H]=bucket(point.values.get('bias20'),m['bias_boundaries'])
        majority=prediction('majority',m['majority'])
        state=bucket(point.volatility,m['vol_boundaries'])
        vol=prediction('vol_prior_d',m['vol_groups'].get(state,m['majority']))
        logit=Logistic(**m['logit']).predict(point) if m['logit'] else prediction('ind_logit',m['majority'])
        answers[H]={p.method:dict(choice=p.answer,probabilities=p.probabilities) for p in (majority,vol,logit)}
    # Exact p6 instructions, projection and units; only dynamic bias cuts are now frozen to development.
    state=dict(daily=json.loads(prepared.input_json)['daily'],indicators=describe_indicators(
        (),point,chip_percentages(store,c,point),groups=groups))
    body=dict(model=MODEL,state=state,questions=questions(c))
    input_json=canonical(dict(feature=json.loads(point.serialized()),daily=json.loads(prepared.input_json)))
    return dict(day=day,model_hash=bundle['model_hash'],input_json=input_json,
        input_hash=hashlib.sha256(input_json.encode()).hexdigest(),body_json=canonical(body),body_hash=sha(body),answers=answers)


def save_baselines(store,prepared,now):
    row,bundle=load_model(store);day=prepared['day']
    if bundle is None or bundle['model_hash']!=prepared['model_hash']:raise DataError('forward_frozen_settings_changed')
    current=lambda:now() if callable(now) else now
    if day not in candidates(store,row,current()):raise DataError('forward_day_unavailable')
    with store.transaction():
        recorded=stamp(current())
        old=store.db.execute('SELECT * FROM d_forward_days WHERE day=?',(day,)).fetchone()
        if old:return False
        store.db.execute('''INSERT INTO d_forward_days(day,model_hash,input_json,input_hash,body_json,body_hash,created_at)
            VALUES (?,?,?,?,?,?,?)''',tuple(prepared[k] for k in ('day','model_hash','input_json','input_hash','body_json','body_hash'))+(recorded,))
        for H,answers in prepared['answers'].items():
            for method,answer in answers.items():
                store.db.execute('INSERT INTO d_forward_predictions VALUES (?,?,?,?,?,?,?,?,?)',
                    (day,H,method,answer['choice'],canonical(answer['probabilities']),recorded,timing(store,day,recorded),prepared['model_hash'],prepared['input_hash']))
    return True


def claim_request(store,day):
    _,bundle=load_model(store)
    with store.transaction():
        row=store.db.execute('SELECT * FROM d_forward_days WHERE day=?',(day,)).fetchone()
        if row is None or row['model_hash']!=bundle['model_hash']:raise DataError('forward_frozen_settings_changed')
        if row['jev_state'] not in ('pending','retry_wait'):return None
        if hashlib.sha256(row['body_json'].encode()).hexdigest()!=row['body_hash']:raise DataError('forward_input_changed')
        store.db.execute("UPDATE d_forward_days SET jev_state='reserved',error=NULL WHERE day=?",(day,))
        return row['body_json'].encode()


def save_jev(store,day,answer,now):
    _,bundle=load_model(store);validated=validate_daily_response(packed(answer))
    with store.transaction():
        recorded=stamp(now() if callable(now) else now)
        row=store.db.execute('SELECT * FROM d_forward_days WHERE day=?',(day,)).fetchone()
        if row is None or row['jev_state']!='reserved' or row['model_hash']!=bundle['model_hash']:raise DataError('forward_request_not_reserved')
        for H,a in validated.answers.items():
            store.db.execute('INSERT INTO d_forward_predictions VALUES (?,?,?,?,?,?,?,?,?)',
                (day,H,'jev_ind',a.choice,canonical(a.probabilities),recorded,timing(store,day,recorded),row['model_hash'],row['input_hash']))
        store.db.execute("UPDATE d_forward_days SET jev_state='done',error=NULL,response_json=? WHERE day=?",(canonical(packed(answer)),day))


def settle(store,now):
    row,_=load_model(store);end=closed_end(now);start=parsed(row['created_at']).date().isoformat()
    days=[r[0] for r in store.db.execute('SELECT day FROM d_forward_days WHERE day>? AND day<=? ORDER BY day',(start,end))]
    engine=DailyReplay(store,start=row['config']['start']);new=0
    with store.transaction():
        for day in days:
            for p in store.db.execute('SELECT * FROM d_forward_predictions WHERE day=?',(day,)).fetchall():
                value=timing(store,day,p['recorded_at'])
                if value!=p['timing']:store.db.execute('UPDATE d_forward_predictions SET timing=? WHERE day=? AND H=? AND method=?',(value,day,p['H'],p['method']))
            for H in HORIZONS:
                if store.db.execute('SELECT 1 FROM d_forward_outcomes WHERE day=? AND H=?',(day,H)).fetchone():continue
                result=engine.outcome(day,H,end_limit=end)
                if not result.scorable:continue
                threshold=Fraction(**row['config']['thresholds'][str(H)])
                store.db.execute('INSERT INTO d_forward_outcomes VALUES (?,?,?,?,?,?,?)',
                    (day,H,result.end_day,label(result.adjusted_return,threshold),str(result.adjusted_return.numerator),str(result.adjusted_return.denominator),stamp(now)))
                new+=1
    return new
