#!/usr/local/bin/python3
"""Daily inventory plus DEVELOPMENT ONLY thresholds/labels; never holdout scores."""
import argparse
import json
from pathlib import Path
import sys
if __package__ in (None,''):sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from back.daily_replay import DailyReplay,development_thresholds
from back.daily_store import DailyStore
from back.daily_sync import START,END,no_jev
from back.store import DEFAULT_DB

def inventory(store,start=START,end=END):
    calendar={r[0] for r in store.db.execute('SELECT day FROM d_calendar WHERE day BETWEEN ? AND ?',(start,end))}
    bars={r[0] for r in store.db.execute('SELECT day FROM d_bars WHERE symbol=? AND day BETWEEN ? AND ?',('2330',start,end))}
    chips={}
    for table in ('d_institutional','d_margin'):
        row=store.db.execute(f'SELECT min(day),max(day),count(DISTINCT day) FROM {table} WHERE symbol=?',('2330',)).fetchone()
        chips[table]=dict(first_day=row[0],last_day=row[1],trading_days=row[2])
    return dict(start=start,end=end,trading_days=len(calendar),candle_days=len(bars),
        missing_days=sorted(calendar-bars),unexpected_days=sorted(bars-calendar),
        corporate_events=store.db.execute('SELECT count(*) FROM d_corp_events WHERE symbol=? AND day BETWEEN ? AND ?',('2330',start,end)).fetchone()[0],
        unknown_corporate_days=[day for day in sorted(calendar) if store.corp_state(day)['state']=='unknown'],chips=chips)

def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db',type=Path,default=DEFAULT_DB)
    parser.add_argument('--inventory-only',action='store_true')
    args=parser.parse_args(argv)
    with DailyStore(args.db,readonly=True) as store,no_jev():
        result=inventory(store)
        if not args.inventory_only:result['development']=development_thresholds(DailyReplay(store))
    result['jev_http_calls']=0
    print(json.dumps(result,ensure_ascii=False,indent=2))
    return 0

if __name__=='__main__':raise SystemExit(main())
