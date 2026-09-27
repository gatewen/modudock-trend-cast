#!/usr/local/bin/python3
"""Deterministic full lifecycle, entirely synthetic; no Jev transport allowed."""
from pathlib import Path
import json
import tempfile
from unittest.mock import patch
from back.daily_store import DailyStore
from back.daily_forward import candidates,prepare_day,save_baselines,claim_request,save_jev,settle
from back.daily_forward_view import forward_view
from back.daily_jev_client import validate_daily_response
from back.evolution_run import ForbiddenClient
from tests.test_daily_forward import fixture,moment,DAY
from tests.test_daily_jev import response

ROOT=Path(__file__).resolve().parents[1]

def main():
    guard=ForbiddenClient()
    with guard.guard(),DailyStore(':memory:') as s:
        row,bundle=fixture(s)
        assert candidates(s,row,moment('2026-09-28T18:00:00'))==()
        # No future calendar has arrived. Keep prediction records pending timing confirmation.
        future=list(s.db.execute('SELECT * FROM d_calendar WHERE day>?',(DAY,)))
        s.db.execute('DELETE FROM d_calendar WHERE day>?',(DAY,));s.db.commit()
        now=moment(DAY+'T17:00:00');prepared=prepare_day(s,row,bundle,DAY)
        save_baselines(s,prepared,now);assert claim_request(s,DAY) is not None
        save_jev(s,DAY,validate_daily_response(response()),now)
        before=forward_view(s,dict(op='daily_forward',H=7))
        assert {p['timing'] for p in before['latest']['predictions']}=={'unconfirmed'}
        assert claim_request(s,DAY) is None
        s.db.executemany('INSERT INTO d_calendar VALUES (?,?)',(tuple(r) for r in future));s.db.commit()
        counts=[]
        for H in (3,7,14):
            end=future[H-1]['day']
            fresh=settle(s,moment(end+'T16:30:00'));counts.append(dict(H=H,end_day=end,new_outcomes=fresh))
        after=forward_view(s,dict(op='daily_forward',H=7))
        assert {p['timing'] for p in after['latest']['predictions']}=={'ontime'}
        assert len(after['pending'])==0
        report=dict(synthetic=True,real_http_calls=guard.calls,holiday_0928_predictions=0,origin=DAY,
            forecast_rows=s.db.execute('SELECT count(*) FROM d_forward_predictions').fetchone()[0],
            outcome_rows=s.db.execute('SELECT count(*) FROM d_forward_outcomes').fetchone()[0],
            research_outcomes_unchanged=s.db.execute('SELECT count(*) FROM d_outcomes').fetchone()[0]==0,
            initial_timing='unconfirmed',confirmed_timing='ontime',maturities=counts,
            recorded_at_preserved=before['latest']['predictions']==[dict(p,timing='unconfirmed') for p in after['latest']['predictions']],
            duplicate_request=False,pending=0)
    assert report['recorded_at_preserved'] and report['forecast_rows']==12 and report['outcome_rows']==3
    (ROOT/'docs/verification/evolve-9-fixture-flow.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(report,ensure_ascii=False))
if __name__=='__main__':main()
