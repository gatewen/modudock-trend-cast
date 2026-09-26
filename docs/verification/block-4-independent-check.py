#!/usr/local/bin/python3
"""Run from trend-cast/: independent stdlib/SQL check, development rows only.

Does not import the scoring implementation, call any API or mutate the database.
The input probabilities and outcomes are the already-committed development data.
"""
from collections import Counter, defaultdict
from decimal import Decimal
import json
import math
from pathlib import Path
import random
import sqlite3

ROOT = Path(__file__).resolve().parents[2]
report = json.loads((ROOT / 'docs/verification/block-4-real-report.json').read_text())
assert 'holdout' not in report
db = sqlite3.connect((ROOT / 'data/block-3-real.sqlite3').as_uri() + '?mode=ro', uri=True)
db.row_factory = sqlite3.Row
start, end = db.execute('SELECT dev_start,dev_end FROM experiments WHERE id=1').fetchone()
names = ('jev', 'always_flat', 'majority', 'momentum', 'reversal')
labels = ('up', 'flat', 'down')
data = {name: {} for name in names}
for row in db.execute('''SELECT p.method,p.t,p.answer,p.probs_json,o.label FROM predictions p
    JOIN outcomes o ON p.experiment_id=o.experiment_id AND p.t=o.t
    WHERE p.experiment_id=1 AND substr(p.t,1,10) BETWEEN ? AND ?''', (start, end)):
    if row['method'] not in data:
        continue
    probabilities = json.loads(row['probs_json'], parse_float=Decimal, parse_int=Decimal)
    loss = sum((probabilities[label] - int(row['label'] == label)) ** 2 for label in labels)
    data[row['method']][row['t']] = (row['label'], row['answer'], float(loss), int(row['label'] == row['answer']))

def equal(a, b):
    assert math.isclose(a, b, rel_tol=1e-12, abs_tol=1e-12), (a, b)

for method, points in data.items():
    calculated = report['dev']['methods'][method]
    n = len(points)
    k = sum(p[3] for p in points.values())
    assert calculated['n'] == n
    assert calculated['correct'] == k
    equal(calculated['accuracy'], k/n)
    equal(calculated['brier'], math.fsum(p[2] for p in points.values()) / n)
    # Invert the binomial score inequality as a quadratic, independently of the
    # implementation's Wilson center/radius form.
    z2 = 1.959963984540054 ** 2
    a, b, c = n + z2, -(2*k + z2), k*k/n
    disc = math.sqrt(b*b - 4*a*c)
    for expected, actual in zip(((-b-disc)/(2*a), (-b+disc)/(2*a)), calculated['accuracy_wilson95']):
        equal(expected, actual)
    matrix = Counter((p[0], p[1]) for p in points.values())
    for truth in labels:
        for choice in labels:
            assert calculated['confusion_matrix'][truth][choice] == matrix[(truth, choice)]
    for label in labels:
        tp = matrix[(label, label)]
        predicted = sum(matrix[(truth, label)] for truth in labels)
        actual = sum(matrix[(label, choice)] for choice in labels)
        for key, count in (('precision', predicted), ('recall', actual)):
            value = calculated['per_class'][label][key]
            assert value is None if count == 0 else math.isclose(value, tp/count)

common = sorted(set.intersection(*(set(points) for points in data.values())))
best = min(names[1:], key=lambda name: sum(data[name][t][2] for t in common)/len(common))
comparison = report['dev']['comparison']
assert best == comparison['baseline']
assert len(common) == comparison['n']
by_day = defaultdict(list)
for stamp in common:
    by_day[stamp[:10]].append(stamp)
days = sorted(by_day)
deltas = {stamp: (data['jev'][stamp][2] - data[best][stamp][2],
                  data['jev'][stamp][3] - data[best][stamp][3]) for stamp in common}
equal(sum(pair[0] for pair in deltas.values())/len(common), comparison['brier_difference'])
equal(sum(pair[1] for pair in deltas.values())/len(common), comparison['accuracy_difference'])
rng = random.Random(20260927)
brier, accuracy = [], []
for _ in range(2000):
    # Materialize every paired point in every sampled day, including duplicate
    # draws. This does not reuse the scoring module's block-total optimization.
    sample = []
    for _ in days:
        sample.extend(by_day[days[rng.randrange(len(days))]])
    brier.append(math.fsum(deltas[t][0] for t in sample)/len(sample))
    accuracy.append(sum(deltas[t][1] for t in sample)/len(sample))
for values, key in ((brier, 'brier_difference_ci95'), (accuracy, 'accuracy_difference_ci95')):
    values.sort()
    bounds = []
    for q in (.025, .975):
        index = (len(values)-1)*q
        fraction, low = math.modf(index)
        bounds.append(values[int(low)]*(1-fraction) + values[math.ceil(index)]*fraction)
    for expected, actual in zip(bounds, comparison['bootstrap'][key]):
        equal(expected, actual)
assert db.total_changes == 0
db.close()
print(json.dumps({'status': 'passed', 'split': 'dev', 'n': len(common),
    'best_baseline': best, 'checks': ['accuracy', 'Wilson quadratic inversion',
    'confusion matrix', 'precision/recall', 'Decimal multiclass Brier',
    'common intersection', 'baseline selection', 'paired day bootstrap with materialized points'],
    'database_writes': False}, ensure_ascii=False, indent=2))
