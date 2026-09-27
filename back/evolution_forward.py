"""Frozen development vol_prior for forward inference; no forward outcomes read."""
from bisect import bisect_left
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
import json

from .data import DataError, TAIPEI
from .evolution import MIN_SAMPLES, WalkForward, smoothed, volatility
from .evolution_run import ForbiddenClient, read_development
from .experiment import canonical
from .forward import active_days, plan_for, selected
from .replay import LABELS, Replay
from .score import percentile
from .store import now_string

METHOD = 'vol_prior'


@dataclass(frozen=True)
class FrozenVolPrior:
    cuts: tuple
    groups: tuple
    majority: tuple

    @classmethod
    def fit(cls, development):
        plan = development.engine.plan
        # Includes the last development point's outcome at exactly 13:30.
        cutoff = datetime.fromisoformat(plan.dev_end + 'T13:30:00').replace(tzinfo=TAIPEI)
        from types import SimpleNamespace
        records = WalkForward(development.engine, development.records).matured(SimpleNamespace(t=cutoff))
        counts = Counter(r.observation.label for r in records)
        samples = [r for r in records if r.volatility is not None]
        values = [r.volatility for r in samples]
        cuts = (percentile(values, 1/3), percentile(values, 2/3)) if values else ()
        groups = [Counter() for _ in range(3)]
        for record in samples:
            groups[bisect_left(cuts, record.volatility)][record.observation.label] += 1
        return cls(cuts, tuple(tuple(g[label] for label in LABELS) for g in groups),
                   tuple(counts[label] for label in LABELS))

    def serialize(self):
        return canonical(dict(version='vol-forward-1', cuts=self.cuts, groups=self.groups,
            majority=self.majority, labels=LABELS, min_samples=MIN_SAMPLES,
            volatility='30_closes_29_simple_returns_population_std',
            quantiles='linear_1/3_2/3_ties_lower', tie_order=['flat','up','down']))

    def predict(self, point):
        from .baselines import Prediction
        if not point.predictable:
            return Prediction(METHOD, None, None, 'not_predictable')
        current = volatility(point)
        counts = self.majority
        if current is not None and self.cuts:
            group = self.groups[bisect_left(self.cuts, current)]
            if sum(group) >= MIN_SAMPLES:
                counts = group
        return smoothed(METHOD, Counter(dict(zip(LABELS, counts))))


def frozen_model(store, *, create=False):
    selected(store, 1)
    development = read_development(store, 1, 'dev')
    model = FrozenVolPrior.fit(development)
    saved = store.db.execute("SELECT * FROM evolution_models WHERE experiment_id=1 AND method='vol_prior'").fetchone()
    if saved is None:
        if not create:
            raise DataError('forward_incomplete')
        store.db.execute('INSERT INTO evolution_models VALUES (?,?,?,?,?)',
            (1, METHOD, development.source_digest, model.serialize(), now_string()))
    elif (saved['source_digest'] != development.source_digest or saved['parameters_json'] != model.serialize()):
        raise DataError('evolution_frozen_changed')
    return model


def run_forward_vol(store, *, experiment_id=1, client=None, cancel=None):
    selected(store, experiment_id)
    client = client or ForbiddenClient()
    def check():
        if cancel is not None and cancel.is_set():
            raise DataError('cancelled')
    with client.guard(), store.transaction():
        check()
        model = frozen_model(store, create=True)
        engine = Replay(store, plan_for(store, experiment_id, verify=True))
        pending, points, predictable = [], 0, 0
        for t in engine.candidates('forward'):
            check()
            point = engine.prepare(t)
            points += 1
            if not point.predictable:
                continue
            predictable += 1
            answer = model.predict(point)
            saved = store.db.execute('''SELECT input_hash,input_json,answer,probs_json,model_reported FROM predictions
                WHERE experiment_id=1 AND method='vol_prior' AND t=?''', (t.isoformat(),)).fetchone()
            if saved is not None:
                if (saved['input_hash'], saved['input_json'], saved['answer'], json.loads(saved['probs_json']), saved['model_reported']) != (
                        point.input_hash, point.input_json, answer.answer, answer.probabilities, None):
                    raise DataError('evolution_prediction_conflict')
                continue
            pending.append((t.isoformat(), point.input_hash, point.input_json, answer.answer, canonical(answer.probabilities)))
        check()
        latest = store.db.execute("SELECT status FROM runs WHERE experiment_id=1 AND method='vol_prior' AND split='forward' ORDER BY id DESC LIMIT 1").fetchone()
        if pending or latest is None or latest['status'] != 'complete':
            created = now_string()
            run = store.db.execute('''INSERT INTO runs
                (experiment_id,method,split,started_at,finished_at,status,n_ok,n_fail)
                VALUES (1,'vol_prior','forward',?,?,'complete',?,0)''', (created, created, len(pending))).lastrowid
            store.db.execute('INSERT INTO run_scopes VALUES (?,1)', (run,))
            store.db.executemany('INSERT INTO predictions VALUES (?,?,?,?,?,?,?,?,?,?)',
                ((1, METHOD, t, run, digest, state, answer, probs, None, created)
                 for t, digest, state, answer, probs in pending))
        check()
        return dict(points=points, predictable=predictable, missing_answers=0, http_calls=client.calls)


def progress_metadata(store, experiment_id):
    """Explicitly authorized execution metadata; never read answers or outcomes."""
    from .forward import FORWARD_METHODS, revealed_days
    days = active_days(store, experiment_id)
    per_point = {}
    for day in days:
        for row in store.db.execute('''SELECT t,method FROM predictions WHERE experiment_id=?
                AND substr(t,1,10)=?''', (experiment_id, day)):
            if row['method'] in FORWARD_METHODS:
                per_point.setdefault(row['t'], set()).add(row['method'])
    return dict(participants=list(FORWARD_METHODS),
                unrevealed_days=len(set(days) - set(revealed_days(store, experiment_id))),
                run_points=sum(set(FORWARD_METHODS) <= values for values in per_point.values()))
