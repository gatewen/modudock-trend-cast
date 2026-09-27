#!/usr/local/bin/python3
"""Daily data only. --execute enables Fugle/TWSE/FinMind, never Jev."""
import argparse
import json
from pathlib import Path
import sys
if __package__ in (None,''):sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from back.daily_store import DailyStore
from back.daily_sync import START, END, synchronize
from back.store import DEFAULT_DB
from scripts.run_dev import exclusive_run

def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db',type=Path,default=DEFAULT_DB)
    parser.add_argument('--start',default=START)
    parser.add_argument('--end',default=END)
    parser.add_argument('--execute',action='store_true')
    parser.add_argument('--refresh',action='store_true')
    args=parser.parse_args(argv)
    if not args.execute:
        print(json.dumps({'execute':False,'jev_http_calls':0}));return 0
    with exclusive_run(args.db),DailyStore(args.db) as store:
        result=synchronize(store,start=args.start,end=args.end,refresh=args.refresh,
                           progress=lambda p:print(json.dumps(p),flush=True))
    print(json.dumps(result),flush=True)
    return 0

if __name__=='__main__':raise SystemExit(main())
