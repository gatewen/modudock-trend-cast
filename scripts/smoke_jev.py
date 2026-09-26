#!/usr/local/bin/python3
"""Explicit block-3 smoke: at most six development points, twenty total attempts.

Never runs on scratchpad/source directly. The persistent quota includes retries
and failed requests, and is consumed before each network attempt. No outcomes or
holdout statistics are read. Re-running supplements missing predictions only.
"""
import argparse
from collections import Counter
import fcntl
import json
import os
from pathlib import Path
import sqlite3
import statistics
import sys
import threading

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from back.data import DataError, local_time
from back.db_writer import DBWriter
from back.experiment import ActivityGate, create_experiment, load_plan
from back.http_client import ClientError
from back.jevcast import JevClient, JevRunner
from back.replay import Replay
from back.store import Store

ROOT = Path(__file__).resolve().parents[1]
COPY = ROOT / 'data' / 'block-3-real.sqlite3'
QUOTA = ROOT / 'data' / 'block-3-api-budget.jsonl'
REPORT = ROOT / 'docs' / 'verification' / 'block-3-real-smoke.json'


class AttemptBudget:
    def __init__(self, path=QUOTA):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()

    def consume(self):
        with self.lock, self.path.open('a+', encoding='utf-8') as file:
            fcntl.flock(file, fcntl.LOCK_EX)
            file.seek(0)
            # Append-only ledger: an interrupted append fails closed on the next
            # run; it can never look like a reset-to-zero truncated counter.
            entries = [json.loads(line)['attempts'] for line in file]
            if (any(type(n) is not int for n in entries)
                    or entries != list(range(1, len(entries) + 1)) or len(entries) >= 20):
                raise ClientError('smoke_budget_exhausted')
            file.write(json.dumps({'attempts': len(entries) + 1}) + '\n')
            file.flush()
            os.fsync(file.fileno())

    def used(self):
        return len(self.path.read_text().splitlines()) if self.path.exists() else 0


class MeasuredClient(JevClient):
    def __init__(self, budget):
        super().__init__(before_request=budget.consume)
        self.observations = {}
        self.errors = Counter()
        self.lock = threading.Lock()

    def predict(self, point, **kwargs):
        try:
            answer = super().predict(point, **kwargs)
        except ClientError as exc:
            with self.lock:
                self.errors[exc.code] += 1
            raise
        with self.lock:
            self.observations[point.t.isoformat()] = answer
        return answer


def prepare_copy(source):
    if COPY.exists():
        return
    source = Path(source).resolve(strict=True)
    COPY.parent.mkdir(exist_ok=True)
    with sqlite3.connect(source.as_uri() + '?mode=ro', uri=True) as original:
        with sqlite3.connect(COPY) as target:
            original.backup(target)


def prepare_experiment(store, gate):
    existing = store.db.execute('SELECT * FROM experiments ORDER BY id LIMIT 1').fetchone()
    if existing:
        return dict(existing)
    months = [r[0] for r in store.db.execute('SELECT DISTINCT substr(day,1,7) FROM daily ORDER BY 1')]
    final = {month for month in months if not store.should_fetch('2330', month)}
    days = [r[0] for r in store.db.execute("SELECT day FROM daily WHERE symbol='2330' ORDER BY day")
            if r[0][:7] in final]
    if len(days) < 27:
        raise DataError('missing_finalized_data')
    # The experiment constructor rejects any unfinalized gap in this range.
    return create_experiment(store, gate, start=days[25], end=days[-1])


def smoke_points(store, experiment_id):
    plan = load_plan(store, experiment_id)
    replay = Replay(store, plan)
    days = plan.days_for('dev')
    clocks = ('09:30', '10:00', '11:00', '12:00', '12:30', '13:00')
    points = [local_time(days[(len(days) - 1) * i // 5] + 'T' + clock + ':00+08:00')
              for i, clock in enumerate(clocks)]
    if not all(replay.prepare(point).predictable for point in points):
        raise DataError('smoke_point_not_predictable')
    return points


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=ROOT / 'data' / 'block-2-real.sqlite3')
    parser.add_argument('--execute', action='store_true', help='Send the six explicitly selected dev points')
    args = parser.parse_args(argv)
    if not args.execute:
        parser.error('--execute is required to make real API requests')
    if not os.environ.get('TYPESAFE_API_KEY'):
        print('missing_key', file=sys.stderr)
        return 2
    writer = runner = None
    try:
        prepare_copy(args.source)
        writer, gate = DBWriter(COPY), ActivityGate()
        row = writer.submit(lambda store: prepare_experiment(store, gate)).result(30)
        points = writer.submit(lambda store: smoke_points(store, row['id'])).result(30)
        budget = AttemptBudget()
        client = MeasuredClient(budget)
        runner = JevRunner(writer, gate, client=client)
        result = runner.start(row['id'], times=points).done.result(120)
        def committed_dev(store):
            return [dict(r) for r in store.db.execute('''SELECT t,answer,probs_json,model_reported
                FROM predictions WHERE experiment_id=? AND method='jev'
                AND substr(t,1,10) BETWEEN ? AND ? ORDER BY t''',
                (row['id'], row['dev_start'], row['dev_end'])) if r['t'] in {p.isoformat() for p in points}]
        rows = writer.submit(committed_dev).result(10)
        choices = Counter(r['answer'] for r in rows)
        probabilities = {label: [json.loads(r['probs_json'])[label] for r in rows]
                         for label in ('up', 'flat', 'down')}
        latencies = [client.observations[r['t']].latency_seconds
                     for r in rows if r['t'] in client.observations]
        def distribution(values):
            return {'n': len(values), 'min': min(values), 'median': statistics.median(values),
                    'mean': statistics.mean(values), 'max': max(values)} if values else {'n': 0}
        report = dict(experiment_id=row['id'], data_digest=row['data_digest'],
            dev_start=row['dev_start'], dev_end=row['dev_end'], split='dev',
            selected_points=6, attempts_total=budget.used(), current_run=result,
            choice_counts={label: choices[label] for label in probabilities},
            probabilities={label: distribution(values) for label, values in probabilities.items()},
            latency_seconds=distribution(latencies), errors=dict(client.errors),
            model_reported_counts=dict(Counter(r['model_reported'] or 'absent' for r in rows)))
        text = json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False)
        secret = os.environ['TYPESAFE_API_KEY']
        if secret in text:
            raise DataError('secret_output_guard')
        # Only aggregate committed development results are written to the repo.
        REPORT.write_text(text + '\n')
        print(text)
        return 0 if len(rows) == 6 else 1
    except Exception:
        print('smoke_failed', file=sys.stderr)
        return 2
    finally:
        if runner is not None:
            runner.close()
        if writer is not None:
            writer.close()


if __name__ == '__main__':
    raise SystemExit(main())
