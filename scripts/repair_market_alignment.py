#!/usr/local/bin/python3
"""Correct audit-only missing-code labels; never change inputs, hashes or scores."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
if __package__ in (None,''):sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from back.daily_store import DailyStore
from back.data import DataError
from back.experiment import canonical
from back.market_run import inputs


def fingerprint(store):
    tables=('d_features','d_predictions','d_outcomes','d_fits','market_features','market_predictions','market_fits',
            'd_experiments','market_experiments','market_rows','d_hold_features','d_hold_predictions','d_hold_outcomes',
            'd_hold_models','d_hold_completion','d_hold_seal','reveals')
    result={}
    for table in tables:
        if not store.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(table,)).fetchone():continue
        rows=[dict(r) for r in store.db.execute(f'SELECT * FROM {table} ORDER BY rowid')]
        if table=='market_features':
            for row in rows:row.pop('alignment_json')
        result[table]=dict(n=len(rows),sha256=hashlib.sha256(canonical(rows).encode()).hexdigest())
    return result


def repair(store,*,execute=False):
    _,original,points,alignment=inputs(store)
    old={p.day:p for p in original};enriched={p.day:p for p in points}
    before=fingerprint(store);changes=[]
    for r in store.db.execute('SELECT * FROM market_features WHERE experiment_id=4 ORDER BY day'):
        day=r['day'];p=enriched.get(day)
        if p is None or (r['original_hash'],r['input_hash'],r['input_json'])!=(old[day].digest,p.digest,p.serialized()):
            raise DataError('market_feature_mismatch')
        actual=json.loads(r['alignment_json']);expected=alignment[day]
        if actual==expected:continue
        legacy={s:dict(a,status='available') if a['status']=='missing_code' else a for s,a in expected.items()}
        if actual!=legacy:raise DataError('market_alignment_not_a_missing_code_correction')
        changes.append(dict(day=day,before=actual,after=expected,old_json=r['alignment_json']))
    if execute:
        with store.transaction():
            for change in changes:
                cur=store.db.execute('UPDATE market_features SET alignment_json=? WHERE experiment_id=4 AND day=? AND alignment_json=?',
                    (canonical(change['after']),change['day'],change['old_json']))
                if cur.rowcount!=1:raise DataError('market_alignment_concurrent_change')
            if fingerprint(store)!=before:raise DataError('market_alignment_changed_features_or_results')
    after=fingerprint(store)
    return dict(executed=execute,corrections=[{k:v for k,v in c.items() if k!='old_json'} for c in changes],
        feature_and_result_fingerprints_before=before,feature_and_result_fingerprints_after=after,
        features_hashes_and_results_unchanged=before==after,http_calls=0)


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--db',type=Path,required=True);p.add_argument('--report',type=Path,required=True)
    p.add_argument('--execute',action='store_true');a=p.parse_args(argv)
    with DailyStore(a.db,readonly=not a.execute) as store:result=repair(store,execute=a.execute)
    a.report.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(dict(executed=a.execute,corrections=len(result['corrections']),features_hashes_and_results_unchanged=result['features_hashes_and_results_unchanged'])))
    return 0


if __name__=='__main__':raise SystemExit(main())
