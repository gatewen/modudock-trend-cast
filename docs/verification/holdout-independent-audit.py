#!/usr/local/bin/python3
"""Read-only audit after full reveal; no imports from the scoring implementation."""
from collections import Counter, defaultdict
from decimal import Decimal
import hashlib
import json
import math
from pathlib import Path
import random
import sqlite3

ROOT = Path(__file__).resolve().parents[2]
METHODS = ('jev', 'always_flat', 'majority', 'momentum', 'reversal')
LABELS = ('up', 'flat', 'down')


def close(left, right):
    assert math.isclose(left, right, abs_tol=1e-12), (left, right)


def main():
    output = ROOT / 'docs/verification'
    before = json.loads((ROOT / 'data/holdout-before.json').read_text())
    run = json.loads((output / 'holdout-run.json').read_text())
    assert run['complete'] and run['revealed'] and run['experiment_id'] == 1
    report = run['report']['holdout']
    with sqlite3.connect((ROOT / 'data/trendcast.sqlite3').as_uri() + '?mode=ro', uri=True) as db:
        db.row_factory = sqlite3.Row
        db.execute('BEGIN')
        row = dict(db.execute('SELECT * FROM experiments WHERE id=1').fetchone())
        start, end = row['hold_start'], row['hold_end']
        reveals = db.execute('SELECT * FROM reveals WHERE experiment_id=1').fetchall()
        assert len(reveals) == 1
        assert (reveals[0]['first_day'], reveals[0]['last_day'], reveals[0]['what']) == (start, end, 'all')
        overlap = db.execute('SELECT prior_overlap_days FROM reveal_context WHERE experiment_id=1').fetchone()[0]
        assert overlap == report['prior_overlap_days'] == 4
        for experiment in (1, 2):
            assert dict(db.execute('SELECT * FROM experiments WHERE id=?', (experiment,)).fetchone()) == before[str(experiment)]['experiment']
            for table, digest in before[str(experiment)]['tables'].items():
                if experiment == 1 and table == 'reveals':
                    continue
                sql = f'SELECT * FROM {table} WHERE experiment_id=?'
                if experiment == 1:
                    sql += " AND split='dev'" if table == 'runs' else " AND substr(t,1,10)<='" + row['dev_end'] + "'"
                records = [tuple(r) for r in db.execute(sql + ' ORDER BY rowid', (experiment,))]
                assert hashlib.sha256(json.dumps(records).encode()).hexdigest() == digest, (experiment, table)
        assert not db.execute("SELECT 1 FROM runs WHERE experiment_id=2 AND split='holdout'").fetchone()
        assert not db.execute('SELECT 1 FROM reveals WHERE experiment_id=2').fetchone()
        # Only the selected and already-revealed experiment is scored.
        truth = {r['t']: r['label'] for r in db.execute('''SELECT t,label FROM outcomes
            WHERE experiment_id=1 AND substr(t,1,10) BETWEEN ? AND ?''', (start, end))}
        predictions = {m: {} for m in METHODS}
        for record in db.execute('''SELECT method,t,answer,probs_json FROM predictions
            WHERE experiment_id=1 AND substr(t,1,10) BETWEEN ? AND ?''', (start, end)):
            predictions[record['method']][record['t']] = record
        common = set(truth)
        for values in predictions.values():
            common.intersection_update(values)
        assert len(common) == report['comparison']['n'] == report['predictable_and_scorable']
        assert report['comparison']['baseline'] == 'majority'
        losses, hits, methods = {}, {}, {}
        for method in METHODS:
            losses[method], hits[method] = {}, {}
            for t in sorted(common):
                p = predictions[method][t]
                probs = json.loads(p['probs_json'], parse_float=Decimal)
                losses[method][t] = sum((Decimal(probs[label]) - int(truth[t] == label)) ** 2 for label in LABELS)
                hits[method][t] = int(p['answer'] == truth[t])
            n, correct = len(common), sum(hits[method].values())
            brier = float(sum(losses[method].values()) / n)
            accuracy = correct / n
            z2 = 1.959963984540054 ** 2
            center = (correct + z2 / 2) / (n + z2)
            radius = math.sqrt(z2 * (n * accuracy * (1 - accuracy) + z2 / 4)) / (n + z2)
            interval = [center - radius, center + radius]
            values = report['methods'][method]
            assert correct == values['correct'] and n == values['n']
            close(brier, values['brier']); close(accuracy, values['accuracy'])
            for a, b in zip(interval, values['accuracy_wilson95']): close(a, b)
            assert values['coverage'] == 1 and values['eligible_missing'] == 0
            methods[method] = dict(n=n, correct=correct, accuracy=accuracy, brier=brier, wilson95=interval)
        daily = defaultdict(list)
        for t in sorted(common):
            daily[t[:10]].append(losses['jev'][t] - losses['majority'][t])
        days = sorted(daily)
        blocks = [(len(daily[day]), float(sum(daily[day]))) for day in days]
        rng = random.Random(20260927)
        draws = []
        for _ in range(2000):
            sampled = [blocks[rng.randrange(len(blocks))] for _ in blocks]
            draws.append(math.fsum(s for n, s in sampled) / sum(n for n, s in sampled))
        draws.sort()
        def quantile(q):
            position = (len(draws) - 1) * q
            lower = math.floor(position)
            return draws[lower] + (draws[math.ceil(position)] - draws[lower]) * (position - lower)
        interval = [quantile(.025), quantile(.975)]
        delta = float(sum(losses['jev'][t] - losses['majority'][t] for t in common) / len(common))
        close(delta, report['comparison']['brier_difference'])
        for a, b in zip(interval, report['comparison']['bootstrap']['brier_difference_ci95']): close(a, b)
        choices = Counter(predictions['jev'][t]['answer'] for t in common)
        for label in LABELS: assert choices[label] == report['jev_description']['classes'][label]['choice_count']
        final_time = max(r[0] for r in db.execute("SELECT finished_at FROM runs WHERE experiment_id=1 AND split='holdout'"))
        assert final_time <= reveals[0]['revealed_at']
        result = dict(experiment_id=1, prompt_version='p1', split='holdout', first_day=start,
            last_day=end, common_n=len(common), methods=methods, jev_choices=dict(choices),
            trading_days=len(days), prior_overlap_days=overlap, brier_difference=delta,
            brier_difference_ci95=interval, repetitions=2000, seed=20260927,
            http_calls=run['http_calls'], retries=run['retries'], errors=run['errors'],
            all_development_unchanged=True, experiment_2_unchanged=True,
            full_reveal_count=1, revealed_after_all_runs=True,
            statement=report['comparison']['statement'])
    (output / 'holdout-independent-results.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
