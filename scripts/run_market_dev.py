#!/usr/local/bin/python3
"""Freeze separately, fit six predeclared methods, and report only development."""
import argparse
from concurrent.futures import ProcessPoolExecutor
import json
from pathlib import Path
import sys

if __package__ in (None,''):sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

from back.daily_store import DailyStore
from back.evolution_run import ForbiddenClient
from back.market_config import PROTOCOL
from back.market_store import ensure_schema,freeze,load_snapshot
from back.market_run import run_horizon,development_report,markdown
from scripts.run_dev import exclusive_run

ROOT=Path(__file__).resolve().parents[1]


def worker(task):
    path,H=task;guard=ForbiddenClient()
    with guard.guard(),DailyStore(path) as store:
        result=run_horizon(store,H,progress=lambda p:print(json.dumps(p),flush=True))
    result['network_attempts']=guard.calls
    return result


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute',action='store_true')
    parser.add_argument('--db',type=Path,default=ROOT/'data/trendcast.sqlite3')
    parser.add_argument('--report',type=Path,default=ROOT/'data/market-development.json')
    parser.add_argument('--jobs',type=int,choices=(1,2,3),default=3)
    args=parser.parse_args(argv)
    if not args.execute:
        print('Preview: experiment 4 development only; 6 methods × 3 horizons; 0 HTTP. Use --execute.');return 0
    guard=ForbiddenClient()
    with exclusive_run(args.db),guard.guard():
        with DailyStore(args.db) as store:
            ensure_schema(store)
            if store.db.execute('SELECT 1 FROM market_experiments').fetchone():load_snapshot(store,PROTOCOL)
            else:freeze(store,PROTOCOL)
        tasks=[(args.db,H) for H in (3,7,14)]
        if args.jobs==1:execution=[worker(task) for task in tasks]
        else:
            with ProcessPoolExecutor(max_workers=args.jobs) as pool:execution=list(pool.map(worker,tasks))
        with DailyStore(args.db,readonly=True) as store:report=development_report(store)
        report['execution']=execution;report['network_attempts']=guard.calls+sum(r['network_attempts'] for r in execution)
        args.report.parent.mkdir(parents=True,exist_ok=True)
        args.report.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
        args.report.with_suffix('.md').write_text(markdown(report))
        print(json.dumps(dict(status='complete',report=str(args.report),execution=execution,network_attempts=report['network_attempts']),ensure_ascii=False))
    return 0


if __name__=='__main__':raise SystemExit(main())
