#!/usr/local/bin/python3
"""Independent read-only arithmetic audit; never imports feature/model/score code."""
import argparse
import hashlib
import json
import math
from pathlib import Path
import random
import sqlite3


def canonical(value):return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False)


def percentile(values,q):
    values=sorted(values);position=(len(values)-1)*q;lower=int(position);upper=min(lower+1,len(values)-1)
    return values[lower]+(values[upper]-values[lower])*(position-lower)


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db',type=Path,required=True)
    parser.add_argument('--report',type=Path,required=True)
    parser.add_argument('--before',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args(argv)
    report=json.loads(args.report.read_text());before=json.loads(args.before.read_text())
    first,last='2010-04-01','2021-12-31'
    checks={}
    with sqlite3.connect(args.db.resolve().as_uri()+'?mode=ro',uri=True) as db:
        db.row_factory=sqlite3.Row
        original=db.execute('SELECT data_digest,dev_digest FROM d_experiments WHERE id=4').fetchone()
        assert original['data_digest']==before['original_data_digest']==report['data_digest']
        assert original['dev_digest']==before['original_dev_digest']==report['dev_digest']
        for table,expected in before['original_development_tables'].items():
            assert table in ('d_features','d_predictions','d_outcomes','d_fits')
            rows=[dict(r) for r in db.execute(f'SELECT * FROM {table} WHERE experiment_id=4 AND day BETWEEN ? AND ? ORDER BY rowid',(first,last))]
            actual=dict(rows=len(rows),sha256=hashlib.sha256(canonical(rows).encode()).hexdigest())
            assert actual==expected,(table,actual,expected)
        calendar=[r[0] for r in db.execute('SELECT day FROM d_calendar WHERE day BETWEEN ? AND ? ORDER BY day',(first,last))]
        for H,results in report['horizons'].items():
            labels={r['day']:r['label'] for r in db.execute('''SELECT day,label FROM d_outcomes
                WHERE experiment_id=4 AND H=? AND day BETWEEN ? AND ? AND end_day<=?''',(int(H),first,last,last))}
            reference={r['day']:json.loads(r['probabilities_json']) for r in db.execute('''SELECT day,probabilities_json
                FROM d_predictions WHERE experiment_id=4 AND H=? AND method='majority' AND day BETWEEN ? AND ?''',(int(H),first,last))}
            for method,result in results.items():
                preds={r['day']:json.loads(r['probabilities_json']) for r in db.execute('''SELECT day,probabilities_json
                    FROM market_predictions WHERE experiment_id=4 AND H=? AND method=? AND day BETWEEN ? AND ?''',(int(H),method,first,last))}
                days=set(labels)&set(reference)&set(preds)
                def brier(p,y):return sum((p[k]-(k==y))**2 for k in ('up','flat','down'))
                differences={d:brier(preds[d],labels[d])-brier(reference[d],labels[d]) for d in days}
                blocks=[]
                for offset in range(0,len(calendar),20):
                    ds=[d for d in calendar[offset:offset+20] if d in differences]
                    if ds:blocks.append((len(ds),math.fsum(differences[d] for d in ds)))
                delta=math.fsum(v for _,v in blocks)/sum(n for n,_ in blocks)
                rng=random.Random(20260927);samples=[]
                for _ in range(2000):
                    chosen=[blocks[rng.randrange(len(blocks))] for _ in blocks]
                    samples.append(math.fsum(v for _,v in chosen)/sum(n for n,_ in chosen))
                ci=[percentile(samples,.025),percentile(samples,.975)]
                expected=result['brier_vs_majority']
                assert len(days)==result['n']==expected['n']
                assert abs(delta-expected['difference'])<1e-14
                assert all(abs(a-b)<1e-14 for a,b in zip(ci,expected['ci95']))
                assert result['complete'] and result['shortlisted']==(ci[1]<0)
                checks[H+'/'+method]=dict(n=len(days),difference=delta,ci95=ci,match=True)
    assert len(checks)==18
    args.output.write_text(json.dumps(dict(original_digests_unchanged=True,original_development_tables_unchanged=True,
        comparisons=checks,http_calls=0,split='dev'),ensure_ascii=False,indent=2)+'\n')
    print('18/18 DB-only point estimates and bootstrap intervals match; original digests and development tables unchanged.')
    return 0


if __name__=='__main__':raise SystemExit(main())
