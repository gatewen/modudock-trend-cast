"""Shared reveal log with explicit intraday/daily parent namespaces."""
from .data import DataError


def table_exists(store,name):
    return store.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(name,)).fetchone() is not None


def ensure_registry(store):
    if not table_exists(store,'reveals'):raise DataError('holdout_missing_reveal_log')
    if 'namespace' in {r[1] for r in store.db.execute('PRAGMA table_info(reveals)')}:return
    # Preserve every existing row and its foreign-key protection. Daily experiment 4
    # is not a fabricated 30-minute experiment, and foreign keys stay enabled.
    if 'segment' not in {r[1] for r in store.db.execute('PRAGMA table_info(reveals)')}:
        store.db.execute("ALTER TABLE reveals ADD COLUMN segment TEXT NOT NULL DEFAULT 'holdout'")
    with store.transaction():
        store.db.execute('CREATE TABLE reveal_experiments(namespace TEXT NOT NULL,id INTEGER NOT NULL,PRIMARY KEY(namespace,id))')
        for namespace,table in (('intraday','experiments'),('daily','d_experiments')):
            store.db.execute(f"INSERT INTO reveal_experiments SELECT ?,id FROM {table}",(namespace,))
            store.db.execute(f'''CREATE TRIGGER {table}_reveal_registry_insert AFTER INSERT ON {table}
                BEGIN INSERT INTO reveal_experiments VALUES ('{namespace}',NEW.id); END''')
            store.db.execute(f'''CREATE TRIGGER {table}_reveal_registry_delete AFTER DELETE ON {table}
                BEGIN DELETE FROM reveal_experiments WHERE namespace='{namespace}' AND id=OLD.id; END''')
            store.db.execute(f'''CREATE TRIGGER {table}_reveal_registry_update AFTER UPDATE OF id ON {table}
                BEGIN UPDATE reveal_experiments SET id=NEW.id WHERE namespace='{namespace}' AND id=OLD.id; END''')
        store.db.execute('''CREATE TABLE reveals_new(experiment_id INTEGER NOT NULL,revealed_at TEXT NOT NULL,
            first_day TEXT NOT NULL,last_day TEXT NOT NULL,what TEXT NOT NULL,segment TEXT NOT NULL DEFAULT 'holdout',
            namespace TEXT NOT NULL DEFAULT 'intraday',FOREIGN KEY(namespace,experiment_id) REFERENCES reveal_experiments(namespace,id))''')
        store.db.execute("INSERT INTO reveals_new SELECT experiment_id,revealed_at,first_day,last_day,what,segment,'intraday' FROM reveals")
        store.db.execute('DROP TABLE reveals')
        store.db.execute('ALTER TABLE reveals_new RENAME TO reveals')


def revealed(store):
    if not table_exists(store,'d_hold_seal') or not table_exists(store,'reveals'):return False
    if 'namespace' not in {r[1] for r in store.db.execute('PRAGMA table_info(reveals)')}:return False
    return bool(store.db.execute('''SELECT 1 FROM reveals r JOIN d_hold_seal s ON s.experiment_id=r.experiment_id
        WHERE r.namespace='daily' AND r.experiment_id=4 AND r.what='all' AND r.segment='holdout'
        AND r.first_day='2022-01-03' AND r.last_day='2024-07-25' ''').fetchone())


def require_revealed(store):
    if not revealed(store):raise DataError('daily_holdout_locked')


def holdout_status(store):
    if revealed(store):return dict(state='used',message='已使用（一次性保留段考試已揭露）')
    return dict(state='unused',message='未使用（沒有入圍者，保留給未來）')
