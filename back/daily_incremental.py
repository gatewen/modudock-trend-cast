"""Append-only live daily data; fetch off the writer, commit on its sole owner."""
from datetime import date, timedelta
from fractions import Fraction

from .daily_store import DailyStore, SCHEMA
from .daily_sources import CALENDAR, INSTITUTIONAL, MARGIN
from .daily_sync import years
from .data import DataError, candles, day_value, symbol_value, decimal_value
from .twse import SOURCE
from .store import now_string


def attached(store):
    result = DailyStore.__new__(DailyStore)
    result.db, result.path = store.db, store.path
    return result


def ensure_daily(store):
    store.db.executescript(SCHEMA)
    return attached(store)


def windows(store, config, end):
    floor=(date.fromisoformat(config['hold_end'])+timedelta(days=1)).isoformat()
    result=[]
    for kind,table in (('calendar','d_calendar'),('institutional','d_institutional'),('margin','d_margin'),('bars','d_bars'),('corp','d_corp_coverage')):
        column='end_day' if kind=='corp' else 'day'
        last=store.db.execute(f'SELECT max({column}) FROM {table}').fetchone()[0] or floor
        first=max(floor,(date.fromisoformat(last)-timedelta(days=7)).isoformat())
        if first<=end:
            result.extend((kind,a,b) for a,b in years(first,end))
    return result


def append_batch(store, kind, batch, floor):
    """Missing rows can arrive late. Existing observed values never get rewritten."""
    if batch.start < floor: raise DataError('daily_incremental_frozen_range')
    db=store.db; symbol=batch.symbol
    symbol_value(symbol);day_value(batch.start);day_value(batch.end)
    if batch.start>batch.end:raise DataError('invalid_daily_range')
    if kind=='bars':
        if batch.timeframe!='D':raise DataError('unconfirmed_daily_candles')
        if batch.status==200 and batch.rows:
            rows=candles([dict(r,date=r['day']) for r in batch.rows],symbol,batch.start,batch.end,'D')
            if list(rows)!=list(batch.rows):raise DataError('noncanonical_daily_candles')
    elif kind=='corp':
        if batch.source!=SOURCE:raise DataError('invalid_daily_corp_batch')
        seen=set()
        for r in batch.events:
            if (r['symbol']!=symbol or r['source']!=SOURCE or not batch.start<=r['day']<=batch.end or r['day'] in seen):
                raise DataError('invalid_daily_corp_event')
            day_value(r['day']);decimal_value(r['prev_close']);decimal_value(r['ref_price']);seen.add(r['day'])
    else:
        expected={'calendar':CALENDAR,'institutional':INSTITUTIONAL,'margin':MARGIN}.get(kind)
        if batch.dataset!=expected:raise DataError('noncanonical_finmind_batch')
        for r in batch.rows:
            day_value(r['day'])
            if not batch.start<=r['day']<=batch.end or r.get('symbol',symbol)!=symbol:raise DataError('noncanonical_finmind_batch')
    if kind=='bars' and (batch.status!=200 or not batch.rows): return 0
    inserted=0
    with store.transaction():
        if kind=='corp':
            new={r['day']:r for r in batch.events}
            # Known-none is data too. A provider revision is not silently applied.
            for row in db.execute('SELECT day FROM d_calendar WHERE day BETWEEN ? AND ?', (batch.start,batch.end)):
                day=row[0];old=store.corp_state(day,symbol);event=new.get(day)
                if old['state']=='unknown': continue
                if (old['state']=='event') != (event is not None):raise DataError('daily_source_revision')
                if event and any(Fraction(old['event'][k])!=Fraction(event[k]) for k in ('prev_close','ref_price')):
                    raise DataError('daily_source_revision')
            for day,event in new.items():
                old=db.execute('SELECT * FROM d_corp_events WHERE symbol=? AND day=?',(symbol,day)).fetchone()
                if old and any(Fraction(old[k])!=Fraction(event[k]) for k in ('prev_close','ref_price')):raise DataError('daily_source_revision')
                inserted+=db.execute('INSERT OR IGNORE INTO d_corp_events VALUES (?,?,?,?,?)',
                    (symbol,day,event['prev_close'],event['ref_price'],SOURCE)).rowcount
            db.execute('INSERT OR IGNORE INTO d_corp_coverage VALUES (?,?,?,?,?)',(symbol,batch.start,batch.end,SOURCE,now_string()))
        else:
            table={'bars':'d_bars','calendar':'d_calendar','institutional':'d_institutional','margin':'d_margin'}[kind]
            for raw in batch.rows:
                row=dict(raw)
                if kind=='calendar': row['source']='FinMind:'+CALENDAR
                keys=('day',) if kind=='calendar' else ('symbol','day','name') if kind=='institutional' else ('symbol','day')
                old=db.execute(f'SELECT * FROM {table} WHERE '+' AND '.join(k+'=?' for k in keys),tuple(row[k] for k in keys)).fetchone()
                if old:
                    if any(old[k]!=v for k,v in row.items()):raise DataError('daily_source_revision')
                    continue
                db.execute(f'INSERT INTO {table} ({",".join(row)}) VALUES ({",".join("?" for _ in row)})',tuple(row.values()));inserted+=1
        store._log(kind,symbol,batch.start,batch.end,len(batch.events if kind=='corp' else batch.rows),
            SOURCE if kind=='corp' else 'Fugle:D:adjusted=false' if kind=='bars' else 'FinMind:'+batch.dataset)
    return inserted
