#!/usr/local/bin/python3
"""Create frozen daily experiment 4 and run development without any network."""
import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
import json
from pathlib import Path
import sys
import time
if __package__ in (None,''):sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from back.daily_experiment import create_experiment, ensure_schema, load_experiment
from back.daily_run import run_horizon
from back.daily_score import development_report, markdown
from back.daily_store import DailyStore
from back.data import DataError
from back.evolution_run import ForbiddenClient
from back.store import DEFAULT_DB
from scripts.run_dev import exclusive_run


def worker(path, H):
    guard=ForbiddenClient()
    def progress(value): print(json.dumps(dict(progress=value)),file=sys.stderr,flush=True)
    with guard.guard(),DailyStore(path) as store:
        result=run_horizon(store,4,H,progress=progress)
    result['network_attempts']=guard.calls
    return result


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db',type=Path,default=DEFAULT_DB)
    parser.add_argument('--experiment',type=int,default=4)
    parser.add_argument('--split',choices=['dev'],default='dev')
    parser.add_argument('--execute',action='store_true')
    parser.add_argument('--jobs',type=int,choices=[1,2,3],default=3)
    parser.add_argument('--report',type=Path)
    args=parser.parse_args(argv)
    if args.experiment!=4: raise DataError('daily_experiment_4_only')
    if not args.execute:
        print(json.dumps(dict(execute=False,experiment=4,split='dev',http_calls=0)))
        return 0
    began=time.monotonic();guard=ForbiddenClient()
    with exclusive_run(args.db):
        with guard.guard(),DailyStore(args.db) as store:
            ensure_schema(store)
            exists=store.db.execute('SELECT 1 FROM d_experiments WHERE id=4').fetchone()
            row=load_experiment(store) if exists else create_experiment(store)
            print(json.dumps(dict(experiment=4,frozen=True,data_digest=row['data_digest'])),file=sys.stderr,flush=True)
        if args.jobs==1: runs=[worker(args.db,H) for H in (3,7,14)]
        else:
            with ProcessPoolExecutor(max_workers=args.jobs) as pool:
                futures=[pool.submit(worker,args.db,H) for H in (3,7,14)]
                runs=[future.result() for future in as_completed(futures)]
        with guard.guard(),DailyStore(args.db,readonly=True) as store:
            report=development_report(store)
        report['execution']=dict(runs=sorted(runs,key=lambda r:r['H']),elapsed_seconds=time.monotonic()-began,
                                 http_calls=0,network_attempts=guard.calls+sum(r['network_attempts'] for r in runs))
        if args.report:
            args.report.parent.mkdir(parents=True,exist_ok=True)
            args.report.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
            args.report.with_suffix('.md').write_text(markdown(report))
        print(json.dumps(report['execution'],ensure_ascii=False,indent=2))
    return 0

if __name__=='__main__':raise SystemExit(main())
