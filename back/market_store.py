"""Separate immutable market snapshot; experiment 4's original digest is untouched."""
import hashlib
import json

from .daily_experiment import load_experiment
from .daily_replay import DEV_START, DEV_END, HOLD_END
from .data import DataError
from .experiment import canonical
from .market_sources import SOURCES, START, bounds, positive
from .store import now_string

SCHEMA='''
CREATE TABLE IF NOT EXISTS market_rows(
 series TEXT NOT NULL CHECK(series IN ('taiex','tsm','sox','usd_twd')),
 day TEXT NOT NULL CHECK(day>='2009-01-01' AND day<='2021-12-31'),
 close TEXT,spot_buy TEXT,spot_sell TEXT,PRIMARY KEY(series,day));
CREATE TABLE IF NOT EXISTS market_fetches(
 series TEXT PRIMARY KEY,start_day TEXT NOT NULL,end_day TEXT NOT NULL,
 fetched_at TEXT NOT NULL,n_rows INTEGER NOT NULL,payload_hash TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS market_experiments(
 experiment_id INTEGER PRIMARY KEY CHECK(experiment_id=4),created_at TEXT NOT NULL,
 original_data_digest TEXT NOT NULL,original_dev_digest TEXT NOT NULL,
 market_digest TEXT NOT NULL,config_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS market_features(
 experiment_id INTEGER NOT NULL,day TEXT NOT NULL,original_hash TEXT NOT NULL,
 input_hash TEXT NOT NULL,input_json TEXT NOT NULL,alignment_json TEXT NOT NULL,
 PRIMARY KEY(experiment_id,day));
CREATE TABLE IF NOT EXISTS market_predictions(
 experiment_id INTEGER NOT NULL,H INTEGER NOT NULL CHECK(H IN (3,7,14)),
 method TEXT NOT NULL,day TEXT NOT NULL,choice TEXT NOT NULL,
 probabilities_json TEXT NOT NULL,state TEXT,input_hash TEXT NOT NULL,
 PRIMARY KEY(experiment_id,H,method,day));
CREATE TABLE IF NOT EXISTS market_fits(
 experiment_id INTEGER NOT NULL,H INTEGER NOT NULL,day TEXT NOT NULL,model_json TEXT NOT NULL,
 PRIMARY KEY(experiment_id,H,day));
'''


def ensure_schema(store):
    store.db.executescript(SCHEMA)
    store.db.execute('''CREATE TRIGGER IF NOT EXISTS market_experiments_no_replace
        BEFORE INSERT ON market_experiments WHEN EXISTS(SELECT 1 FROM market_experiments)
        BEGIN SELECT RAISE(ABORT,'market_snapshot_immutable'); END''')
    for operation in ('UPDATE','DELETE'):
        store.db.execute(f'''CREATE TRIGGER IF NOT EXISTS market_experiments_no_{operation.lower()}
            BEFORE {operation} ON market_experiments BEGIN SELECT RAISE(ABORT,'market_snapshot_immutable'); END''')
    for table in ('market_rows','market_fetches'):
        for operation in ('INSERT','UPDATE','DELETE'):
            # Includes OLD and NEW for moves into/out of the protected interval.
            prefixes=('OLD','NEW') if operation=='UPDATE' else ('OLD',) if operation=='DELETE' else ('NEW',)
            matches=[]
            for p in prefixes:
                matches.append(f"{p}.day BETWEEN json_extract(e.config_json,'$.start') AND json_extract(e.config_json,'$.protected_end')" if table=='market_rows' else
                               f"{p}.start_day<=json_extract(e.config_json,'$.protected_end') AND {p}.end_day>=json_extract(e.config_json,'$.start')")
            store.db.execute(f'''CREATE TRIGGER IF NOT EXISTS {table}_frozen_{operation.lower()}
                BEFORE {operation} ON {table} WHEN EXISTS(SELECT 1 FROM market_experiments e WHERE {' OR '.join(matches)})
                BEGIN SELECT RAISE(ABORT,'market_data_frozen'); END''')
    for table in ('market_features','market_predictions','market_fits'):
        for operation in ('INSERT','UPDATE'):
            store.db.execute(f'''CREATE TRIGGER IF NOT EXISTS {table}_dev_{operation.lower()}
                BEFORE {operation} ON {table} WHEN NOT EXISTS(SELECT 1 FROM market_experiments e
                WHERE e.experiment_id=NEW.experiment_id AND NEW.day BETWEEN
                json_extract(e.config_json,'$.dev_start') AND json_extract(e.config_json,'$.dev_end'))
                BEGIN SELECT RAISE(ABORT,'market_results_dev_only'); END''')
    store.db.commit()


def write_batches(store,batches):
    batches=tuple(batches)
    if {b.series for b in batches}!=set(SOURCES) or len(batches)!=4:
        raise DataError('market_requires_four_sources')
    for b in batches:
        bounds(b.start,b.end)
        if (b.start,b.end)!=(START,DEV_END) or not b.rows: raise DataError('market_incomplete_source')
        seen=set()
        for r in b.rows:
            if r['series']!=b.series or not b.start<=r['day']<=b.end or r['day'] in seen:
                raise DataError('market_noncanonical_batch')
            seen.add(r['day'])
            if any(r[k] is not None and positive(r[k])!=r[k] for k in ('close','spot_buy','spot_sell')):
                raise DataError('market_noncanonical_batch')
    with store.transaction():
        if store.db.execute('SELECT 1 FROM market_experiments').fetchone(): raise DataError('market_data_frozen')
        for b in batches:
            store.db.execute('DELETE FROM market_rows WHERE series=?',(b.series,))
            store.db.executemany('INSERT INTO market_rows VALUES (?,?,?,?,?)',
                ((r['series'],r['day'],r['close'],r['spot_buy'],r['spot_sell']) for r in b.rows))
            store.db.execute('INSERT OR REPLACE INTO market_fetches VALUES (?,?,?,?,?,?)',
                             (b.series,b.start,b.end,now_string(),len(b.rows),b.payload_hash))


def source_summary(store):
    result={}
    for series in SOURCES:
        rows=[dict(r) for r in store.db.execute('SELECT * FROM market_rows WHERE series=? AND day BETWEEN ? AND ? ORDER BY day',
                                               (series,START,DEV_END))]
        f=store.db.execute('SELECT * FROM market_fetches WHERE series=?',(series,)).fetchone()
        if not f or (f['start_day'],f['end_day'])!=(START,DEV_END) or f['n_rows']!=len(rows) or not rows:
            raise DataError('market_incomplete_source')
        result[series]=dict(rows=len(rows),first=rows[0]['day'],last=rows[-1]['day'],
                           sha256=hashlib.sha256(canonical(rows).encode()).hexdigest(),payload_hash=f['payload_hash'])
    return result


def snapshot_digest(config,sources):
    return hashlib.sha256(canonical(dict(config=config,sources=sources)).encode()).hexdigest()


def freeze(store,protocol):
    ensure_schema(store)
    with store.transaction():
        original=load_experiment(store)
        if store.db.execute('SELECT 1 FROM market_experiments').fetchone(): raise DataError('market_snapshot_exists')
        config=dict(start=START,dev_start=DEV_START,dev_end=DEV_END,protected_end=HOLD_END,
                    stored_end=DEV_END,protocol=protocol)
        sources=source_summary(store)
        digest=snapshot_digest(config,sources)
        store.db.execute('INSERT INTO market_experiments VALUES (4,?,?,?,?,?)',
                         (now_string(),original['data_digest'],original['dev_digest'],digest,canonical(config)))
    return load_snapshot(store,protocol)


def load_snapshot(store,protocol):
    original=load_experiment(store)
    row=store.db.execute('SELECT * FROM market_experiments WHERE experiment_id=4').fetchone()
    if row is None: raise DataError('market_snapshot_missing')
    config=json.loads(row['config_json'])
    expected=dict(start=START,dev_start=DEV_START,dev_end=DEV_END,protected_end=HOLD_END,stored_end=DEV_END,protocol=protocol)
    if canonical(config)!=canonical(expected): raise DataError('market_policy_changed')
    if row['original_data_digest']!=original['data_digest'] or row['original_dev_digest']!=original['dev_digest']:
        raise DataError('market_original_experiment_changed')
    if row['market_digest']!=snapshot_digest(config,source_summary(store)):
        raise DataError('market_source_digest_changed')
    return dict(row,config=config,original=original)
