"""Independent stdlib audit. Only bounded dev SQL; imports no trend-cast code."""
import argparse
import json
import math
from pathlib import Path
import random
import sqlite3

parser=argparse.ArgumentParser()
parser.add_argument('--db',type=Path,required=True)
parser.add_argument('--report',type=Path,required=True)
args=parser.parse_args()
report=json.loads(args.report.read_text())
db=sqlite3.connect(args.db.resolve().as_uri()+'?mode=ro',uri=True)
db.execute('BEGIN')
calendar=[r[0] for r in db.execute("SELECT day FROM d_calendar WHERE day BETWEEN '2010-04-01' AND '2021-12-31' ORDER BY day")]
methods=('majority','vol_prior_d','ind_logit');labels=('up','flat','down')
for H in (3,7,14):
    predictions={}
    for m,d,p in db.execute("""SELECT method,day,probabilities_json FROM d_predictions
        WHERE experiment_id=4 AND H=? AND day BETWEEN '2010-04-01' AND '2021-12-31'
        AND method IN ('majority','vol_prior_d','ind_logit')""",(H,)):
        predictions.setdefault(d,{})[m]=json.loads(p)
    losses={};ensemble_losses=[];base_losses=[]
    for d,truth in db.execute("""SELECT day,label FROM d_outcomes WHERE experiment_id=4 AND H=?
        AND day BETWEEN '2010-04-01' AND '2021-12-31' AND end_day<='2021-12-31' ORDER BY day""",(H,)):
        p=predictions[d]
        avg={k:sum(p[m][k] for m in methods)/3 for k in labels}
        a=sum((avg[k]-(k==truth))**2 for k in labels)
        b=sum((p['majority'][k]-(k==truth))**2 for k in labels)
        ensemble_losses.append(a);base_losses.append(b);losses[d]=a-b
    blocks=[]
    for offset in range(0,len(calendar),20):
        values=[losses[d] for d in calendar[offset:offset+20] if d in losses]
        if values:blocks.append((len(values),math.fsum(values)))
    rng=random.Random(20260927);distribution=[]
    for _ in range(2000):
        draws=[rng.choice(blocks) for _ in blocks]
        distribution.append(math.fsum(x[1] for x in draws)/sum(x[0] for x in draws))
    distribution.sort()
    def quantile(p):
        i=(len(distribution)-1)*p;left=int(i)
        return distribution[left]+(distribution[min(left+1,len(distribution)-1)]-distribution[left])*(i-left)
    ci=[quantile(.025),quantile(.975)];delta=math.fsum(losses.values())/len(losses)
    p=report['horizons'][str(H)]
    assert p['n']==len(losses)
    assert abs(p['ens_avg']['brier']-math.fsum(ensemble_losses)/len(losses))<1e-12
    assert abs(p['majority']['brier']-math.fsum(base_losses)/len(losses))<1e-12
    assert abs(p['brier_vs_majority']['difference']-delta)<1e-12
    assert all(abs(a-b)<1e-12 for a,b in zip(p['brier_vs_majority']['ci95'],ci))
    assert p['shortlisted']==(ci[1]<0)
    print(json.dumps(dict(H=H,n=len(losses),blocks=len(blocks),difference=delta,ci95=ci,matched=True)))
db.close()
