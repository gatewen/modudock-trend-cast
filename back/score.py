"""Frozen-split scoring, common-cohort comparison and paired day bootstrap.

Pure statistical helpers plus a snapshot reader. This module never reveals a
split or writes data; load_split itself rejects unauthorized holdout reads.
Probabilities are scored as accepted, without rounding or renormalization.
"""
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from decimal import Decimal
import json
import math
import random

from .baselines import METHODS
from .data import DataError
from .experiment import holdout_revealed, load_plan
from .http_client import ClientError, _invalid_constant, _pairs
from .jevcast import validate_response
from .replay import LABELS, Replay

ALL_METHODS = ('jev', *METHODS)
BOOTSTRAP_REPETITIONS = 2000
BOOTSTRAP_SEED = 20260927
Z95 = 1.959963984540054
INCOMPLETE = '結果不完整，不下結論'
NO_EVIDENCE = '沒有證據顯示 jev 比簡單方法好'


def wilson95(correct, n):
    if type(n) is not int or type(correct) is not int or not 0 <= correct <= n:
        raise DataError('invalid_binomial_counts')
    if n == 0:
        return None
    p = correct / n
    z2 = Z95 * Z95
    denominator = 1 + z2 / n
    center = (p + z2 / (2 * n)) / denominator
    radius = Z95 * math.sqrt(p * (1 - p) / n + z2 / (4 * n * n)) / denominator
    return [0.0 if correct == 0 else max(0.0, center - radius),
            1.0 if correct == n else min(1.0, center + radius)]


@dataclass(frozen=True)
class ScoredPoint:
    t: str
    truth: str = field(repr=False)
    choice: str = field(repr=False)
    probabilities: dict = field(repr=False)

    @property
    def brier(self):
        return math.fsum((self.probabilities[label] - int(self.truth == label)) ** 2
                         for label in LABELS)

    @property
    def correct(self):
        return int(self.choice == self.truth)


def metrics(points):
    points = tuple(points)
    matrix = {truth: {choice: 0 for choice in LABELS} for truth in LABELS}
    for point in points:
        if point.truth not in LABELS or point.choice not in LABELS:
            raise DataError('invalid_scoring_label')
        matrix[point.truth][point.choice] += 1
    n = len(points)
    correct = sum(matrix[label][label] for label in LABELS)
    classes = {}
    for label in LABELS:
        actual = sum(matrix[label].values())
        predicted = sum(matrix[truth][label] for truth in LABELS)
        tp = matrix[label][label]
        classes[label] = {'true_count': actual, 'predicted_count': predicted, 'correct': tp,
                          'precision': tp / predicted if predicted else None,
                          'recall': tp / actual if actual else None}
    return dict(n=n, correct=correct, accuracy=correct / n if n else None,
                accuracy_wilson95=wilson95(correct, n),
                brier=math.fsum(point.brier for point in points) / n if n else None,
                per_class=classes, confusion_matrix=matrix,
                confusion_axes={'rows': 'truth', 'columns': 'choice', 'order': list(LABELS)})


def descriptive_choices(points):
    """Guess and truth frequencies share exactly the jev-valid/scorable cohort."""
    summary = metrics(points)
    n = summary['n']
    classes = {}
    for label, values in summary['per_class'].items():
        predicted, actual = values['predicted_count'], values['true_count']
        classes[label] = dict(choice_count=predicted, correct=values['correct'],
            hit_rate=values['precision'], choice_share=predicted / n if n else None,
            true_count=actual, true_share=actual / n if n else None,
            share_difference=(predicted - actual) / n if n else None)
    return {'cohort': 'jev_valid_predictable_and_scorable', 'n': n,
            'classes': classes, 'affects_conclusion': False}


def percentile(values, probability):
    ordered = sorted(values)
    position = (len(ordered) - 1) * probability
    low = math.floor(position)
    high = math.ceil(position)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def paired_bootstrap(jev, baseline, *, repetitions=BOOTSTRAP_REPETITIONS, seed=BOOTSTRAP_SEED):
    """Draw N trading-day blocks with replacement; pool all paired points.

    N is the number of distinct days represented in the common comparison set.
    The same sampled days serve both methods and both metrics. Unequal blocks
    retain their full sizes; this is a point-weighted mean, not a mean of days.
    """
    if set(jev) != set(baseline):
        raise DataError('unpaired_comparison')
    if type(repetitions) is not int or repetitions < 2:
        raise DataError('invalid_bootstrap_repetitions')
    grouped = defaultdict(list)
    for t in sorted(jev):
        left, right = jev[t], baseline[t]
        if left.t != t or right.t != t or left.truth != right.truth:
            raise DataError('unpaired_comparison')
        grouped[t[:10]].append((left.brier - right.brier, left.correct - right.correct))
    blocks = [(len(grouped[day]), math.fsum(v[0] for v in grouped[day]),
               sum(v[1] for v in grouped[day])) for day in sorted(grouped)]
    metadata = dict(repetitions=repetitions, seed=seed, trading_days=len(blocks),
                    unit='trading_day', interval='percentile_95_linear')
    if not blocks:
        return {**metadata, 'brier_difference_ci95': None, 'accuracy_difference_ci95': None}
    rng = random.Random(seed)
    brier, accuracy = [], []
    for _ in range(repetitions):
        sampled = [blocks[rng.randrange(len(blocks))] for _ in range(len(blocks))]
        count = sum(block[0] for block in sampled)
        brier.append(math.fsum(block[1] for block in sampled) / count)
        accuracy.append(sum(block[2] for block in sampled) / count)
    return {**metadata, 'brier_difference_ci95': [percentile(brier, .025), percentile(brier, .975)],
            'accuracy_difference_ci95': [percentile(accuracy, .025), percentile(accuracy, .975)]}


def conclusion(baseline, brier_difference, ci95, *, common_n, eligible_n, complete):
    # Integer comparison fixes the inclusive 95% boundary without float error.
    if (not complete or eligible_n == 0 or common_n * 100 < eligible_n * 95
            or baseline is None or ci95 is None):
        return INCOMPLETE
    if brier_difference < 0 and ci95[1] < 0:
        return f'jev 比 {baseline} 好'
    if brier_difference > 0 and ci95[0] > 0:
        return f'jev 比 {baseline} 差'
    # Crossing or touching zero does not establish a directional difference.
    return NO_EVIDENCE


def prediction_answer(row, point, model):
    """Reject corrupt/mismatched stored forecasts; never repair their probabilities."""
    if row['input_hash'] != point.input_hash or row['input_json'] != point.input_json:
        raise DataError('prediction_input_mismatch')
    probabilities = json.loads(row['probs_json'], parse_float=Decimal,
        parse_constant=_invalid_constant, object_pairs_hook=_pairs)
    payload = {'answers': {'direction': {'choice': row['answer'], 'probabilities': probabilities}}}
    if row['model_reported'] is not None:
        payload['model'] = row['model_reported']
    return validate_response(payload, model)


@dataclass
class SplitScores:
    name: str
    first_day: str
    last_day: str
    candidates: int
    predictable: int
    scorable: int
    eligible: dict = field(repr=False)
    scored: dict = field(repr=False)
    methods: dict = field(repr=False)
    common: tuple = field(repr=False)
    complete: bool


def load_split(store, row, split):
    """Read an authorized split; report.py supplies a consistent read snapshot."""
    if split == 'holdout' and not holdout_revealed(store, row):
        raise DataError('holdout_locked')
    plan = load_plan(store, row['id'])
    days = plan.days_for(split)
    first, last = days[0], days[-1]
    args = (row['id'], first, last)
    stored = {method: {} for method in ALL_METHODS}
    for record in store.db.execute('''SELECT * FROM predictions WHERE experiment_id=?
        AND substr(t,1,10) BETWEEN ? AND ? ORDER BY t''', args):
        if record['method'] in stored:
            stored[record['method']][record['t']] = record
    outcomes = {record['t']: record for record in store.db.execute('''SELECT * FROM outcomes
        WHERE experiment_id=? AND substr(t,1,10) BETWEEN ? AND ?''', args)}
    runs = {method: [] for method in ALL_METHODS}
    for run in store.db.execute('''SELECT * FROM runs WHERE experiment_id=? AND split=? ORDER BY id''',
                                (row['id'], split)):
        if run['method'] in runs:
            runs[run['method']].append(run)
    # Older DBs have no traversal metadata; all expected committed answers are
    # sufficient evidence, but an isolated partial smoke run is not.
    scopes = set()
    if store.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='run_scopes'").fetchone():
        scopes = {r[0] for r in store.db.execute('''SELECT s.run_id FROM run_scopes s JOIN runs r ON r.id=s.run_id
            WHERE r.experiment_id=? AND r.split=? AND s.full_split=1 AND r.status='complete' ''', (row['id'], split))}
    scored = {method: {} for method in ALL_METHODS}
    valid = Counter()
    invalid = Counter()
    eligible = {}
    candidates = predictable = scorable = 0
    engine = Replay(store, plan)
    for t in engine.candidates(split):
        stamp = t.isoformat()
        point = engine.prepare(t)
        outcome = engine.outcome(point)
        candidates += 1
        predictable += int(point.predictable)
        scorable += int(outcome.scorable)
        old = outcomes.get(stamp)
        if old is not None and (not outcome.scorable or
                (old['close_t'], old['close_end'], old['label']) != (point.close_t, outcome.close_end, outcome.label)):
            raise DataError('outcome_mismatch')
        if not point.predictable:
            continue
        if outcome.scorable:
            eligible[stamp] = outcome.label
        for method in ALL_METHODS:
            prediction = stored[method].get(stamp)
            if prediction is None:
                continue
            try:
                answer = prediction_answer(prediction, point, row['model'])
            except (DataError, ClientError, ValueError, TypeError, KeyError, RecursionError):
                invalid[method] += 1
                continue
            valid[method] += 1
            if outcome.scorable:
                scored[method][stamp] = ScoredPoint(stamp, outcome.label, answer.choice, answer.probabilities)
    methods = {}
    common = set(eligible)
    for method in ALL_METHODS:
        common.intersection_update(scored[method])
        latest = runs[method][-1] if runs[method] else None
        traversed = any(run['id'] in scopes for run in runs[method])
        complete = (latest is not None and latest['status'] == 'complete' and not invalid[method]
                    and (traversed or valid[method] == predictable))
        methods[method] = {**metrics(scored[method].values()),
            'coverage': len(scored[method]) / len(eligible) if eligible else None,
            'eligible_missing': len(eligible) - len(scored[method]),
            'predictable_missing': predictable - valid[method], 'invalid_predictions': invalid[method],
            'failure_attempts': sum(run['n_fail'] for run in runs[method]),
            'replay_complete': complete}
    return SplitScores(split, first, last, candidates, predictable, scorable, eligible,
                       scored, methods, tuple(sorted(common)), all(m['replay_complete'] for m in methods.values()))


def best_development_baseline(development):
    if not development.common:
        return None
    # Fixed tie order is METHODS. Selection uses the same five-method dev
    # intersection and is held fixed for holdout and every bootstrap replicate.
    return min(METHODS, key=lambda method: math.fsum(
        development.scored[method][t].brier for t in development.common) / len(development.common))


def split_report(data, baseline, *, development_ready=True):
    common = {method: {t: data.scored[method][t] for t in data.common} for method in ALL_METHODS}
    summaries = {method: metrics(points.values()) for method, points in common.items()}
    bootstrap = paired_bootstrap(common['jev'], common[baseline]) if baseline else {
        'repetitions': BOOTSTRAP_REPETITIONS, 'seed': BOOTSTRAP_SEED, 'trading_days': 0,
        'unit': 'trading_day', 'interval': 'percentile_95_linear',
        'brier_difference_ci95': None, 'accuracy_difference_ci95': None}
    brier = accuracy = None
    if baseline is not None and data.common:
        brier = math.fsum(common['jev'][t].brier - common[baseline][t].brier for t in data.common) / len(data.common)
        accuracy = sum(common['jev'][t].correct - common[baseline][t].correct for t in data.common) / len(data.common)
    return dict(first_day=data.first_day, last_day=data.last_day,
        candidates=data.candidates, predictable=data.predictable, scorable=data.scorable,
        predictable_and_scorable=len(data.eligible), methods=data.methods,
        comparison=dict(n=len(data.common), eligible_n=len(data.eligible),
            coverage=len(data.common) / len(data.eligible) if data.eligible else None,
            baseline=baseline, baseline_selection='development_five_method_intersection_brier',
            intersection_metrics=summaries,
            brier_difference=brier, accuracy_difference=accuracy, bootstrap=bootstrap,
            replay_complete=data.complete,
            statement=conclusion(baseline, brier, bootstrap['brier_difference_ci95'],
                common_n=len(data.common), eligible_n=len(data.eligible),
                complete=data.complete and development_ready)),
        jev_description=descriptive_choices(data.scored['jev'].values()))
