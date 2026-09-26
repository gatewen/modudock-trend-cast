#!/usr/local/bin/python3
"""Final exam for experiment 1/p1 only; disclose only after all answers commit.

The 1500-call ceiling includes retries and missing-answer repair passes. Before
reveal, output contains activity metadata only, never outcomes or performance.
"""
import argparse
import json
import os
from pathlib import Path
import sys
import time

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from back.baselines import METHODS
from back.data import DataError
from back.db_writer import DBWriter
from back.experiment import (MODEL, ActivityGate, experiment_row, holdout_revealed, load_plan,
    require_final_holdout, reveal_holdout, verify_digest)
from back.http_client import ClientError
from back.jevcast import JevRunner
from back.jobs import BaselineRunner
from back.replay import Replay
from back.report import build_report, markdown_report
from back.score import ALL_METHODS, best_development_baseline, load_split, prediction_answer
from back.store import DEFAULT_DB
from scripts.run_dev import CallBudget, DevClient as BudgetClient, exclusive_run

MAX_CALLS = 1500
SELECTED_BASELINE = 'majority'


def check_selection(experiment_id, split):
    require_final_holdout(experiment_id)
    if split != 'holdout':
        raise DataError('holdout_split_required')


def preflight(store, experiment_id):
    row = experiment_row(store, experiment_id)
    require_final_holdout(experiment_id, row['prompt_version'])
    if row['model'] != MODEL:
        raise DataError('development_selection_changed')
    verify_digest(store, row)
    development = load_split(store, row, 'dev')
    if (not development.complete or not development.common
            or best_development_baseline(development) != SELECTED_BASELINE):
        raise DataError('development_selection_changed')
    return holdout_revealed(store, row)


def completion(store, row):
    """Validate stored answers/outcomes and traversal without disclosing scores."""
    require_final_holdout(row['id'], row['prompt_version'])
    verify_digest(store, row)
    args = (row['id'], row['hold_start'], row['hold_end'])
    stored = {(r['method'], r['t']): r for r in store.db.execute('''SELECT * FROM predictions
        WHERE experiment_id=? AND substr(t,1,10) BETWEEN ? AND ?''', args)}
    outcomes = {r['t']: r for r in store.db.execute('''SELECT * FROM outcomes
        WHERE experiment_id=? AND substr(t,1,10) BETWEEN ? AND ?''', args)}
    engine = Replay(store, load_plan(store, row['id']))
    missing = {m: 0 for m in ALL_METHODS}
    eligible = missing_outcomes = 0
    for t in engine.candidates('holdout'):
        point = engine.prepare(t)
        outcome = engine.outcome(point)
        stamp = t.isoformat()
        if outcome.scorable:
            old = outcomes.get(stamp)
            if old is None or (old['close_t'], old['close_end'], old['label']) != (point.close_t, outcome.close_end, outcome.label):
                missing_outcomes += 1
        elif stamp in outcomes:
            missing_outcomes += 1
        if not point.predictable:
            continue
        eligible += int(outcome.scorable)
        for method in ALL_METHODS:
            old = stored.get((method, stamp))
            try:
                if old is None:
                    raise DataError('missing_answer')
                prediction_answer(old, point, row['model'])
            except (DataError, ClientError, ValueError, TypeError, KeyError, RecursionError):
                missing[method] += 1
    traversed = {}
    for method in ALL_METHODS:
        last = store.db.execute('''SELECT r.status,s.full_split FROM runs r
            LEFT JOIN run_scopes s ON s.run_id=r.id WHERE r.experiment_id=?
            AND r.method=? AND r.split='holdout' ORDER BY r.id DESC LIMIT 1''', (row['id'], method)).fetchone()
        traversed[method] = bool(last and last['status'] == 'complete' and last['full_split'] == 1)
    return {'complete': bool(eligible and not any(missing.values()) and not missing_outcomes and all(traversed.values())),
            'missing': missing, 'missing_outcomes': missing_outcomes, 'traversed': traversed}


def require_complete(store, row):
    if not completion(store, row)['complete']:
        raise DataError('holdout_incomplete')


def finish_and_reveal(store, experiment_id):
    require_final_holdout(experiment_id)
    # The final check, exposure snapshot and reveal record share one transaction.
    reveal_holdout(store, experiment_id, check=require_complete)
    return build_report(store, experiment_id=experiment_id)


def run_holdout(path, *, experiment_id, split, max_calls=MAX_CALLS, client=None, progress=None):
    check_selection(experiment_id, split)
    if type(max_calls) is not int or not 0 <= max_calls <= MAX_CALLS:
        raise DataError('invalid_max_calls')
    path = Path(path).resolve()
    if not path.is_file():
        raise DataError('database_missing')
    client = client if client is not None else BudgetClient(CallBudget(max_calls))
    if client.budget.limit != max_calls:
        raise DataError('budget_mismatch')
    def notify(phase, method=None):
        if progress:
            progress({'phase': phase, 'method': method, 'experiment_id': experiment_id})
    def result(report=None):
        return {'experiment_id': experiment_id, 'split': 'holdout', 'complete': report is not None,
            'revealed': report is not None, 'http_calls': client.budget.calls,
            'retries': client.budget.retries, 'max_calls': max_calls, 'errors': dict(client.errors),
            'elapsed_seconds': time.monotonic() - started, **({'report': report} if report is not None else {})}
    with exclusive_run(path):
        started = time.monotonic()
        writer, gate = DBWriter(path), ActivityGate()
        baseline = jev = None
        try:
            revealed = writer.submit(lambda store: preflight(store, experiment_id)).result()
            if revealed:
                # Reopening the result never schedules more HTTP or duplicates reveals.
                return result(writer.submit(lambda store: build_report(store, experiment_id=experiment_id)).result())
            notify('baselines')
            baseline = BaselineRunner(writer, gate)
            for method in METHODS:
                notify('baseline', method)
                baseline.start(experiment_id, method=method, split='holdout').done.result()
            def state(store):
                return completion(store, experiment_row(store, experiment_id))
            status = writer.submit(state).result()
            if status['missing_outcomes'] or any(status['missing'][m] or not status['traversed'][m] for m in METHODS):
                return result()
            if not status['complete'] and max_calls and not os.environ.get('TYPESAFE_API_KEY'):
                raise DataError('missing_key')
            jev = JevRunner(writer, gate, client=client, stop_dispatch=client.budget.stopped)
            while not status['complete'] and not client.budget.stopped.is_set():
                notify('jev')
                before = client.budget.calls
                jev.start(experiment_id, split='holdout').done.result()
                status = writer.submit(state).result()
                if client.budget.calls == before and not status['complete']:
                    break  # Disabled auth or invalid stored records cannot be repaired by retrying.
            if not status['complete']:
                return result()
            notify('verify_then_reveal')
            report = writer.submit(lambda store: finish_and_reveal(store, experiment_id)).result()
            return result(report)
        finally:
            if jev is not None:
                jev.close().result()
                for worker in jev.workers:
                    worker.join(timeout=16)
            if baseline is not None:
                baseline.close().result()
            writer.close(); writer.thread.join(timeout=2)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--experiment', type=int, required=True)
    parser.add_argument('--split', choices=('holdout',), required=True)
    parser.add_argument('--db', type=Path, default=DEFAULT_DB)
    parser.add_argument('--max-calls', type=int, default=MAX_CALLS)
    parser.add_argument('--report', type=Path)
    parser.add_argument('--markdown', type=Path)
    args = parser.parse_args(argv)
    if not args.execute or args.experiment != 1 or not 0 <= args.max_calls <= MAX_CALLS:
        parser.error('requires --execute --experiment 1 --split holdout and max-calls in 0..1500')
    for output in (args.report, args.markdown):
        if output and (output.resolve() == args.db.resolve() or
                       (output.exists() and args.db.exists() and output.samefile(args.db))):
            parser.error('output must not be the database')
    try:
        value = run_holdout(args.db, experiment_id=args.experiment, split=args.split, max_calls=args.max_calls,
            progress=lambda event: print(json.dumps(event), file=sys.stderr, flush=True))
        text = json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n'
        if args.report:
            args.report.write_text(text)
        if args.markdown and value['complete']:
            args.markdown.write_text(markdown_report(value['report']))
        print(text, end='')
        return 0 if value['complete'] else 1
    except Exception:
        print('holdout_run_failed', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
