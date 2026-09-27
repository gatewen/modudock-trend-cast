"""Immutable experiment 4 in its own namespace; hashes may read raw holdout data,
never compute holdout features/returns/labels. All writes are development-only.
"""
import hashlib
import json

from .daily_config import settings
from .daily_replay import DailyReplay, development_thresholds, DEV_END, HOLD_START, HOLD_END
from .data import DataError, day_value
from .experiment import canonical
from .store import now_string

SOURCE_TABLES=('d_calendar','d_bars','d_corp_events','d_corp_coverage','d_institutional','d_margin','d_fetch_log')
SCHEMA='''
CREATE TABLE IF NOT EXISTS d_experiments(
 id INTEGER PRIMARY KEY,created_at TEXT NOT NULL,config_json TEXT NOT NULL,
 data_digest TEXT NOT NULL,dev_digest TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS d_features(
 experiment_id INTEGER NOT NULL,day TEXT NOT NULL,input_hash TEXT NOT NULL,input_json TEXT NOT NULL,
 PRIMARY KEY(experiment_id,day));
CREATE TABLE IF NOT EXISTS d_predictions(
 experiment_id INTEGER NOT NULL,H INTEGER NOT NULL CHECK(H IN (3,7,14)),
 method TEXT NOT NULL,day TEXT NOT NULL,choice TEXT NOT NULL CHECK(choice IN ('up','flat','down')),
 probabilities_json TEXT NOT NULL,state TEXT,input_hash TEXT NOT NULL,
 PRIMARY KEY(experiment_id,H,method,day));
CREATE TABLE IF NOT EXISTS d_outcomes(
 experiment_id INTEGER NOT NULL,H INTEGER NOT NULL CHECK(H IN (3,7,14)),day TEXT NOT NULL,
 end_day TEXT NOT NULL,label TEXT NOT NULL CHECK(label IN ('up','flat','down')),
 return_numerator TEXT NOT NULL,return_denominator TEXT NOT NULL,
 PRIMARY KEY(experiment_id,H,day));
CREATE TABLE IF NOT EXISTS d_fits(
 experiment_id INTEGER NOT NULL,H INTEGER NOT NULL,day TEXT NOT NULL,model_json TEXT NOT NULL,
 PRIMARY KEY(experiment_id,H,day));
CREATE TRIGGER IF NOT EXISTS d_experiments_no_update BEFORE UPDATE ON d_experiments
 BEGIN SELECT RAISE(ABORT,'daily_experiment_immutable'); END;
CREATE TRIGGER IF NOT EXISTS d_experiments_no_delete BEFORE DELETE ON d_experiments
 BEGIN SELECT RAISE(ABORT,'daily_experiment_immutable'); END;
'''


def ensure_schema(store):
    db=store.db
    db.executescript(SCHEMA)
    for table in SOURCE_TABLES:
        for operation in ('INSERT','UPDATE','DELETE'):
            prefixes=('OLD','NEW') if operation=='UPDATE' else ('OLD',) if operation=='DELETE' else ('NEW',)
            matches=[]
            for p in prefixes:
                match=(f"{p}.start_day<=json_extract(e.config_json,'$.hold_end') AND {p}.end_day>=json_extract(e.config_json,'$.start')"
                       if table in ('d_corp_coverage','d_fetch_log') else
                       f"{p}.day BETWEEN json_extract(e.config_json,'$.start') AND json_extract(e.config_json,'$.hold_end')")
                if table!='d_calendar': match+=f" AND {p}.symbol=json_extract(e.config_json,'$.symbol')"
                matches.append('('+match+')')
            db.execute(f'''CREATE TRIGGER IF NOT EXISTS {table}_freeze_{operation.lower()}
                BEFORE {operation} ON {table} WHEN EXISTS(SELECT 1 FROM d_experiments e WHERE {' OR '.join(matches)})
                BEGIN SELECT RAISE(ABORT,'daily_data_frozen'); END''')
    for table in ('d_features','d_predictions','d_outcomes','d_fits'):
        for operation in ('INSERT','UPDATE'):
            endpoint=" AND NEW.end_day<=json_extract(e.config_json,'$.dev_end') AND NEW.end_day>=NEW.day" if table=='d_outcomes' else ''
            db.execute(f'''CREATE TRIGGER IF NOT EXISTS {table}_dev_{operation.lower()}
                BEFORE {operation} ON {table} WHEN NOT EXISTS(SELECT 1 FROM d_experiments e
                  WHERE e.id=NEW.experiment_id AND NEW.day BETWEEN json_extract(e.config_json,'$.dev_start')
                    AND json_extract(e.config_json,'$.dev_end'){endpoint})
                BEGIN SELECT RAISE(ABORT,'daily_results_dev_only'); END''')
    db.commit()


def source_summary(store, config, end):
    """Canonical raw source hashes, including FinMind coverage/provenance.

    Overlapping range metadata is clipped to the frozen interval. Fetch timestamps
    and rows after that interval are not research inputs.
    """
    start=config['start'];symbol=config['symbol'];summary={}
    for table in SOURCE_TABLES:
        if table in ('d_corp_coverage','d_fetch_log'):
            rows=store.db.execute(f'SELECT * FROM {table} WHERE symbol=? AND start_day<=? AND end_day>=?',(symbol,end,start))
        elif table=='d_calendar':
            rows=store.db.execute('SELECT * FROM d_calendar WHERE day BETWEEN ? AND ?',(start,end))
        else:
            rows=store.db.execute(f'SELECT * FROM {table} WHERE symbol=? AND day BETWEEN ? AND ?',(symbol,start,end))
        values=[];days=[]
        for row in rows:
            value=dict(row);value.pop('fetched_at',None)
            if 'day' in value: days.append(value['day'])
            if 'start_day' in value:
                value['start_day']=max(value['start_day'],start);value['end_day']=min(value['end_day'],end)
                # Whole-response row count may include post-freeze data.
                value.pop('n_rows',None)
            values.append(canonical(value))
        values.sort()
        summary[table]=dict(rows=len(values),sha256=hashlib.sha256('\n'.join(values).encode()).hexdigest())
        if days: summary[table].update(first_day=min(days),last_day=max(days))
    return summary


def digest(config, summary):
    return hashlib.sha256(canonical(dict(config=config,source=summary)).encode()).hexdigest()


def create_experiment(store, *, experiment_id=4, start='2010-01-04'):
    if type(experiment_id) is not int or experiment_id!=4: raise DataError('daily_experiment_4_only')
    day_value(start)
    if start>'2010-04-01': raise DataError('daily_missing_warmup')
    ensure_schema(store)
    with store.transaction():
        if store.db.execute('SELECT 1 FROM d_experiments WHERE id=?',(experiment_id,)).fetchone():
            raise DataError('daily_experiment_exists')
        config=settings(start)
        engine=DailyReplay(store,start=start)
        days=engine.calendar(start,HOLD_END)
        bar_days=tuple(r[0] for r in store.db.execute('SELECT day FROM d_bars WHERE symbol=? AND day BETWEEN ? AND ? ORDER BY day',('2330',start,HOLD_END)))
        if len(days)<61 or days[0]!=start: raise DataError('daily_missing_warmup')
        if days!=bar_days: raise DataError('daily_missing_calendar_or_bar')
        if not any(d>=HOLD_START for d in days): raise DataError('daily_incomplete_frozen_range')
        calendar_coverage=store.db.execute('''SELECT 1 FROM d_fetch_log WHERE kind='calendar' AND symbol='2330'
            AND start_day<=? AND end_day>=? AND status='written' ''',(start,HOLD_END)).fetchone()
        if calendar_coverage is None: raise DataError('daily_unconfirmed_calendar')
        if any(store.corp_state(d)['state']=='unknown' for d in days): raise DataError('daily_unknown_corporate_action')
        thresholds=development_thresholds(engine)
        config['thresholds']={str(h):dict(numerator=r['k_numerator'],denominator=r['k_denominator']) for h,r in thresholds.items()}
        config['warmup_end']=days[59];config['first_prediction']=days[60]
        full=source_summary(store,config,HOLD_END)
        config['finmind_summary']={name:full[name] for name in ('d_calendar','d_institutional','d_margin')}
        overall=digest(config,full)
        dev=digest(config,source_summary(store,config,DEV_END))
        store.db.execute('INSERT INTO d_experiments VALUES (?,?,?,?,?)',(experiment_id,now_string(),canonical(config),overall,dev))
    return load_experiment(store,experiment_id)


def load_experiment(store, experiment_id=4, *, verify=True):
    if type(experiment_id) is not int or experiment_id!=4: raise DataError('daily_experiment_4_only')
    row=store.db.execute('SELECT * FROM d_experiments WHERE id=?',(experiment_id,)).fetchone()
    if row is None: raise DataError('daily_experiment_missing')
    config=json.loads(row['config_json'])
    expected=settings(config['start'])
    if any(canonical(config.get(key))!=canonical(value) for key,value in expected.items()):
        raise DataError('daily_frozen_settings_changed')
    if verify and digest(config,source_summary(store,config,DEV_END))!=row['dev_digest']:
        raise DataError('daily_source_digest_changed')
    return dict(row,config=config)
