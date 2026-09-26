#!/usr/local/bin/python3
"""Read-only development comparison; no scoring imports, bootstrap or selection."""
from collections import Counter
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import sqlite3

ROOT = Path(__file__).resolve().parents[2]
METHODS = ('jev', 'always_flat', 'majority', 'momentum', 'reversal')
LABELS = ('up', 'flat', 'down')


def main():
    output = ROOT / 'docs/verification'
    source = json.loads((output / 'p2-source.json').read_text())
    with sqlite3.connect((ROOT / 'data/trendcast.sqlite3').as_uri() + '?mode=ro', uri=True) as db:
        db.row_factory = sqlite3.Row
        db.execute('BEGIN')
        rows = {i: dict(db.execute('SELECT * FROM experiments WHERE id=?', (i,)).fetchone()) for i in (1, 2)}
        assert rows[1] == source['source_experiment'], 'p1 experiment changed'
        assert (rows[1]['prompt_version'], rows[2]['prompt_version']) == ('p1', 'p2')
        for key in ('config_json', 'dev_start', 'dev_end', 'hold_start', 'hold_end',
                    'threshold_permille', 'feature_version', 'model'):
            assert rows[1][key] == rows[2][key], 'settings mismatch'
        start, end = rows[1]['dev_start'], rows[1]['dev_end']
        outcomes, predictions = {}, {}
        before = hashlib.sha256()
        for table in ('predictions', 'outcomes'):
            before.update(table.encode())
            for item in db.execute(f'SELECT * FROM {table} WHERE experiment_id=1 AND substr(t,1,10) BETWEEN ? AND ? ORDER BY t' + (',method' if table == 'predictions' else ''), (start, end)):
                before.update((json.dumps(dict(item), sort_keys=True, ensure_ascii=False) + '\n').encode())
        assert before.hexdigest() == source['p1_dev_records_sha256'], 'p1 development changed'
        for experiment in (1, 2):
            outcomes[experiment] = {r['t']: (r['label'], r['close_t'], r['close_end']) for r in db.execute(
                'SELECT * FROM outcomes WHERE experiment_id=? AND substr(t,1,10) BETWEEN ? AND ?', (experiment, start, end))}
            predictions[experiment] = {m: {} for m in METHODS}
            for r in db.execute('''SELECT method,t,answer,probs_json,input_hash,input_json FROM predictions
                    WHERE experiment_id=? AND substr(t,1,10) BETWEEN ? AND ?''', (experiment, start, end)):
                predictions[experiment][r['method']][r['t']] = dict(r)
        assert outcomes[1] == outcomes[2], 'development outcomes differ'
        baseline_matches = {}
        for method in METHODS[1:]:
            assert predictions[1][method] == predictions[2][method], 'baseline mismatch'
            baseline_matches[method] = {'identical': True, 'n': len(predictions[2][method])}
        common = set(outcomes[2])
        for method in METHODS:
            common.intersection_update(predictions[2][method])
        common.intersection_update(predictions[1]['jev'])
        assert len(common) == source['source_dev_eligible'], 'development answers incomplete'

        def metrics(experiment, method):
            total, correct = Decimal(0), 0
            for t in sorted(common):
                p = predictions[experiment][method][t]
                truth = outcomes[experiment][t][0]
                probabilities = json.loads(p['probs_json'], parse_float=Decimal)
                correct += p['answer'] == truth
                total += sum((Decimal(probabilities[label]) - int(truth == label)) ** 2 for label in LABELS)
            return {'n': len(common), 'correct': correct, 'accuracy': correct / len(common),
                    'brier': float(total / len(common))}

        choices = Counter(predictions[2]['jev'][t]['answer'] for t in common)
        # Only activity metadata, never holdout outcomes or market data.
        assert db.execute("SELECT 1 FROM runs WHERE experiment_id=2 AND split!='dev' LIMIT 1").fetchone() is None
        assert db.execute('SELECT 1 FROM reveals WHERE experiment_id IN (1,2) LIMIT 1').fetchone() is None
        result = {'experiment_id': 2, 'prompt_version': 'p2', 'split': 'dev',
            'dev_start': start, 'dev_end': end, 'common_n': len(common),
            'methods': {m: metrics(2, m) for m in METHODS},
            'jev_choice': {label: {'n': choices[label], 'share': choices[label] / len(common)} for label in LABELS},
            'baseline_comparison_with_experiment_1': baseline_matches,
            'p1_p2_common_development': {'p1': metrics(1, 'jev'), 'p2': metrics(2, 'jev')},
            'p1_development_unchanged': True, 'development_outcomes_identical': True,
            'same_split_and_settings_except_prompt': True, 'holdout_not_run_or_revealed': True}
    (output / 'p2-dev-results.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
