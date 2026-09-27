#!/usr/local/bin/python3
"""Authorized experiment-4 p6 development sample; durable 650-attempt round cap."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import time
if __package__ in (None,''):sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from back.daily_jev import freeze_plan,run_plan,report
from back.daily_jev_client import DailyJevClient,RoundBudget
from back.daily_prompt import ROUND_CALL_LIMIT
from back.daily_store import DailyStore
from back.data import DataError
from back.experiment import canonical
from back.store import DEFAULT_DB
from scripts.run_dev import exclusive_run

APPROVED_SAMPLE_SHA256='c97a1e113f8669095cd96fa04cd9c8bd86c41676ca8601897ca90f3bd52dd24f'


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--db',type=Path,default=DEFAULT_DB)
    p.add_argument('--experiment',type=int,choices=[4],default=4)
    p.add_argument('--split',choices=['dev'],default='dev')
    p.add_argument('--execute',action='store_true')
    p.add_argument('--max-calls',type=int,default=ROUND_CALL_LIMIT)
    p.add_argument('--report',type=Path)
    a=p.parse_args(argv)
    if not 0<=a.max_calls<=ROUND_CALL_LIMIT:raise DataError('invalid_p6_max_calls')
    if not a.execute:
        print(json.dumps(dict(execute=False,method='jev_ind',http_calls=0)));return 0
    began=time.monotonic()
    def progress(value):print(json.dumps(value,ensure_ascii=False),file=sys.stderr,flush=True)
    with exclusive_run(a.db),DailyStore(a.db) as store:
        plan=freeze_plan(store,experiment_id=a.experiment,split=a.split,progress=progress)
        sample_hash=hashlib.sha256(canonical(plan['definition']['days']).encode()).hexdigest()
        if plan['sample_count']!=576 or sample_hash!=APPROVED_SAMPLE_SHA256:raise DataError('p6_approved_sample_changed')
        budget=RoundBudget(plan['plan_hash'],max_calls=a.max_calls)
        client=DailyJevClient(budget)
        result=run_plan(store,client,plan,progress=progress)
        scored=report(store)
        scored['execution']=dict(**result,**budget.summary(),elapsed_seconds=time.monotonic()-began)
        if a.report:
            a.report.parent.mkdir(parents=True,exist_ok=True)
            a.report.write_text(json.dumps(scored,ensure_ascii=False,indent=2)+'\n')
        print(json.dumps(scored,ensure_ascii=False,indent=2))
    return int(scored['missing_days']!=0)

if __name__=='__main__':raise SystemExit(main())
