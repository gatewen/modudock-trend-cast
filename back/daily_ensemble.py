"""SPEC 16.1A: read-only development ensemble over existing walk-forward rows.

The predictor receives only the three forecasts at (experiment, H, t). Outcomes
are read separately by the scorer; no fitting, weighting or future lookup occurs.
Keep this research extension outside experiment 4's frozen method registry.
"""
import hashlib
import json
import math

from .daily_config import LABELS
from .daily_experiment import load_experiment
from .daily_models import prediction
from .daily_replay import DEV_END, DEV_START, HORIZONS, horizon
from .daily_run import prepare_development, development_records
from .daily_score import paired_blocks
from .data import DataError
from .experiment import canonical
from .score import ScoredPoint, metrics

COMPONENTS = ('majority', 'vol_prior_d', 'ind_logit')


def average_current(current):
    if set(current) != set(COMPONENTS):
        raise DataError('ensemble_requires_three_current_predictions')
    for method in COMPONENTS:
        p = current[method]
        if p.method != method or any(type(v) not in (int, float) for v in p.probabilities.values()):
            raise DataError('ensemble_invalid_component')
        if prediction(method, p.probabilities).answer != p.answer:
            raise DataError('ensemble_invalid_component')
    probs = {label: math.fsum(current[m].probabilities[label] for m in COMPONENTS) / 3
             for label in LABELS}
    return prediction('ens_avg', probs)


def current_predictions(store, experiment_id, H, point):
    """Exactly t, not the latest row or a complete-development fitted model."""
    horizon(H)
    if experiment_id != 4 or not DEV_START <= point.day <= DEV_END:
        raise DataError('ensemble_dev_only')
    current = {}
    for r in store.db.execute('''SELECT method,choice,probabilities_json,input_hash
        FROM d_predictions WHERE experiment_id=? AND H=? AND day=?
        AND method IN ('majority','vol_prior_d','ind_logit')''',
        (experiment_id, H, point.day)):
        p = prediction(r['method'], json.loads(r['probabilities_json']))
        if r['input_hash'] != point.digest or p.answer != r['choice']:
            raise DataError('ensemble_component_mismatch')
        current[r['method']] = p
    # Missing components stay missing; never reweight the remaining methods.
    return current


def development_report(store, experiment_id=4, *, split='dev'):
    if split != 'dev':
        raise DataError('ensemble_dev_only')
    row = load_experiment(store, experiment_id)
    config = row['config']
    first, last = config['dev_start'], config['dev_end']
    if not DEV_START <= first <= last <= DEV_END:
        raise DataError('ensemble_dev_only')
    calendar = tuple(r[0] for r in store.db.execute(
        'SELECT day FROM d_calendar WHERE day BETWEEN ? AND ? ORDER BY day', (first, last)))
    points = prepare_development(store, row)
    report = dict(experiment=experiment_id, split='dev', method='ens_avg',
                  components=COMPONENTS, data_digest=row['data_digest'],
                  dev_digest=row['dev_digest'], dev_start=first, dev_end=last,
                  http_calls=0, horizons={})
    for H in HORIZONS:
        forecasts, references, audit = {}, {}, []
        missing = {m: 0 for m in COMPONENTS}
        # Finish all forecasts before obtaining any outcomes for scoring.
        for point in points:
            current = current_predictions(store, experiment_id, H, point)
            for m in COMPONENTS:
                missing[m] += int(m not in current)
            if set(current) != set(COMPONENTS):
                continue
            forecasts[point.day] = average_current(current)
            references[point.day] = current['majority']
            audit.append(dict(day=point.day, H=H, input_hash=point.digest,
                              components={m:current[m].probabilities for m in COMPONENTS},
                              probabilities=forecasts[point.day].probabilities,
                              answer=forecasts[point.day].answer))
        _, records, _ = development_records(store, row, points, H)
        expected = {r.frame.day:r for r in records}
        outcomes = {r['day']:r for r in store.db.execute('''SELECT day,end_day,label FROM d_outcomes
            WHERE experiment_id=? AND H=? AND day BETWEEN ? AND ? AND end_day<=?''',
            (experiment_id, H, first, last, last))}
        if set(outcomes) - set(expected) or any(
                r['label'] != expected[d].truth or r['end_day'] != expected[d].end_day
                for d, r in outcomes.items()):
            raise DataError('ensemble_outcome_mismatch')
        common = sorted(set(forecasts) & set(outcomes))
        def scored(predictions):
            return {d:ScoredPoint(d, outcomes[d]['label'], predictions[d].answer,
                                 predictions[d].probabilities) for d in common}
        candidate, reference = scored(forecasts), scored(references)
        comparison = paired_blocks(candidate, reference, calendar, repetitions=2000, seed=20260927)
        complete = bool(expected) and len(common) == len(expected) and len(forecasts) == len(points)
        shortlisted = bool(complete and comparison['ci95'] and comparison['ci95'][1] < 0)
        report['horizons'][str(H)] = dict(
            threshold=config['thresholds'][str(H)], predictable=len(points), expected_scorable=len(expected),
            n=len(common), complete=complete, coverage=len(common)/len(expected) if expected else None,
            missing_components=missing, missing_outcomes=len(set(expected)-set(outcomes)),
            forecast_digest=hashlib.sha256(canonical(audit).encode()).hexdigest(),
            ens_avg=metrics(candidate.values()), majority=metrics(reference.values()),
            brier_vs_majority=comparison, shortlisted=shortlisted,
            verdict=('入圍：值得再驗證，尚非證明有效' if shortlisted else
                     '未入圍' if complete else '結果不完整，不下結論'))
    return report


def markdown(report):
    lines = ['# ens_avg：實驗 4 開發段', '',
             'SPEC §16.1A；三個當下 walk-forward 機率等權平均。差值為 ens_avg − majority。',
             '完整開發交易日曆切不重疊 20 日區塊、保留尾塊；2000 次、seed=20260927。',
             '開發段入圍只代表值得再驗證；保留段未使用。', '',
             '| 天期 | n／可評分 | ens_avg Brier | majority Brier | 差值 | 95% 區間 | 判讀 |',
             '|---|---:|---:|---:|---:|---|---|']
    for H, p in report['horizons'].items():
        c = p['brier_vs_majority']
        def number(v): return f'{v:.9f}' if v is not None else '無樣本'
        ci = '[' + ', '.join(number(v) for v in c['ci95']) + ']' if c['ci95'] else '無樣本'
        lines.append(f'| {H} | {p["n"]}／{p["expected_scorable"]} | {number(p["ens_avg"]["brier"])} | '
                     f'{number(p["majority"]["brier"])} | {number(c["difference"])} | {ci} | {p["verdict"]} |')
    return '\n'.join(lines) + '\n'
