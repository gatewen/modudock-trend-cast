#!/usr/local/bin/python3
"""Explicitly opt in to stage A only; fixed 500 timestamps, no automatic B."""
import argparse
import json
from pathlib import Path
import sys
import time
if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from back.move import run_stage_a
from back.store import DEFAULT_DB, Store
from scripts.run_dev import CallBudget, DevClient, exclusive_run


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', type=Path, default=DEFAULT_DB)
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--max-calls', type=int, default=600)
    args = parser.parse_args(argv)
    budget = CallBudget(args.max_calls)
    if not args.execute:
        print(json.dumps({'stage': 'A', 'execute': False, 'http_calls': 0}))
        return 0
    started = time.monotonic()
    with exclusive_run(args.db), Store(args.db) as store:
        client = DevClient(budget)
        result = run_stage_a(store, client, execute=True,
            progress=lambda p: print(json.dumps(p), flush=True))
    result.update(http_calls=budget.calls, retries=budget.retries,
                  elapsed_seconds=time.monotonic()-started, errors=dict(client.errors))
    print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
    return int(result['missing'] != 0)


if __name__ == '__main__':
    raise SystemExit(main())
