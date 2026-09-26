#!/usr/local/bin/python3
"""Run one frozen development split; explicit API opt-in and per-invocation quota.

No holdout predictions, outcomes, labels, statistics or revelations are computed.
JevRunner's existing frozen-data digest check remains in force. The independent
--max-calls ceiling counts every HTTP attempt, including 429/529 retries.
"""
import argparse
from collections import Counter
from contextlib import contextmanager
from dataclasses import dataclass, field
import fcntl
import json
import os
from pathlib import Path
import sys
import threading
import time

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from back.baselines import Baselines, METHODS
from back.data import DataError
from back.db_writer import DBWriter
from back.experiment import (ActivityGate, canonical, experiment_row, load_plan, verify_digest)
from back.http_client import ClientError
from back.jevcast import JevClient, JevRunner
from back.replay import LABELS, Replay, observation
from back.store import now_string

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / 'data' / 'block-3-real.sqlite3'
ALL_METHODS = ('jev', *METHODS)


class CallBudget:
    """Shared by all six workers; a retry consumes another permit, never a refund."""
    def __init__(self, limit):
        if type(limit) is not int or limit < 0:
            raise DataError('invalid_max_calls')
        self.limit = limit
        self.calls = self.retries = 0
        self.lock = threading.Lock()
        self.stopped = threading.Event()
        if limit == 0:
            self.stopped.set()

    def consume(self, *, retry=False):
        with self.lock:
            if self.calls >= self.limit:
                self.stopped.set()
                raise ClientError('call_limit')
            self.calls += 1
            self.retries += int(retry)
            if self.calls == self.limit:
                self.stopped.set()


class DevClient(JevClient):
    def __init__(self, budget, **transport):
        super().__init__(before_request=self._before_http, **transport)
        self.budget = budget
        self.local = threading.local()
        self.lock = threading.Lock()
        self.errors = Counter()
        self.quota_blocked = 0

    def _before_http(self):
        self.budget.consume(retry=self.local.attempts > 0)
        self.local.attempts += 1

    def predict(self, point, **kwargs):
        self.local.attempts = 0
        try:
            return super().predict(point, **kwargs)
        except ClientError as exc:
            with self.lock:
                if exc.code == 'call_limit' and self.local.attempts == 0:
                    self.quota_blocked += 1
                else:
                    self.errors[exc.code] += 1
            raise


@dataclass
class Development:
    experiment_id: int
    dev_start: str
    dev_end: str
    candidates: int
    predictable: set = field(repr=False)
    scorable: int
    cohort: dict = field(repr=False)
    baseline_new: dict
    baseline_failed: dict


def dev_predictions(store, experiment_id, start, end):
    # Every read of predictions is explicitly date- and experiment-bounded.
    return [dict(r) for r in store.db.execute('''SELECT method,t,input_hash,input_json,
        answer,probs_json FROM predictions WHERE experiment_id=?
        AND substr(t,1,10) BETWEEN ? AND ?''', (experiment_id, start, end))]


def check_reference_settings(store, experiment_id, reference_id):
    row, reference = experiment_row(store, experiment_id), experiment_row(store, reference_id)
    fields = ('dev_start', 'dev_end', 'hold_start', 'hold_end', 'threshold_permille',
              'feature_version', 'model', 'config_json')
    if experiment_id == reference_id or any(row[key] != reference[key] for key in fields):
        raise DataError('reference_settings_mismatch')
    verify_digest(store, row)
    verify_digest(store, reference)


def compare_baselines(store, dev, reference_id):
    """Fail before any HTTP if development baseline answers/inputs differ."""
    check_reference_settings(store, dev.experiment_id, reference_id)
    def values(experiment_id, method):
        return {row['t']: (row['input_hash'], row['input_json'], row['answer'],
                           json.loads(row['probs_json']))
                for row in dev_predictions(store, experiment_id, dev.dev_start, dev.dev_end)
                if row['method'] == method}
    compared = {}
    for method in METHODS:
        current, prior = values(dev.experiment_id, method), values(reference_id, method)
        if not current or current != prior:
            raise DataError('baseline_reference_mismatch')
        compared[method] = {'n': len(current), 'identical': True}
    return {'experiment_id': reference_id, 'methods': compared}


def prepare_development(store, experiment_id, progress=None):
    """Compute chronologically, then atomically persist all local methods/outcomes.

    Majority receives only previously visited predictable+scorable observations;
    Baselines additionally enforces t'+30 <= t. No writes occur while feature
    preparation is running, so SQLite cache invalidation cannot change inputs.
    """
    with store.transaction():
        row = experiment_row(store, experiment_id)
        verify_digest(store, row)
        plan = load_plan(store, experiment_id)
        replay = Replay(store, plan)
        stored = {(r['method'], r['t']): r for r in dev_predictions(
            store, experiment_id, row['dev_start'], row['dev_end'])}
        old_outcomes = {r['t']: tuple(r) for r in store.db.execute('''SELECT t,close_t,close_end,label
            FROM outcomes WHERE experiment_id=? AND substr(t,1,10) BETWEEN ? AND ?''',
            (experiment_id, row['dev_start'], row['dev_end']))}
        history, outcomes = [], []
        prepared = {method: [] for method in METHODS}
        failed = {method: 0 for method in METHODS}
        predictable, cohort = set(), {}
        candidates = scorable = 0
        for t in replay.candidates('dev'):
            if plan.split_of(t.date().isoformat()) != 'dev':
                raise DataError('development_boundary_violation')
            candidates += 1
            point = replay.prepare(t)
            outcome = replay.outcome(point)
            stamp = t.isoformat()
            if outcome.scorable:
                scorable += 1
                values = (stamp, point.close_t, outcome.close_end, outcome.label)
                if stamp in old_outcomes:
                    if old_outcomes[stamp] != values:
                        raise DataError('outcome_conflict')
                else:
                    outcomes.append((experiment_id, *values))
            if point.predictable:
                predictable.add(stamp)
                if outcome.scorable:
                    cohort[stamp] = outcome.label
                methods = Baselines(replay, history)
                for method in METHODS:
                    answer = methods.predict(method, point)
                    if answer.answer is None:
                        failed[method] += 1
                        continue
                    probs = canonical(answer.probabilities)
                    prior = stored.get((method, stamp))
                    if prior is not None:
                        if (prior['input_hash'], prior['input_json'], prior['answer'],
                                json.loads(prior['probs_json'])) != (
                                point.input_hash, point.input_json, answer.answer, answer.probabilities):
                            raise DataError('baseline_prediction_conflict')
                    else:
                        prepared[method].append((stamp, point.input_hash, point.input_json,
                                                 answer.answer, probs))
                if outcome.scorable:
                    history.append(observation(point, outcome))
            if progress is not None and candidates % 200 == 0:
                progress({'phase': 'baselines', 'dev_candidates_processed': candidates})
        store.db.executemany('INSERT INTO outcomes VALUES (?,?,?,?,?)', outcomes)
        for method, values in prepared.items():
            if not values:
                continue
            created = now_string()
            cursor = store.db.execute('''INSERT INTO runs
                (experiment_id,method,split,started_at,status) VALUES (?,?,?,?,?)''',
                (experiment_id, method, 'dev', created, 'running'))
            run_id = cursor.lastrowid
            store.db.execute('INSERT INTO run_scopes VALUES (?,1)', (run_id,))
            store.db.executemany('INSERT INTO predictions VALUES (?,?,?,?,?,?,?,?,?,?)',
                ((experiment_id, method, stamp, run_id, input_hash, input_json,
                  answer, probs, None, created) for stamp, input_hash, input_json, answer, probs in values))
            store.db.execute('UPDATE runs SET status=?,finished_at=?,n_ok=?,n_fail=? WHERE id=?',
                ('complete', now_string(), len(values), failed[method], run_id))
    return Development(experiment_id, row['dev_start'], row['dev_end'], candidates,
                       predictable, scorable, cohort,
                       {method: len(values) for method, values in prepared.items()}, failed)


def raw_report(store, dev, run, client, elapsed):
    methods = {method: {} for method in ALL_METHODS}
    for row in dev_predictions(store, dev.experiment_id, dev.dev_start, dev.dev_end):
        if row['method'] in methods and row['t'] in dev.predictable:
            if row['answer'] not in LABELS:
                raise DataError('invalid_stored_prediction')
            methods[row['method']][row['t']] = row['answer']
    common = set(dev.cohort)
    for answers in methods.values():
        common.intersection_update(answers)
    metrics = {}
    for method, answers in methods.items():
        correct = sum(answers[t] == dev.cohort[t] for t in common)
        metrics[method] = {'n': len(common), 'correct': correct,
                           'accuracy': correct / len(common) if common else None}
    choices = Counter(methods['jev'].values())
    missing = len(dev.predictable - methods['jev'].keys())
    return dict(experiment_id=dev.experiment_id, split='dev', dev_start=dev.dev_start,
        dev_end=dev.dev_end, candidates=dev.candidates, predictable=len(dev.predictable),
        scorable=dev.scorable, predictable_and_scorable=len(dev.cohort),
        elapsed_seconds=elapsed, all_methods_intersection=metrics,
        baseline_new_predictions=dev.baseline_new, baseline_failures=dev.baseline_failed,
        jev=dict(run_status=run['status'], committed_this_run=run['n_ok'],
            failed_this_run=run['n_fail'], existing_skipped=run['skipped'],
            http_calls=client.budget.calls, retries=client.budget.retries,
            max_calls=client.budget.limit, quota_blocked_before_first_http=client.quota_blocked,
            errors=dict(client.errors), successful_total=len(methods['jev']),
            missing_predictable_points=missing, choice_counts={label: choices[label] for label in LABELS}),
        complete=(missing == 0 and not any(dev.baseline_failed.values())))


@contextmanager
def exclusive_run(path):
    # Cross-process exclusion complements JevRunner's process-local ActivityGate.
    with path.with_name(path.name + '.run-dev.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise DataError('development_run_busy') from None
        yield


def run_development(path, *, experiment_id=1, max_calls=3500, client=None, progress=None,
                    reference_experiment=None):
    """Programmatic execution; the public CLI requires --execute before calling."""
    path = Path(path).resolve()
    if not path.is_file():
        raise DataError('database_missing')
    budget = CallBudget(max_calls)
    if client is None:
        client = DevClient(budget)
    elif client.budget.limit != max_calls:
        raise DataError('budget_mismatch')
    with exclusive_run(path):
        started = time.monotonic()
        writer, gate = DBWriter(path), ActivityGate()
        runner = None
        try:
            if reference_experiment is not None:
                writer.submit(lambda store: check_reference_settings(
                    store, experiment_id, reference_experiment)).result()
            dev = writer.submit(lambda store: prepare_development(store, experiment_id, progress)).result()
            comparison = None if reference_experiment is None else writer.submit(
                lambda store: compare_baselines(store, dev, reference_experiment)).result()
            def pending(store):
                known = {r['t'] for r in dev_predictions(store, experiment_id, dev.dev_start, dev.dev_end)
                         if r['method'] == 'jev'}
                return bool(dev.predictable - known)
            if writer.submit(pending).result() and max_calls and not os.environ.get('TYPESAFE_API_KEY'):
                raise DataError('missing_key')
            def notify(snapshot):
                if progress is not None and (snapshot['status'] != 'running'
                        or (snapshot['n_ok'] + snapshot['n_fail']) % 100 == 0):
                    progress({'phase': 'jev', **snapshot, 'http_calls': client.budget.calls})
            runner = JevRunner(writer, gate, client=client, on_progress=notify,
                               stop_dispatch=client.budget.stopped)
            run = runner.start(experiment_id, split='dev').done.result()
            result = writer.submit(lambda store: raw_report(
                store, dev, run, client, time.monotonic() - started)).result()
            if comparison is not None:
                result['baseline_reference'] = comparison
            return result
        finally:
            if runner is not None:
                runner.close().result()
                for worker in runner.workers:
                    worker.join(timeout=16)
            writer.close()
            writer.thread.join(timeout=2)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--db', type=Path, default=DEFAULT_DB)
    parser.add_argument('--experiment', type=int, default=1)
    parser.add_argument('--reference-experiment', type=int,
                        help='Require identical split/settings and development baselines before HTTP')
    parser.add_argument('--max-calls', type=int, default=3500,
                        help='Maximum HTTP attempts this invocation, including retries; 0 disables HTTP')
    parser.add_argument('--report', type=Path, help='Save the same development-only JSON printed to stdout')
    args = parser.parse_args(argv)
    if not args.execute:
        parser.error('--execute is required')
    if args.max_calls < 0:
        parser.error('--max-calls must be nonnegative')
    try:
        result = run_development(args.db, experiment_id=args.experiment, max_calls=args.max_calls,
            reference_experiment=args.reference_experiment,
            progress=lambda value: print(json.dumps(value), file=sys.stderr, flush=True))
        text = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False)
        key = os.environ.get('TYPESAFE_API_KEY')
        if key and key in text:
            raise DataError('secret_output_guard')
        if args.report:
            args.report.write_text(text + '\n')
        print(text)
        return 0 if result['complete'] else 1
    except (DataError, ClientError):
        print('development_run_failed', file=sys.stderr)
        return 2
    except Exception:
        print('development_run_failed', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
