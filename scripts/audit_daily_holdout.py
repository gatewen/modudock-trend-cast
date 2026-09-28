#!/usr/local/bin/python3
"""Independent SQL/arithmetic audit, gated by the committed daily reveal."""
import argparse
import hashlib
import json
import math
from pathlib import Path
import random
import sqlite3


def canonical(value):return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False)
def sha(value):return hashlib.sha256(canonical(value).encode()).hexdigest()
def quantile(values,q):
    a=sorted(values);x=(len(a)-1)*q;i=int(x)
    return a[i]+(a[min(i+1,len(a)-1)]-a[i])*(x-i)


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    for key in ('db','report','before','output'):p.add_argument('--'+key,type=Path,required=True)
    args=p.parse_args(argv)
    with sqlite3.connect(args.db.resolve().as_uri()+'?mode=ro',uri=True) as db:
        db.row_factory=sqlite3.Row
        if 'namespace' not in {r[1] for r in db.execute('PRAGMA table_info(reveals)')}:
            raise RuntimeError('daily_holdout_locked')
        if not db.execute("SELECT 1 FROM reveals WHERE namespace='daily' AND experiment_id=4 AND what='all' AND first_day='2022-01-03' AND last_day='2024-07-25'").fetchone():
            raise RuntimeError('daily_holdout_locked')
        report=json.loads(args.report.read_text());before=json.loads(args.before.read_text())
        for table,expected in before['tables'].items():
            assert table in ('d_features','d_predictions','d_outcomes','d_fits','market_features','market_predictions','market_fits')
            rows=[dict(r) for r in db.execute(f'SELECT * FROM {table} WHERE experiment_id=4 AND day BETWEEN ? AND ? ORDER BY rowid',('2010-04-01','2021-12-31'))]
            assert dict(n=len(rows),sha256=sha(rows))==expected
        assert dict(db.execute('SELECT data_digest,dev_digest FROM d_experiments WHERE id=4').fetchone())==before['original']
        assert db.execute('SELECT market_digest FROM market_experiments WHERE experiment_id=4').fetchone()[0]==before['market_digest']
        bundle=json.loads(db.execute('SELECT model_json FROM d_hold_models WHERE experiment_id=4').fetchone()[0])
        fits={}
        for H in (3,7,14):
            m=bundle['horizons'][str(H)]
            truths=[r[0] for r in db.execute("SELECT label FROM d_outcomes WHERE experiment_id=4 AND H=? AND day BETWEEN '2010-04-01' AND '2021-12-31' AND end_day<='2021-12-31'",(H,))]
            assert m['majority']=={k:(truths.count(k)+1)/(len(truths)+3) for k in ('up','flat','down')}
            for table,name in (('d_fits','ind_logit'),('market_fits','mkt_logit')):
                raw=db.execute(f"SELECT day,model_json FROM {table} WHERE experiment_id=4 AND H=? AND day<='2021-12-31' ORDER BY day DESC LIMIT 1",(H,)).fetchone()
                assert m[name]==json.loads(raw['model_json'])
                fits[str(H)+'/'+name]=dict(day=raw['day'],train_n=m[name]['train_n'],match=True)
        calendar=[r[0] for r in db.execute("SELECT day FROM d_calendar WHERE day BETWEEN '2022-01-03' AND '2024-07-25' ORDER BY day")]
        checks=[]
        assert {(c['H'],c['method']) for c in report['primary_comparisons']}=={(3,'ens_avg'),(7,'ens_avg'),(3,'mkt_logit')}
        for expected in report['primary_comparisons']:
            H,method=expected['H'],expected['method']
            truths={r['day']:r['label'] for r in db.execute("SELECT day,label FROM d_hold_outcomes WHERE experiment_id=4 AND H=? AND day BETWEEN '2022-01-03' AND '2024-07-25' AND end_day<='2024-07-25'",(H,))}
            def read(name):return {r['day']:json.loads(r['probabilities_json']) for r in db.execute('SELECT day,probabilities_json FROM d_hold_predictions WHERE experiment_id=4 AND H=? AND method=?',(H,name))}
            candidate,reference=read(method),read('majority')
            def brier(p,y):return math.fsum((p[k]-(k==y))**2 for k in ('up','flat','down'))
            delta={d:brier(candidate[d],y)-brier(reference[d],y) for d,y in truths.items()}
            blocks=[]
            for i in range(0,len(calendar),20):
                days=[d for d in calendar[i:i+20] if d in delta]
                if days:blocks.append((len(days),math.fsum(delta[d] for d in days)))
            point=math.fsum(v for _,v in blocks)/sum(n for n,_ in blocks)
            rng=random.Random(20260927);samples=[]
            for _ in range(2000):
                draw=[blocks[rng.randrange(len(blocks))] for _ in blocks]
                samples.append(math.fsum(v for _,v in draw)/sum(n for n,_ in draw))
            ci=[quantile(samples,.025),quantile(samples,.975)]
            assert len(truths)==expected['n']==len(calendar)-H
            assert abs(point-expected['difference'])<1e-14
            assert all(abs(a-b)<1e-14 for a,b in zip(ci,expected['ci95']))
            checks.append(dict(H=H,method=method,n=len(truths),difference=point,ci95=ci,match=True))
        fk=[tuple(r) for r in db.execute('PRAGMA foreign_key_check')];assert not fk
    result=dict(primary_checks=checks,last_fit_checks=fits,development_tables_unchanged=True,
        original_digests_unchanged=True,market_digest_unchanged=True,foreign_key_violations=fk)
    args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print('3/3 primary comparisons independently match; last fits, full-development majority, old tables and foreign keys verified.')
    return 0


if __name__=='__main__':raise SystemExit(main())
