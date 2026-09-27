#!/usr/local/bin/python3
"""Read-only, dates-only forward preview. Never runs or reveals any method."""
import argparse
import json
from pathlib import Path
import sys

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from back.forward import discover
from back.store import DEFAULT_DB, Store


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', type=Path, default=DEFAULT_DB)
    args = parser.parse_args(argv)
    with Store(args.db, readonly=True) as store:
        store.db.execute('BEGIN')
        result = discover(store, 1)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
