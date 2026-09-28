#!/usr/local/bin/python3
"""Fetch four FinMind series 2009..2021 only; no key or Jev call."""
import argparse
import json
from pathlib import Path
import sys
if __package__ in (None,''):sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from back.daily_experiment import load_experiment
from back.daily_store import DailyStore
from back.daily_sync import no_jev
from back.market_sources import SOURCES,MarketClient
from back.market_store import ensure_schema,write_batches,source_summary
from back.store import DEFAULT_DB
from scripts.run_dev import exclusive_run


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db',type=Path,default=DEFAULT_DB)
    parser.add_argument('--execute',action='store_true')
    args=parser.parse_args(argv)
    if not args.execute:
        print(json.dumps(dict(execute=False,jev_http_calls=0)));return 0
    with exclusive_run(args.db),no_jev(),DailyStore(args.db) as s:
        load_experiment(s);ensure_schema(s)
        if s.db.execute('SELECT 1 FROM market_experiments').fetchone():
            print(json.dumps(dict(status='frozen',sources=source_summary(s),jev_http_calls=0)));return 0
        client=MarketClient();batches=[]
        for series in SOURCES:
            b=client.fetch(series);batches.append(b)
            print(json.dumps(dict(series=series,n=len(b.rows),start=b.start,end=b.end)),flush=True)
        write_batches(s,batches)
        print(json.dumps(dict(status='written',sources=source_summary(s),jev_http_calls=0)))
    return 0


if __name__=='__main__':raise SystemExit(main())
