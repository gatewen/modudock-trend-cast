"""Atomic development-only persistence and common-cohort evaluation for round 2.

No call to load_plan/verify_digest: their full frozen range includes holdout.
Only calendar/source rows through dev_end are read. Split endpoints outside
development are inert experiment metadata used to construct ReplayPlan.
"""
from contextlib import contextmanager, ExitStack
from dataclasses import dataclass, field
import hashlib
import json
import math
import socket
from unittest.mock import patch

from .baselines import METHODS as BASELINES
from .data import DataError, MAPPING
from .evolution import LearningPoint, METHODS, WalkForward, volatility
from .experiment import MODEL, canonical, experiment_row
from .jevcast import JevClient
from .replay import Replay, ReplayPlan, observation
from .score import ScoredPoint, metrics, paired_bootstrap, prediction_answer, INCOMPLETE
from .store import now_string

OLD_METHODS = ('jev', *BASELINES)
ALL_METHODS = (*OLD_METHODS, *METHODS)


class ForbiddenClient:
    """Fail closed at client, HTTP transport, DNS and socket connection layers."""
    def __init__(self):
        self.calls = 0

    def predict(self, *args, **kwargs):
        self.calls += 1
        raise DataError('network_forbidden')

    @contextmanager
    def guard(self):
        with ExitStack() as stack:
            for owner, name in ((JevClient, 'predict'), (JevClient, '_request'),
                                (socket, 'create_connection'), (socket, 'getaddrinfo'),
                                (socket.socket, 'connect'), (socket.socket, 'connect_ex')):
                stack.enter_context(patch.object(owner, name, self.predict))
            yield self
            if self.calls:
                raise DataError('network_forbidden')


@dataclass
class Development:
    row: dict = field(repr=False)
    engine: Replay = field(repr=False)
    points: tuple = field(repr=False)
    records: tuple = field(repr=False)
    stored: dict = field(repr=False)
    scored: dict = field(repr=False)
    source_digest: str
    eligible: int
    source_ready: bool


def read_development(store, experiment_id=1, split='dev'):
    if type(experiment_id) is not int or experiment_id != 1 or split != 'dev':
        raise DataError('evolution_dev_only')
    row = experiment_row(store, experiment_id)
    config = json.loads(row['config_json'])
    if (row['prompt_version'] != 'p1' or row['feature_version'] != 'f1' or row['model'] != MODEL
            or row['threshold_permille'] != 3 or config.get('symbol') != '2330'
            or config.get('mapping') != MAPPING):
        raise DataError('evolution_settings_changed')
    calendar = tuple(r[0] for r in store.db.execute('''SELECT day FROM daily
        WHERE symbol=? AND day BETWEEN ? AND ? ORDER BY day''',
        (config['symbol'], config['warmup_start'], row['dev_end'])))
    # These two metadata sentinels are never visited by a replay or source query.
    sentinels = tuple(sorted({row['hold_start'], row['hold_end']}))
    engine = Replay(store, ReplayPlan.from_experiment(row, calendar + sentinels))
    args = (experiment_id, row['dev_start'], row['dev_end'])
    stored = {m: {} for m in ALL_METHODS}
    for r in store.db.execute('''SELECT * FROM predictions WHERE experiment_id=?
            AND substr(t,1,10) BETWEEN ? AND ? ORDER BY method,t''', args):
        if r['method'] in stored:
            stored[r['method']][r['t']] = dict(r)
    outcomes = {r['t']: r for r in store.db.execute('''SELECT * FROM outcomes WHERE experiment_id=?
        AND substr(t,1,10) BETWEEN ? AND ? ORDER BY t''', args)}
    digest = hashlib.sha256(canonical(row).encode())
    for table in ('bars', 'daily', 'corp_events', 'corp_coverage'):
        where = 'start_day<=? AND end_day>=?' if table == 'corp_coverage' else 'day<=? AND day>=?'
        order = 'start_day,end_day' if table == 'corp_coverage' else 'day,bar_end' if table == 'bars' else 'day'
        digest.update(table.encode())
        for r in store.db.execute(f'SELECT * FROM {table} WHERE symbol=? AND {where} ORDER BY {order}',
                (config['symbol'], row['dev_end'], config['warmup_start'])):
            digest.update((canonical(dict(r)) + '\n').encode())
    scored = {m: {} for m in ALL_METHODS}
    points, records = [], []
    eligible = 0
    for t in engine.candidates('dev'):
        if engine.plan.split_of(t.date().isoformat()) != 'dev':
            raise DataError('evolution_dev_only')
        point = engine.prepare(t)
        outcome = engine.outcome(point)
        stamp = t.isoformat()
        old = outcomes.get(stamp)
        if outcome.scorable:
            if old is None or (old['close_t'], old['close_end'], old['label']) != (point.close_t, outcome.close_end, outcome.label):
                raise DataError('development_outcome_mismatch')
            digest.update(canonical(dict(old)).encode())
        elif old is not None:
            raise DataError('development_outcome_mismatch')
        answers = {}
        if point.predictable:
            for method in OLD_METHODS:
                saved = stored[method].get(stamp)
                if saved is None:
                    continue
                answer = prediction_answer(saved, point, row['model'])
                answers[method] = answer
                digest.update(canonical(saved).encode())
                if outcome.scorable:
                    scored[method][stamp] = ScoredPoint(stamp, outcome.label, answer.choice, answer.probabilities)
        eligible += int(point.predictable and outcome.scorable)
        current_jev = answers['jev'].choice if 'jev' in answers else None
        records.append(LearningPoint(observation(point, outcome), volatility(point), current_jev))
        points.append((point, outcome, current_jev))
    ready = True
    for method in OLD_METHODS:
        last = store.db.execute('''SELECT status FROM runs WHERE experiment_id=? AND method=?
            AND split='dev' ORDER BY id DESC LIMIT 1''', (experiment_id, method)).fetchone()
        ready = ready and last is not None and last['status'] == 'complete'
    return Development(row, engine, tuple(points), tuple(records), stored, scored, digest.hexdigest(), eligible, ready)


def comparison_report(scored, eligible, *, complete=True):
    common = set.intersection(*(set(scored[m]) for m in ALL_METHODS))
    common = tuple(sorted(common))
    paired = {m: {t: scored[m][t] for t in common} for m in ALL_METHODS}
    summaries = {m: metrics(paired[m].values()) for m in ('majority', *METHODS)}
    enough = complete and eligible > 0 and len(common) * 100 >= eligible * 95
    comparisons = {}
    for method in METHODS:
        bootstrap = paired_bootstrap(paired[method], paired['majority'])
        delta = math.fsum(paired[method][t].brier - paired['majority'][t].brier for t in common) / len(common) if common else None
        ci = bootstrap['brier_difference_ci95']
        promoted = bool(enough and ci is not None and ci[1] < 0)
        comparisons[method] = dict(brier_difference=delta, bootstrap=bootstrap,
            statement=INCOMPLETE if not enough else '值得前瞻驗證' if promoted else '沒有改善',
            qualifies=promoted)
    qualified = [m for m in METHODS if comparisons[m]['qualifies']]
    winner = min(qualified, key=lambda m: summaries[m]['brier']) if qualified else None
    return dict(n=len(common), eligible_n=eligible, coverage=len(common) / eligible if eligible else None,
        baseline='majority', methods=summaries, comparisons=comparisons, promoted=winner,
        selection='lowest_development_intersection_brier_among_qualifying_methods')


def run_development(store, *, experiment_id=1, split='dev', client=None):
    """One transaction: immutable existing answers, all three new methods or none."""
    client = client or ForbiddenClient()
    with client.guard(), store.transaction():
        data = read_development(store, experiment_id, split)
        learner = WalkForward(data.engine, data.records)
        pending = {m: [] for m in METHODS}
        failures = {m: 0 for m in METHODS}
        for point, outcome, current_jev in data.points:
            if not point.predictable:
                continue
            for method in METHODS:
                prediction = learner.predict(method, point, jev_choice=current_jev)
                stamp = point.t.isoformat()
                saved = data.stored[method].get(stamp)
                if prediction.answer is None:
                    failures[method] += 1
                    if saved is not None:
                        raise DataError('evolution_prediction_conflict')
                    continue
                probs = canonical(prediction.probabilities)
                if saved is not None:
                    if (saved['input_hash'], saved['input_json'], saved['answer'], json.loads(saved['probs_json']), saved['model_reported']) != (
                            point.input_hash, point.input_json, prediction.answer, prediction.probabilities, None):
                        raise DataError('evolution_prediction_conflict')
                else:
                    pending[method].append((stamp, point.input_hash, point.input_json, prediction.answer, probs))
                if outcome.scorable:
                    data.scored[method][stamp] = ScoredPoint(stamp, outcome.label, prediction.answer, prediction.probabilities)
        for method in METHODS:
            if not pending[method]:
                continue
            created = now_string()
            cursor = store.db.execute('''INSERT INTO runs
                (experiment_id,method,split,started_at,finished_at,status,n_ok,n_fail)
                VALUES (?,?,'dev',?,?,'complete',?,?)''',
                (experiment_id, method, created, created, len(pending[method]), failures[method]))
            run_id = cursor.lastrowid
            store.db.execute('INSERT INTO run_scopes VALUES (?,1)', (run_id,))
            store.db.executemany('INSERT INTO predictions VALUES (?,?,?,?,?,?,?,?,?,?)',
                ((experiment_id, method, stamp, run_id, digest, state, answer, probs, None, created)
                 for stamp, digest, state, answer, probs in pending[method]))
        report = comparison_report(data.scored, data.eligible, complete=data.source_ready and not any(failures.values()))
        return dict(experiment_id=experiment_id, split='dev', first_day=data.row['dev_start'],
            last_day=data.row['dev_end'], source_digest=data.source_digest,
            http_calls=client.calls, new_predictions={m: len(pending[m]) for m in METHODS},
            missing_predictions=failures, comparison=report)


def development_view(store, experiment_id):
    """Read only committed development forecasts; never retrain or insert."""
    if experiment_id != 1:
        return {'state': 'unavailable', 'message': '進化方法只屬於實驗 1'}
    row = experiment_row(store, experiment_id)
    exists = store.db.execute('''SELECT 1 FROM predictions WHERE experiment_id=1
        AND method IN ('clock_prior','vol_prior','jev_calibrated')
        AND substr(t,1,10) BETWEEN ? AND ? LIMIT 1''', (row['dev_start'],row['dev_end'])).fetchone()
    if not exists:
        return {'state': 'unavailable', 'message': '尚無進化新方法開發段結果'}
    data = read_development(store, experiment_id)
    for point, outcome, _ in data.points:
        if not point.predictable or not outcome.scorable:
            continue
        for method in METHODS:
            saved = data.stored[method].get(point.t.isoformat())
            if saved is None:
                continue
            answer = prediction_answer(saved, point, row['model'])
            data.scored[method][point.t.isoformat()] = ScoredPoint(point.t.isoformat(), outcome.label,
                                                                answer.choice, answer.probabilities)
    complete = data.source_ready
    for method in METHODS:
        last = store.db.execute('''SELECT status FROM runs WHERE experiment_id=1 AND split='dev'
            AND method=? ORDER BY id DESC LIMIT 1''',(method,)).fetchone()
        complete = complete and last is not None and last['status']=='complete'
    return {'state':'ready', **comparison_report(data.scored,data.eligible,complete=complete),
            'message':'開發段勝出＝值得前瞻驗證，不是證明有效'}
