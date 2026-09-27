"""Preregistered p3 stage A. Timestamp-only sampling precedes all label reads.

Original binary answers and matured up/down counts are retained for audit.
This module has no holdout, forward, reveal, or stage-B execution path.
"""
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import timedelta
import hashlib
import json
import random

from .data import DataError, local_time
from .experiment import (ActivityGate, canonical, create_experiment, experiment_row,
                         load_plan, verify_digest)
from .jevcast import JevAnswer, validate_response
from .replay import Replay, persist_outcome
from .score import ScoredPoint, metrics, paired_bootstrap, prediction_answer
from .store import now_string

SEED = 20260927
SAMPLE_SIZE = 500
EXPERIMENT = 3
METHOD = 'jev_move'


def check_settings(store):
    reference, row = experiment_row(store, 1), experiment_row(store, EXPERIMENT)
    if reference['prompt_version'] != 'p1' or row['prompt_version'] != 'p3':
        raise DataError('move_settings_changed')
    for key in ('dev_start', 'dev_end', 'hold_start', 'hold_end', 'threshold_permille',
                'feature_version', 'model', 'config_json'):
        if row[key] != reference[key]:
            raise DataError('move_settings_changed')
    verify_digest(store, reference)
    verify_digest(store, row)
    return reference, row


def ensure_experiment(store):
    """Create with atomic readiness checks, then require exact reference boundaries."""
    if store.db.execute('SELECT 1 FROM experiments WHERE id=3').fetchone() is None:
        ids = [r[0] for r in store.db.execute('SELECT id FROM experiments ORDER BY id')]
        if ids != [1, 2]:
            raise DataError('move_experiment_id_unavailable')
        ref = experiment_row(store, 1)
        config = json.loads(ref['config_json'])
        # create_experiment already atomically checks all readiness prerequisites.
        row = create_experiment(store, ActivityGate(), start=ref['dev_start'], end=ref['hold_end'],
                                symbol=config['symbol'], threshold_permille=ref['threshold_permille'],
                                prompt_version='p3')
        if row['id'] != EXPERIMENT:
            raise DataError('move_experiment_id_unavailable')
    return check_settings(store)[1]


def population_times(store, reference):
    """Audited p1 eligible cohort: prediction presence AND scorable outcome presence.

    Read ONLY the timestamp column. Never sample on truth, probabilities, or
    whether vol_prior happens to have an answer. Missing baselines abort later.
    """
    return tuple(r[0] for r in store.db.execute('''SELECT p.t FROM predictions p
        JOIN outcomes o ON o.experiment_id=p.experiment_id AND o.t=p.t
        WHERE p.experiment_id=1 AND p.method='majority'
        AND substr(p.t,1,10) BETWEEN ? AND ? ORDER BY p.t''',
        (reference['dev_start'], reference['dev_end'])))


def freeze_sample(store, *, size=SAMPLE_SIZE):
    with store.transaction():
        reference, _ = check_settings(store)
        population = population_times(store, reference)
        if len(population) < size or size < 1:
            raise DataError('insufficient_stage_a_population')
        selected = sorted(random.Random(SEED).sample(population, size))
        digest = hashlib.sha256(canonical(population).encode()).hexdigest()
        values = (EXPERIMENT, 'A', SEED, size, reference['data_digest'], digest, canonical(selected))
        prior = store.db.execute('SELECT * FROM move_samples WHERE experiment_id=3').fetchone()
        if prior is None:
            store.db.execute('INSERT INTO move_samples VALUES (?,?,?,?,?,?,?,?)', (*values, now_string()))
        elif tuple(prior)[:-1] != values:
            raise DataError('stage_a_sample_changed')
    return tuple(selected)


def matured_counts(t, observations):
    """Only eligible development records are supplied; inclusive 30-minute boundary."""
    counts = Counter(label for stamp, label in observations
                     if local_time(stamp) + timedelta(minutes=30) <= local_time(t))
    return counts['up'], counts['down']


def convert(answer, up, down):
    u = (up + 1) / (up + down + 2)
    probabilities = {'flat': answer.probabilities['still'],
                     'up': answer.probabilities['move'] * u,
                     'down': answer.probabilities['move'] * (1-u)}
    choice = max(('flat', 'up', 'down'), key=probabilities.__getitem__)
    return JevAnswer(choice, probabilities, answer.model_reported, answer.latency_seconds)


def prepare_sample(store, times):
    reference, row = check_settings(store)
    engine = Replay(store, load_plan(store, EXPERIMENT))
    population = set(population_times(store, reference))
    observations = [(r['t'], r['label']) for r in store.db.execute('''SELECT t,label FROM outcomes
        WHERE experiment_id=1 AND substr(t,1,10) BETWEEN ? AND ? ORDER BY t''',
        (row['dev_start'], row['dev_end'])) if r['t'] in population]
    prepared = {}
    for stamp in times:
        if stamp not in population or engine.plan.split_of(stamp[:10]) != 'dev':
            raise DataError('stage_a_point_outside_development')
        point = engine.prepare(stamp)
        outcome = engine.outcome(point)
        if not point.predictable or not outcome.scorable:
            raise DataError('stage_a_eligibility_changed')
        baseline = store.db.execute('''SELECT * FROM predictions WHERE experiment_id=1
            AND method='vol_prior' AND t=?''', (stamp,)).fetchone()
        if baseline is None:
            raise DataError('stage_a_baseline_missing')
        prediction_answer(baseline, point, row['model'])
        truth = dict(observations).get(stamp)
        if truth != outcome.label:
            raise DataError('stage_a_truth_conflict')
        prepared[stamp] = (point, outcome, *matured_counts(stamp, observations))
    return prepared


def run_stage_a(store, client, *, execute=False, size=SAMPLE_SIZE, progress=None):
    if not execute:
        return {'stage': 'A', 'execute': False, 'http_calls': 0}
    ensure_experiment(store)
    times = freeze_sample(store, size=size)  # COMMIT the time-only draw before reading any label.
    prepared = prepare_sample(store, times)
    missing = []
    for stamp, (point, outcome, up, down) in prepared.items():
        old = store.db.execute('''SELECT * FROM predictions WHERE experiment_id=3
            AND method='jev_move' AND t=?''', (stamp,)).fetchone()
        raw = store.db.execute('SELECT * FROM move_answers WHERE experiment_id=3 AND t=?', (stamp,)).fetchone()
        if old is not None:
            prediction_answer(old, point, experiment_row(store, 3)['model'])
            if raw is None or (raw['up_count'], raw['down_count']) != (up, down):
                raise DataError('move_audit_conflict')
            payload = {'answers': {'move': {
                'choice': raw['choice'], 'probabilities': json.loads(raw['probs_json'])}}}
            if raw['model_reported'] is not None:
                payload['model'] = raw['model_reported']
            binary = validate_response(payload, prompt_version='p3')
            expected = convert(binary, up, down)
            if old['answer'] != expected.choice or json.loads(old['probs_json']) != expected.probabilities:
                raise DataError('move_conversion_conflict')
        elif raw is not None:
            raise DataError('move_audit_conflict')
        else:
            missing.append(stamp)
    with store.transaction():
        run_id = store.db.execute('''INSERT INTO runs(experiment_id,method,split,started_at,status)
            VALUES (3,'jev_move','dev',?,'running')''', (now_string(),)).lastrowid
        store.db.execute('INSERT INTO run_scopes VALUES (?,0)', (run_id,))
    failures = success = 0
    try:
        with ThreadPoolExecutor(max_workers=6) as pool:
            jobs = {pool.submit(client.predict, prepared[t][0], prompt_version='p3'): t for t in missing}
            for future in as_completed(jobs):
                stamp = jobs[future]
                point, outcome, up, down = prepared[stamp]
                try:
                    raw = future.result()
                except Exception:
                    failures += 1  # Never persist exception text or upstream content.
                    with store.transaction():
                        store.db.execute('UPDATE runs SET n_fail=n_fail+1 WHERE id=?', (run_id,))
                    continue
                answer = convert(raw, up, down)
                with store.transaction():
                    persist_outcome(store, point, outcome)
                    store.db.execute('INSERT INTO predictions VALUES (?,?,?,?,?,?,?,?,?,?)',
                        (EXPERIMENT, METHOD, stamp, run_id, point.input_hash, point.input_json,
                         answer.choice, canonical(answer.probabilities), answer.model_reported, now_string()))
                    store.db.execute('INSERT INTO move_answers VALUES (?,?,?,?,?,?,?,?)',
                        (EXPERIMENT, stamp, raw.choice, canonical(raw.probabilities), up, down,
                         raw.model_reported, raw.latency_seconds))
                    store.db.execute('UPDATE runs SET n_ok=n_ok+1 WHERE id=?', (run_id,))
                success += 1
                if progress and (success + failures) % 25 == 0:
                    progress({'stage': 'A', 'committed': success, 'failed': failures, 'skipped': size-len(missing)})
    finally:
        with store.transaction():
            store.db.execute('UPDATE runs SET status=?,finished_at=? WHERE id=?',
                ('complete' if success == len(missing) else 'incomplete', now_string(), run_id))
    result = dict(stage='A', sample_size=size, new_predictions=success, failed=failures,
                  skipped=size-len(missing), missing=len(missing)-success)
    if result['missing'] == 0:
        result['report'] = stage_a_report(store, times)
    return result


def stage_a_report(store, times):
    reference, row = check_settings(store)
    prepared = prepare_sample(store, times)
    scored = {METHOD: {}, 'vol_prior': {}}
    binary_choices = Counter()
    for stamp, (point, outcome, _, _) in prepared.items():
        for method, exp in ((METHOD, EXPERIMENT), ('vol_prior', 1)):
            saved = store.db.execute('''SELECT * FROM predictions
                WHERE experiment_id=? AND method=? AND t=?''', (exp, method, stamp)).fetchone()
            if saved is None:
                raise DataError('stage_a_incomplete')
            answer = prediction_answer(saved, point, row['model'])
            scored[method][stamp] = ScoredPoint(stamp, outcome.label, answer.choice, answer.probabilities)
        binary = store.db.execute('SELECT choice FROM move_answers WHERE experiment_id=3 AND t=?', (stamp,)).fetchone()
        if binary is None:
            raise DataError('stage_a_incomplete')
        binary_choices[binary[0]] += 1
    summaries = {method: metrics(points.values()) for method, points in scored.items()}
    boot = paired_bootstrap(scored[METHOD], scored['vol_prior'])
    return dict(methods=summaries,
        brier_difference=summaries[METHOD]['brier']-summaries['vol_prior']['brier'],
        accuracy_difference=summaries[METHOD]['accuracy']-summaries['vol_prior']['accuracy'],
        bootstrap=boot, binary_choice_distribution=dict(binary_choices),
        choice_distribution={k:dict(Counter(p.choice for p in v.values())) for k,v in scored.items()},
        stage_b_criterion_met=boot['brier_difference_ci95'][1] < 0,
        statement='值得進入階段 B 驗證' if boot['brier_difference_ci95'][1] < 0 else '沒有改善',
        next_action='停止，等 cc 決定；未執行階段 B')
