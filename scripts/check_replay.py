#!/usr/local/bin/python3
"""Temporary dev-only replay audit. Never emit holdout counts, dates or labels."""
import argparse
from collections import Counter
import json
from pathlib import Path
import sqlite3
import sys

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from back.data import DataError
from back.replay import LABELS, Replay, temporary_plan
from back.store import DEFAULT_DB, Store


def audit_development(store, *, symbol='2330', threshold_permille=3):
    """User-authorized temporary split until block 3 creates frozen experiments."""
    store.db.execute('BEGIN')
    try:
        # Once experiments exist their frozen split owns disclosure. This
        # temporary audit must not silently repartition and reveal their holdout.
        if store.db.execute('SELECT 1 FROM experiments LIMIT 1').fetchone():
            raise DataError('temporary_audit_requires_no_experiments')
        plan = temporary_plan(store, symbol, threshold_permille)
        replay = Replay(store, plan)
        candidate = predictable = scorable = joint = 0
        counts, reasons = Counter(), Counter()
        # Do not traverse holdout points, even to generate unpublished totals.
        for t in replay.candidates('dev'):
            point = replay.prepare(t)
            outcome = replay.outcome(point)
            candidate += 1
            predictable += int(point.predictable)
            scorable += int(outcome.scorable)
            if point.predictable and outcome.scorable:
                joint += 1
                counts[outcome.label] += 1
            if not point.predictable:
                reasons[point.reason] += 1
        result = {
            'scope': 'dev_only', 'mode': 'temporary_split_not_frozen_experiment',
            'split_rule': 'first floor(70% of trading days); initial warmup days retained as candidates',
            'feature_version': plan.feature_version, 'threshold_permille': plan.threshold_permille,
            'dev_start': plan.dev_start, 'dev_end': plan.dev_end,
            'dev_trading_days': len(plan.days_for('dev')),
            'candidate_points': candidate, 'predictable_points': predictable,
            'scorable_points': scorable, 'predictable_and_scorable_points': joint,
            'unpredictable_reasons': dict(sorted(reasons.items())),
            'label_cohort': 'predictable AND scorable development points only',
            'labels': {label: {'count': counts[label],
                                'percent': round(counts[label] * 100 / joint, 4) if joint else None}
                       for label in LABELS},
        }
        store.db.commit()
        return result
    except BaseException:
        store.db.rollback()
        raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', type=Path, default=DEFAULT_DB)
    parser.add_argument('--symbol', default='2330')
    parser.add_argument('--threshold-permille', type=int, default=3)
    args = parser.parse_args(argv)
    if not args.db.is_file():
        print('database_missing', file=sys.stderr)
        return 2
    try:
        with Store(args.db, readonly=True) as store:
            result = audit_development(store, symbol=args.symbol, threshold_permille=args.threshold_permille)
        print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
        return 0
    except (DataError, sqlite3.Error, ValueError, OSError):
        print('replay_audit_failed', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
