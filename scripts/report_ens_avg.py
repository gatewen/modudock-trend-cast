#!/usr/local/bin/python3
"""Read existing experiment 4 development forecasts; no DB writes or network."""
import argparse
import json
from pathlib import Path
import sys
if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from back.daily_ensemble import development_report, markdown
from back.daily_store import DailyStore
from back.evolution_run import ForbiddenClient
from back.store import DEFAULT_DB


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', type=Path, default=DEFAULT_DB)
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args(argv)
    guard = ForbiddenClient()
    with guard.guard(), DailyStore(args.db, readonly=True) as store:
        store.db.execute('BEGIN')  # One consistent read snapshot.
        report = development_report(store)
    report['network_attempts'] = guard.calls
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    args.report.with_suffix('.md').write_text(markdown(report))
    print(markdown(report), end='')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
