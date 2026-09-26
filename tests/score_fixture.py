import json

from back.experiment import load_plan
from back.replay import Replay
from tests.experiment_fixture import frozen_experiment, seed_experiment


def scored_fixture(store, *, populate_holdout=False):
    days = seed_experiment(store)
    row = frozen_experiment(store, days, end=days[30])
    seed_split(store, row, 'dev')
    if populate_holdout:
        seed_split(store, row, 'holdout')
    return row, days


def seed_split(store, row, split):
    engine = Replay(store, load_plan(store, row['id']))
    points = [(point, engine.outcome(point)) for point in
              (engine.prepare(t) for t in engine.candidates(split))]
    with store.transaction():
        for method in ('jev', 'always_flat', 'majority', 'momentum', 'reversal'):
            run_id = store.db.execute('''INSERT INTO runs
                (experiment_id,method,split,started_at,status,n_ok) VALUES (?,?,?,'fixture','complete',?)''',
                (row['id'], method, split, len(points))).lastrowid
            store.db.execute('INSERT INTO run_scopes VALUES (?,1)', (run_id,))
            for point, outcome in points:
                probs = {'up': .2, 'flat': .6, 'down': .2} if method == 'jev' else {'up': 0, 'flat': 1, 'down': 0}
                store.db.execute('INSERT INTO predictions VALUES (?,?,?,?,?,?,?,?,?,?)',
                    (row['id'], method, point.t.isoformat(), run_id, point.input_hash, point.input_json,
                     'flat', json.dumps(probs), row['model'] if method == 'jev' else None, 'fixture'))
        store.db.executemany('INSERT INTO outcomes VALUES (?,?,?,?,?)',
            ((row['id'], point.t.isoformat(), point.close_t, outcome.close_end, outcome.label)
             for point, outcome in points if outcome.scorable))
    return [point.t.isoformat() for point, _ in points]


def change_answers(store, method, choice, start, end, *, probabilities=None):
    probabilities = probabilities or {k: int(k == choice) for k in ('up', 'flat', 'down')}
    with store.transaction():
        store.db.execute('''UPDATE predictions SET answer=?,probs_json=?
            WHERE method=? AND substr(t,1,10) BETWEEN ? AND ?''',
            (choice, json.dumps(probabilities), method, start, end))
