#!/usr/local/bin/python3
"""SPEC 16.5 one-time exam. Explicit execution; no Jev calls, no early scores."""
import argparse
import json
from pathlib import Path
import sys
if __package__ in (None,''):sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

from back.daily_hold_store import ensure_schema,freeze_models,install_market,verify_market
from back.daily_hold_run import predict_all,settle_and_reveal,report,markdown
from back.daily_incremental import attached
from back.daily_replay import HOLD_START,HOLD_END
from back.daily_reveal import revealed
from back.daily_sync import no_jev
from back.evolution_run import ForbiddenClient
from back.http_client import JsonClient
from back.market_sources import SOURCES,parse_bounded
from back.store import Store,DEFAULT_DB
from scripts.run_dev import exclusive_run


def fetch_market():
    http=JsonClient('api.finmindtrade.com');batches=[]
    for series,(dataset,symbol) in SOURCES.items():
        response=http.get('/api/v4/data',dict(dataset=dataset,data_id=symbol,start_date=HOLD_START,end_date=HOLD_END))
        batches.append(parse_bounded(response.payload,series,HOLD_START,HOLD_END))
    return batches


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--execute',action='store_true');p.add_argument('--db',type=Path,default=DEFAULT_DB)
    p.add_argument('--report',type=Path,required=True);p.add_argument('--report-only',action='store_true')
    args=p.parse_args(argv)
    if not args.execute and not args.report_only:
        print('Preview only: experiment 4, fixed models, all horizons, outcomes/reveal only after zero missing predictions.');return 0
    guard=ForbiddenClient();execution={}
    with exclusive_run(args.db):
        if args.execute:
            with Store(args.db) as base:
                store=attached(base);ensure_schema(store)
                with guard.guard():freeze_models(store)
                if not revealed(store):
                    if not store.db.execute('SELECT 1 FROM d_hold_market_snapshot').fetchone():
                        with no_jev():install_market(store,fetch_market())
                    with guard.guard():
                        verify_market(store)
                        execution=predict_all(store,progress=lambda v:print(json.dumps(v),flush=True))
                        execution['new_reveal']=settle_and_reveal(store)
        with guard.guard(),Store(args.db,readonly=True) as base:
            result=report(attached(base))
        result['execution']=execution;result['network_attempts_during_prediction_and_scoring']=guard.calls
        args.report.parent.mkdir(parents=True,exist_ok=True)
        args.report.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
        args.report.with_suffix('.md').write_text(markdown(result))
        print(json.dumps(dict(status='revealed',report=str(args.report),primary_comparisons=result['primary_comparisons'],promoted=result['promoted'],jev_http_calls=0),ensure_ascii=False))
    return 0


if __name__=='__main__':raise SystemExit(main())
