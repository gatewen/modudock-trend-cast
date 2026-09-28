"""Append-only holdout namespace; outcomes need a complete prediction seal."""
import hashlib
import json

from .daily_experiment import load_experiment,digest,source_summary
from .daily_hold_model import POLICY,build
from .daily_replay import HOLD_START,HOLD_END
from .daily_reveal import ensure_registry,table_exists
from .data import DataError
from .experiment import canonical,exposed_days
from .market_sources import SOURCES,positive
from .store import now_string

def sha(value):return hashlib.sha256(canonical(value).encode()).hexdigest()

SCHEMA='''
CREATE TABLE IF NOT EXISTS d_hold_models(experiment_id INTEGER PRIMARY KEY CHECK(experiment_id=4),
 created_at TEXT NOT NULL,policy_json TEXT NOT NULL,model_json TEXT NOT NULL,model_hash TEXT NOT NULL,preflight_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS d_hold_market_rows(series TEXT NOT NULL,day TEXT NOT NULL CHECK(day BETWEEN '2022-01-03' AND '2024-07-25'),
 close TEXT,spot_buy TEXT,spot_sell TEXT,PRIMARY KEY(series,day));
CREATE TABLE IF NOT EXISTS d_hold_market_snapshot(experiment_id INTEGER PRIMARY KEY CHECK(experiment_id=4),
 created_at TEXT NOT NULL,digest TEXT NOT NULL,sources_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS d_hold_features(experiment_id INTEGER NOT NULL CHECK(experiment_id=4),day TEXT NOT NULL,
 input_json TEXT NOT NULL,input_hash TEXT NOT NULL,alignment_json TEXT NOT NULL,PRIMARY KEY(experiment_id,day));
CREATE TABLE IF NOT EXISTS d_hold_predictions(experiment_id INTEGER NOT NULL CHECK(experiment_id=4),H INTEGER NOT NULL CHECK(H IN (3,7,14)),
 method TEXT NOT NULL,day TEXT NOT NULL,choice TEXT NOT NULL,probabilities_json TEXT NOT NULL,state TEXT,input_hash TEXT NOT NULL,model_hash TEXT NOT NULL,
 PRIMARY KEY(experiment_id,H,method,day));
CREATE TABLE IF NOT EXISTS d_hold_completion(experiment_id INTEGER PRIMARY KEY CHECK(experiment_id=4),
 completed_at TEXT NOT NULL,n_days INTEGER NOT NULL,n_predictions INTEGER NOT NULL,prediction_digest TEXT NOT NULL,feature_digest TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS d_hold_outcomes(experiment_id INTEGER NOT NULL CHECK(experiment_id=4),H INTEGER NOT NULL CHECK(H IN (3,7,14)),
 day TEXT NOT NULL,end_day TEXT NOT NULL,label TEXT NOT NULL CHECK(label IN ('up','flat','down')),
 return_numerator TEXT NOT NULL,return_denominator TEXT NOT NULL,PRIMARY KEY(experiment_id,H,day));
CREATE TABLE IF NOT EXISTS d_hold_seal(experiment_id INTEGER PRIMARY KEY CHECK(experiment_id=4),
 revealed_at TEXT NOT NULL,n_outcomes INTEGER NOT NULL,outcome_digest TEXT NOT NULL);
'''


def preflight(store):
    counts={}
    for table in ('d_features','d_fits','d_predictions','d_outcomes','d_jev_requests','market_features','market_fits','market_predictions',
                  'd_forward_days','d_forward_predictions','d_forward_outcomes','d_hold_features','d_hold_predictions','d_hold_outcomes'):
        if not table_exists(store,table):continue
        cols={r[1] for r in store.db.execute(f'PRAGMA table_info({table})')}
        exp=' AND experiment_id=4' if 'experiment_id' in cols else ''
        counts[table]=store.db.execute(f'SELECT count(*) FROM {table} WHERE day BETWEEN ? AND ?'+exp,(HOLD_START,HOLD_END)).fetchone()[0]
    reveals=store.db.execute('SELECT count(*) FROM reveals WHERE experiment_id=4 OR (first_day<=? AND last_day>=?)',(HOLD_END,HOLD_START)).fetchone()[0]
    days=[r[0] for r in store.db.execute('SELECT day FROM d_calendar WHERE day BETWEEN ? AND ?',(HOLD_START,HOLD_END))]
    if any(counts.values()) or reveals or exposed_days(store,'2330',days):raise DataError('daily_holdout_not_pristine')
    return dict(checked_at=now_string(),derived_rows=counts,reveals=reveals,pristine=True)


def ensure_schema(store):
    store.db.executescript(SCHEMA);ensure_registry(store)
    for table in ('d_hold_models','d_hold_market_snapshot','d_hold_completion','d_hold_seal'):
        store.db.execute(f'''CREATE TRIGGER IF NOT EXISTS {table}_no_replace BEFORE INSERT ON {table}
            WHEN EXISTS(SELECT 1 FROM {table}) BEGIN SELECT RAISE(ABORT,'holdout_immutable'); END''')
    for table in ('d_hold_models','d_hold_market_snapshot','d_hold_features','d_hold_predictions','d_hold_completion','d_hold_outcomes','d_hold_seal'):
        for op in ('UPDATE','DELETE'):
            store.db.execute(f'''CREATE TRIGGER IF NOT EXISTS {table}_no_{op.lower()} BEFORE {op} ON {table}
                BEGIN SELECT RAISE(ABORT,'holdout_immutable'); END''')
    for op in ('INSERT','UPDATE','DELETE'):
        store.db.execute(f'''CREATE TRIGGER IF NOT EXISTS d_hold_market_rows_no_{op.lower()} BEFORE {op} ON d_hold_market_rows
            WHEN EXISTS(SELECT 1 FROM d_hold_market_snapshot) BEGIN SELECT RAISE(ABORT,'holdout_market_frozen'); END''')
    for table in ('d_hold_features','d_hold_predictions','d_hold_outcomes'):
        keys='OLD.experiment_id=NEW.experiment_id AND OLD.day=NEW.day'
        if table!='d_hold_features':keys+=' AND OLD.H=NEW.H'
        if table=='d_hold_predictions':keys+=' AND OLD.method=NEW.method'
        store.db.execute(f'''CREATE TRIGGER IF NOT EXISTS {table}_no_replace BEFORE INSERT ON {table}
            WHEN EXISTS(SELECT 1 FROM {table} OLD WHERE {keys}) BEGIN SELECT RAISE(ABORT,'holdout_immutable'); END''')
        endpoint=" OR NEW.end_day<NEW.day OR NEW.end_day>'2024-07-25'" if table=='d_hold_outcomes' else ''
        store.db.execute(f'''CREATE TRIGGER IF NOT EXISTS {table}_boundary BEFORE INSERT ON {table}
            WHEN NEW.day<'2022-01-03' OR NEW.day>'2024-07-25'{endpoint}
            BEGIN SELECT RAISE(ABORT,'holdout_date_boundary'); END''')
    for table in ('d_hold_features','d_hold_predictions'):
        store.db.execute(f'''CREATE TRIGGER IF NOT EXISTS {table}_sealed BEFORE INSERT ON {table}
            WHEN EXISTS(SELECT 1 FROM d_hold_completion) BEGIN SELECT RAISE(ABORT,'holdout_predictions_sealed'); END''')
    store.db.execute('''CREATE TRIGGER IF NOT EXISTS d_hold_outcomes_predicted BEFORE INSERT ON d_hold_outcomes
        WHEN NOT EXISTS(SELECT 1 FROM d_hold_completion c WHERE c.experiment_id=4 AND c.n_predictions=(SELECT count(*) FROM d_hold_predictions))
        BEGIN SELECT RAISE(ABORT,'holdout_predictions_incomplete'); END''')
    store.db.execute('''CREATE TRIGGER IF NOT EXISTS reveals_daily_complete BEFORE INSERT ON reveals
        WHEN NEW.namespace='daily' AND (NEW.experiment_id!=4 OR NEW.what!='all' OR NEW.segment!='holdout'
        OR NEW.first_day!='2022-01-03' OR NEW.last_day!='2024-07-25'
        OR NOT EXISTS(SELECT 1 FROM d_hold_seal s JOIN d_hold_completion c ON c.experiment_id=s.experiment_id
            WHERE s.experiment_id=4 AND s.n_outcomes=(SELECT count(*) FROM d_hold_outcomes)
            AND c.n_predictions=(SELECT count(*) FROM d_hold_predictions)))
        BEGIN SELECT RAISE(ABORT,'holdout_reveal_incomplete'); END''')
    store.db.execute('''CREATE TRIGGER IF NOT EXISTS reveals_daily_once BEFORE INSERT ON reveals
        WHEN NEW.namespace='daily' AND EXISTS(SELECT 1 FROM reveals WHERE namespace='daily' AND experiment_id=NEW.experiment_id)
        BEGIN SELECT RAISE(ABORT,'holdout_already_revealed'); END''')
    for op in ('UPDATE','DELETE'):
        store.db.execute(f'''CREATE TRIGGER IF NOT EXISTS reveals_daily_no_{op.lower()} BEFORE {op} ON reveals
            WHEN OLD.namespace='daily' BEGIN SELECT RAISE(ABORT,'holdout_reveal_permanent'); END''')
    store.db.commit()


def verify_original(store):
    row=load_experiment(store)
    if digest(row['config'],source_summary(store,row['config'],HOLD_END))!=row['data_digest']:
        raise DataError('holdout_original_source_changed')
    return row


def freeze_models(store):
    if store.db.execute('SELECT 1 FROM d_hold_models').fetchone():return load_models(store)
    audit=preflight(store);verify_original(store);bundle=build(store)
    with store.transaction():
        preflight(store)
        store.db.execute('INSERT INTO d_hold_models VALUES (4,?,?,?,?,?)',
            (now_string(),canonical(POLICY),canonical(bundle),sha(bundle),canonical(audit)))
    return load_models(store)


def load_models(store):
    r=store.db.execute('SELECT * FROM d_hold_models WHERE experiment_id=4').fetchone()
    if r is None:raise DataError('holdout_models_missing')
    model=json.loads(r['model_json'])
    if r['policy_json']!=canonical(POLICY) or r['model_hash']!=sha(model):raise DataError('holdout_models_changed')
    return dict(r,model=model)


def market_rows(store):
    return [dict(r) for r in store.db.execute('SELECT * FROM d_hold_market_rows ORDER BY series,day')]


def install_market(store,batches):
    load_models(store);batches=tuple(batches)
    if len(batches)!=4 or {b.series for b in batches}!=set(SOURCES):raise DataError('holdout_market_incomplete')
    for b in batches:
        if (b.start,b.end)!=(HOLD_START,HOLD_END) or not b.rows:raise DataError('holdout_market_incomplete')
        for r in b.rows:
            if r['series']!=b.series or not HOLD_START<=r['day']<=HOLD_END:raise DataError('holdout_market_boundary')
            if any(r[k] is not None and positive(r[k])!=r[k] for k in ('close','spot_buy','spot_sell')):raise DataError('holdout_market_invalid')
    sources={b.series:dict(rows=len(b.rows),payload_hash=b.payload_hash,start=b.start,end=b.end) for b in batches}
    with store.transaction():
        if store.db.execute('SELECT 1 FROM d_hold_market_snapshot').fetchone():raise DataError('holdout_market_frozen')
        for b in batches:
            store.db.executemany('INSERT INTO d_hold_market_rows VALUES (?,?,?,?,?)',
                ((r['series'],r['day'],r['close'],r['spot_buy'],r['spot_sell']) for r in b.rows))
        value=sha(dict(rows=market_rows(store),sources=sources))
        store.db.execute('INSERT INTO d_hold_market_snapshot VALUES (4,?,?,?)',(now_string(),value,canonical(sources)))


def verify_market(store):
    r=store.db.execute('SELECT * FROM d_hold_market_snapshot WHERE experiment_id=4').fetchone()
    if r is None or r['digest']!=sha(dict(rows=market_rows(store),sources=json.loads(r['sources_json']))):
        raise DataError('holdout_market_changed')
    return dict(r)
