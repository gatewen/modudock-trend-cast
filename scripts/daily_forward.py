#!/usr/local/bin/python3
"""One shared daily-forward cycle, suitable for a terminal or launchd.

Only environment variables supply credentials; stdout is one safe JSON line.
"""
import argparse
import json
import os
from pathlib import Path
import sys

if __package__ in (None,''):
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

from back.daily_forward_service import DailyForwardService
from back.daily_forward_client import ledger_usage
from back.daily_lock import forward_lock
from back.db_writer import DBWriter
from back.evolution_budget import LEDGER,LIMIT
from back.experiment import ActivityGate
from back.store import DEFAULT_DB
from back.data import DataError


def run_once(db_path=DEFAULT_DB,*,ledger_path=LEDGER,service_factory=DailyForwardService):
    summary=dict(status='complete',new_predictions=0,scored_outcomes=0,jev_http_calls=0,
                 ledger_used=None,ledger_limit=LIMIT)
    writer=service=None
    try:
        summary['ledger_used']=ledger_usage(ledger_path)
        missing=[name for name in ('FUGLE_API_KEY','TYPESAFE_API_KEY') if not os.environ.get(name)]
        if missing:return dict(summary,status='missing_keys',missing_keys=missing)
        if not Path(db_path).is_file():return dict(summary,status='error',error='database_missing')
        # Lock precedes opening the writer, and survives until its final close.
        with forward_lock(db_path) as lease:
            try:
                writer=DBWriter(db_path);writer.ready.result()
                service=service_factory(writer,ActivityGate(),autostart=False,ledger_path=ledger_path)
                return service.cycle(sync=True,lease=lease)
            finally:
                if service is not None:service.close()
                if writer is not None:writer.close();writer.thread.join()
    except DataError as error:
        if str(error)=='daily_forward_busy':return dict(summary,status='busy')
        code=str(error) if str(error) in ('daily_forward_lock_failed','database_open_failed') else 'daily_forward_failed'
        return dict(summary,status='error',error=code)
    except Exception:
        return dict(summary,status='error',error='daily_forward_failed')


def main(argv=None,*,stdout=None,runner=run_once):
    parser=argparse.ArgumentParser(description='增量同步後記錄多日前瞻預測並計分；金鑰僅讀環境變數。')
    parser.add_argument('--db',type=Path,default=DEFAULT_DB)
    args=parser.parse_args(argv)
    result=runner(args.db)
    print(json.dumps(result,ensure_ascii=False,separators=(',',':')),file=stdout or sys.stdout)
    return 1 if result['status'] in ('error','no_experiment','partial') else 0


if __name__=='__main__':raise SystemExit(main())
