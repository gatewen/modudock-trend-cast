#!/usr/local/bin/python3
"""Read-only audit against the already-reviewed round-6/7 development results."""
from pathlib import Path
import hashlib
import json
import re
import sqlite3
import sys
if __package__ in (None,''):sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from back.daily_view import DailyViews
from back.daily_store import DailyStore
from back.evolution_run import ForbiddenClient
from back.protocol import Outbox

ROOT=Path(__file__).resolve().parents[1]
DOC=ROOT/'docs/verification'

def fingerprints(db):
    result={}
    names=[r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
    for name in names:
        assert re.fullmatch('[A-Za-z_][A-Za-z_0-9]*',name)
        digest=hashlib.sha256()
        for row in db.execute(f'SELECT * FROM {name} ORDER BY '+','.join(str(i+1) for i in range(len(db.execute(f'SELECT * FROM {name} LIMIT 0').description)))):
            digest.update(json.dumps(tuple(row),ensure_ascii=False,separators=(',',':')).encode());digest.update(b'\n')
        result[name]=digest.hexdigest()
    return result

def main():
    reference=json.loads((DOC/'evolve-6-development.json').read_text())
    jev=json.loads((DOC/'evolve-7-development.json').read_text())
    views=DailyViews();client=ForbiddenClient();rows=intervals=claims_checked=0;packets=[]
    with client.guard(),DailyStore(ROOT/'data/trendcast.sqlite3',readonly=True) as store:
        before=fingerprints(store.db)
        for H in (3,7,14):
            report=views.handle(store,dict(op='daily_report',H=H));expected=reference['horizons'][str(H)]
            for value in report['methods']:
                method=value['method']
                if method=='jev_ind':
                    old=jev['horizons'][str(H)]['jev_ind'];comp=jev['horizons'][str(H)]['comparisons']['majority']
                else:old=expected['methods'][method];comp=old['brier_vs_majority']
                for key in ('n','accuracy','brier'):assert abs(value[key]-old[key])<1e-12,(H,method,key)
                assert abs(value['difference']-comp['difference'])<1e-12
                assert all(abs(a-b)<1e-12 for a,b in zip(value['ci95'],comp['ci95']))
                rows+=1;intervals+=1
            old_claims={(r['indicator'],r['state']):r for r in expected['claims_vs_frequency']}
            for value in report['claims']:
                old=old_claims[value['indicator'],value['state']]
                assert value['n']==old['n'] and value['frequencies']==old['frequencies']
                claims_checked+=1
            for op,fields in [('daily_status',{}),('daily_chart',{'range':'all'}),('daily_chart',{'range':'1y'}),
                              ('daily_chart',{'range':'6m'}),('daily_indicators',{'date':'2021-12-30'}),('daily_report',{})]:
                payload=views.handle(store,dict(op=op,H=H,**fields))
                raw=json.dumps(payload,ensure_ascii=False)
                assert all(d<='2021-12-31' for d in re.findall(r'\d{4}-\d{2}-\d{2}',raw))
                size=len(Outbox.encode(dict(t='msg',seq=1,body=dict(payload,op=op))))
                packets.append(dict(H=H,op=op,bytes=size))
        after=fingerprints(store.db);assert before==after
    with sqlite3.connect((ROOT/'data/evolve-2026-09-27-budget.sqlite3').as_uri()+'?mode=ro',uri=True) as ledger:
        campaign=ledger.execute('SELECT used FROM budget WHERE campaign=?',('evolve/2026-09-27',)).fetchone()[0]
    assert campaign==1222
    result=dict(metric_rows_checked=rows,intervals_checked=intervals,claims_checked=claims_checked,
        database_tables_unchanged=len(before),http_calls=client.calls,campaign_used=campaign,
        all_daily_exports_development_only=True,packets=packets)
    (DOC/'evolve-8-data-audit.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(result))
if __name__=='__main__':main()
