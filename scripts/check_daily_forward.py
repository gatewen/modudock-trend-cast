#!/usr/local/bin/python3
"""Real DB: startup cycle under a transport ban; no candidates or predictions."""
from pathlib import Path
import json
import sqlite3
import threading
from back.daily_store import DailyStore
from back.daily_forward_service import DailyForwardService
from back.daily_forward_view import forward_view
from back.daily_forward import parsed,candidates
from back.daily_experiment import load_experiment
from back.db_writer import DBWriter
from back.experiment import ActivityGate
from back.evolution_run import ForbiddenClient
from scripts.check_daily_view import fingerprints

ROOT=Path(__file__).resolve().parents[1]

def main():
    path=ROOT/'data/trendcast.sqlite3'
    with DailyStore(path,readonly=True) as s:
        before=fingerprints(s.db)
        row=load_experiment(s)
        assert candidates(s,row,parsed('2026-09-27T18:00:00+08:00'))==()
    writer=DBWriter(path);writer.ready.result(5)
    guard=ForbiddenClient()
    service=DailyForwardService(writer,ActivityGate(),now=lambda:parsed('2026-09-27T18:00:00+08:00'),poll_seconds=3600)
    try:
        with guard.guard():service.cycle()
    finally:service.close();service.worker.join(3);writer.close();writer.thread.join(3)
    with DailyStore(path,readonly=True) as s:
        after=fingerprints(s.db)
        assert all(after[k]==v for k,v in before.items())
        views=[forward_view(s,dict(op='daily_forward',H=h)) for h in (3,7,14)]
        assert all(v['latest'] is None and v['recorded_days']==0 for v in views)
        assert s.db.execute('SELECT count(*) FROM d_forward_predictions').fetchone()[0]==0
        assert s.db.execute('SELECT count(*) FROM d_forward_outcomes').fetchone()[0]==0
        last=s.db.execute('SELECT max(day) FROM d_bars').fetchone()[0]
    with sqlite3.connect((ROOT/'data/evolve-2026-09-27-budget.sqlite3').as_uri()+'?mode=ro',uri=True) as db:
        used=db.execute('SELECT used FROM budget WHERE campaign=?',('evolve/2026-09-27',)).fetchone()[0]
    assert used==1222 and guard.calls==0
    report=dict(http_calls=0,campaign_used=used,latest_source_day=last,forward_predictions=0,forward_outcomes=0,
        existing_tables_unchanged=len(before),views=views)
    (ROOT/'docs/verification/evolve-9-real-audit.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(report,ensure_ascii=False))
if __name__=='__main__':main()
