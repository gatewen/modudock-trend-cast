#!/usr/local/bin/python3
"""Run SPEC 14.3 locally on experiment 1 development only; network forbidden."""
import argparse
import json
from pathlib import Path
import sys

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from back.data import DataError
from back.evolution_run import ForbiddenClient, run_development
from back.store import DEFAULT_DB, Store
from scripts.run_dev import exclusive_run


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', type=Path, default=DEFAULT_DB)
    parser.add_argument('--experiment', type=int, choices=(1,), default=1)
    parser.add_argument('--split', choices=('dev',), default='dev')
    parser.add_argument('--execute', action='store_true', help='Permit local prediction writes; HTTP is always forbidden')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args(argv)
    if not args.execute:
        parser.error('--execute is required for local writes')
    if not args.db.is_file():
        parser.error('database_missing')
    if args.output and (args.output.resolve() == args.db.resolve() or args.output.suffix.lower() != '.json'):
        parser.error('output_must_be_separate_json')
    try:
        with exclusive_run(args.db), Store(args.db) as store:
            result = run_development(store, experiment_id=args.experiment, split=args.split, client=ForbiddenClient())
    except DataError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    serialized = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False)
    if args.output:
        args.output.write_text(serialized + '\n')
    print(serialized)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
