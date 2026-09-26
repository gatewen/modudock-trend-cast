#!/usr/local/bin/python3
"""Read-only report. This CLI has no reveal flag and makes no API requests."""
import argparse
import json
from pathlib import Path
import sys

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from back.report import build_report, markdown_report
from back.store import DEFAULT_DB, Store


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', type=Path, default=DEFAULT_DB)
    parser.add_argument('--experiment', type=int)
    parser.add_argument('--dev-only', action='store_true', help='Never include even an already-revealed holdout')
    parser.add_argument('--format', choices=('json', 'markdown'), default='json')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args(argv)
    if not args.db.is_file():
        print('database_missing', file=sys.stderr)
        return 2
    try:
        if args.output and (args.output.resolve() == args.db.resolve()
                            or (args.output.exists() and args.output.samefile(args.db))):
            print('output_is_database', file=sys.stderr)
            return 2
        with Store(args.db, readonly=True) as store:
            result = build_report(store, experiment_id=args.experiment, dev_only=args.dev_only)
        text = markdown_report(result) if args.format == 'markdown' else json.dumps(
            result, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + '\n'
        if args.output:
            args.output.write_text(text)
        else:
            print(text, end='')
        return 0
    except Exception:
        print('report_failed', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
