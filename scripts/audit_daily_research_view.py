#!/usr/local/bin/python3
"""Compare the read-only product projections with reviewed round 1–3 reports."""
import json
from pathlib import Path
import sqlite3
import sys
import time
if __package__ in (None,''):sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from back.daily_store import DailyStore
from back.daily_view import DailyViews
from back.evolution_budget import LIMIT
from back.evolution_run import ForbiddenClient

ROOT=Path(__file__).resolve().parents[1]


def main(*,stem='evolve2-4'):
    out=ROOT/'docs/verification'
    ens=json.loads((out/'evolve2-1-ens-avg.json').read_text())
    market=json.loads((out/'evolve2-2-development.json').read_text())
    held=json.loads((out/'evolve2-3-holdout.json').read_text())
    result=dict(horizons={},http_calls=0)
    start=time.monotonic()
    with DailyStore(ROOT/'data/trendcast.sqlite3',readonly=True) as store,ForbiddenClient().guard():
        view=DailyViews()
        for H in (3,7,14):
            dev=view.handle(store,dict(op='daily_report',H=H))
            methods={r['method']:r for r in dev['methods']}
            assert len(methods)==25
            for method,expected in dict(market['horizons'][str(H)],ens_avg=ens['horizons'][str(H)]).items():
                metric=expected['ens_avg'] if method=='ens_avg' else expected['candidate']
                actual=methods[method]
                for key in ('n','accuracy','brier'):assert actual[key]==metric[key],(H,method,key)
                for key in ('difference','ci95'):assert actual[key]==expected['brier_vs_majority'][key],(H,method,key)
                assert actual['shortlisted']==expected['shortlisted'],(H,method)
            hold=view.handle(store,dict(op='daily_holdout',H=H))
            assert hold['state']=='used'
            assert hold['primary_comparisons']==held['primary_comparisons']
            assert {r['method']:{k:v for k,v in r.items() if k!='method'} for r in hold['descriptive']}==held['descriptive'][str(H)]
            result['horizons'][str(H)]=dict(development=dev,holdout=hold)
        assert store.db.total_changes==0
    with sqlite3.connect((ROOT/'data/evolve-2026-09-27-budget.sqlite3').as_uri()+'?mode=ro',uri=True) as db:
        result['jev_used']=db.execute("SELECT used FROM budget WHERE campaign='evolve/2026-09-27'").fetchone()[0]
    assert result['jev_used']==1222
    result.update(jev_limit=LIMIT,elapsed_seconds=round(time.monotonic()-start,3),reviewed_results_match=True)
    (out/(stem+'-view-audit.json')).write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({k:v for k,v in result.items() if k!='horizons'}));return 0


if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--stem',default='evolve2-4')
    raise SystemExit(main(stem=p.parse_args().stem))
