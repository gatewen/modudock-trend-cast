#!/usr/local/bin/python3
"""Offline vol_prior only on the enrolled experiment 1 forward cohort. No reveal."""
import argparse
import json
from pathlib import Path
import sys
if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from back.evolution_forward import run_forward_vol
from back.evolution_run import ForbiddenClient
from back.store import DEFAULT_DB, Store
from scripts.run_dev import exclusive_run

def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db',type=Path,default=DEFAULT_DB)
    parser.add_argument('--experiment',type=int,choices=(1,),default=1)
    parser.add_argument('--execute',action='store_true')
    args=parser.parse_args(argv)
    if not args.execute: parser.error('--execute required')
    if not args.db.is_file(): parser.error('database_missing')
    with exclusive_run(args.db), Store(args.db) as store:
        result=run_forward_vol(store,experiment_id=args.experiment,client=ForbiddenClient())
    print(json.dumps(result))
    return 0

if __name__=='__main__': raise SystemExit(main())
