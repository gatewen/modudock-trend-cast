"""Daily research namespace. No reads/writes to the 30-minute data or experiments.

Year/range replacement and coverage are atomic. Calendar is an independent
dataset so missing symbol candles cannot silently shorten a return horizon.
"""
from contextlib import contextmanager
from pathlib import Path
import sqlite3

from .data import DataError, candles, day_value, decimal_value, symbol_value
from .daily_sources import CALENDAR, INSTITUTIONAL, MARGIN, parse_finmind
from .store import DEFAULT_DB, now_string
from .twse import SOURCE

SCHEMA = '''
CREATE TABLE IF NOT EXISTS d_calendar(day TEXT PRIMARY KEY,source TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS d_bars(
 symbol TEXT NOT NULL,day TEXT NOT NULL,open TEXT NOT NULL,high TEXT NOT NULL,
 low TEXT NOT NULL,close TEXT NOT NULL,volume TEXT NOT NULL,PRIMARY KEY(symbol,day));
CREATE TABLE IF NOT EXISTS d_corp_events(
 symbol TEXT NOT NULL,day TEXT NOT NULL,prev_close TEXT NOT NULL,ref_price TEXT NOT NULL,
 source TEXT NOT NULL,PRIMARY KEY(symbol,day));
CREATE TABLE IF NOT EXISTS d_corp_coverage(
 symbol TEXT NOT NULL,start_day TEXT NOT NULL,end_day TEXT NOT NULL,source TEXT NOT NULL,
 fetched_at TEXT NOT NULL,PRIMARY KEY(symbol,start_day,end_day));
CREATE TABLE IF NOT EXISTS d_institutional(
 symbol TEXT NOT NULL,day TEXT NOT NULL,name TEXT NOT NULL,buy INTEGER NOT NULL,
 sell INTEGER NOT NULL,PRIMARY KEY(symbol,day,name));
CREATE TABLE IF NOT EXISTS d_margin(
 symbol TEXT NOT NULL,day TEXT NOT NULL,margin_balance INTEGER NOT NULL,
 short_balance INTEGER NOT NULL,PRIMARY KEY(symbol,day));
CREATE TABLE IF NOT EXISTS d_fetch_log(
 kind TEXT NOT NULL,symbol TEXT NOT NULL,start_day TEXT NOT NULL,end_day TEXT NOT NULL,
 fetched_at TEXT NOT NULL,status TEXT NOT NULL,http_status INTEGER,n_rows INTEGER NOT NULL,
 source TEXT NOT NULL,PRIMARY KEY(kind,symbol,start_day,end_day));
'''


class DailyStore:
    def __init__(self, path=DEFAULT_DB, *, readonly=False):
        self.path = Path(path)
        if readonly:
            self.db = sqlite3.connect(self.path.resolve().as_uri()+'?mode=ro',uri=True,timeout=5)
        else:
            if str(path) != ':memory:': self.path.parent.mkdir(parents=True,exist_ok=True)
            self.db = sqlite3.connect(str(path), timeout=5)
            self.db.execute('PRAGMA journal_mode=WAL')
            self.db.executescript(SCHEMA)
        self.db.row_factory = sqlite3.Row

    def close(self): self.db.close()
    def __enter__(self): return self
    def __exit__(self,*_): self.close()

    @contextmanager
    def transaction(self):
        self.db.execute('BEGIN IMMEDIATE')
        try:
            yield
            self.db.commit()
        except BaseException:
            self.db.rollback()
            raise

    def _log(self, kind, symbol, start, end, n, source):
        self.db.execute('INSERT OR REPLACE INTO d_fetch_log VALUES (?,?,?,?,?,?,?,?,?)',
            (kind,symbol,start,end,now_string(),'written',200,n,source))

    def fetched(self, kind, symbol, start, end):
        return self.db.execute('''SELECT 1 FROM d_fetch_log WHERE kind=? AND symbol=?
            AND start_day=? AND end_day=? AND status='written' ''', (kind,symbol,start,end)).fetchone() is not None

    def write_bars(self, batch):
        if batch.timeframe != 'D' or batch.status != 200 or not batch.rows:
            raise DataError('unconfirmed_daily_candles')
        rows = candles([dict(r,date=r['day']) for r in batch.rows],batch.symbol,batch.start,batch.end,'D')
        if rows != batch.rows:
            raise DataError('noncanonical_daily_candles')
        with self.transaction():
            self.db.execute('DELETE FROM d_bars WHERE symbol=? AND day BETWEEN ? AND ?',
                            (batch.symbol,batch.start,batch.end))
            self.db.executemany('INSERT INTO d_bars VALUES (?,?,?,?,?,?,?)',
                ((r['symbol'],r['day'],r['open'],r['high'],r['low'],r['close'],r['volume']) for r in rows))
            self._log('bars',batch.symbol,batch.start,batch.end,len(rows),'Fugle:D:adjusted=false')

    def write_corp(self, batch):
        symbol_value(batch.symbol); day_value(batch.start); day_value(batch.end)
        if batch.start>batch.end or batch.source!=SOURCE:
            raise DataError('invalid_daily_corp_batch')
        values=[]; seen=set()
        for r in batch.events:
            day_value(r['day'])
            if (r['symbol']!=batch.symbol or r['source']!=SOURCE or r['day'] in seen
                    or not batch.start<=r['day']<=batch.end):
                raise DataError('invalid_daily_corp_event')
            seen.add(r['day'])
            values.append((r['symbol'],r['day'],decimal_value(r['prev_close']),decimal_value(r['ref_price']),SOURCE))
        with self.transaction():
            self.db.execute('DELETE FROM d_corp_events WHERE symbol=? AND day BETWEEN ? AND ?',
                            (batch.symbol,batch.start,batch.end))
            self.db.executemany('INSERT INTO d_corp_events VALUES (?,?,?,?,?)',values)
            self.db.execute('INSERT OR REPLACE INTO d_corp_coverage VALUES (?,?,?,?,?)',
                            (batch.symbol,batch.start,batch.end,SOURCE,now_string()))
            self._log('corp',batch.symbol,batch.start,batch.end,len(values),SOURCE)

    def corp_state(self, day, symbol='2330'):
        day_value(day); symbol_value(symbol)
        known=self.db.execute('''SELECT 1 FROM d_corp_coverage WHERE symbol=?
            AND start_day<=? AND end_day>=?''',(symbol,day,day)).fetchone()
        if known is None: return {'state':'unknown','event':None}
        event=self.db.execute('SELECT * FROM d_corp_events WHERE symbol=? AND day=?',(symbol,day)).fetchone()
        return {'state':'event' if event else 'none','event':dict(event) if event else None}

    def write_finmind(self, batch):
        # Revalidate injected batches through the same canonical parser.
        raw=[]
        for row in batch.rows:
            r=dict(date=row['day'])
            if batch.dataset!=CALENDAR: r['stock_id']=row['symbol']
            if batch.dataset==INSTITUTIONAL: r.update(name=row['name'],buy=row['buy'],sell=row['sell'])
            if batch.dataset==MARGIN: r.update(MarginPurchaseTodayBalance=row['margin_balance'],ShortSaleTodayBalance=row['short_balance'])
            raw.append(r)
        validated=parse_finmind({'status':200,'msg':'success','data':raw},batch.dataset,batch.symbol,batch.start,batch.end)
        if validated!=batch: raise DataError('noncanonical_finmind_batch')
        if batch.dataset==CALENDAR and not batch.rows: raise DataError('empty_trading_calendar')
        with self.transaction():
            if batch.dataset==CALENDAR:
                self.db.execute('DELETE FROM d_calendar WHERE day BETWEEN ? AND ?',(batch.start,batch.end))
                self.db.executemany('INSERT INTO d_calendar VALUES (?,?)',((r['day'],'FinMind:'+CALENDAR) for r in batch.rows))
                kind='calendar'
            else:
                table='d_institutional' if batch.dataset==INSTITUTIONAL else 'd_margin'
                self.db.execute(f'DELETE FROM {table} WHERE symbol=? AND day BETWEEN ? AND ?', (batch.symbol,batch.start,batch.end))
                if batch.dataset==INSTITUTIONAL:
                    self.db.executemany('INSERT INTO d_institutional VALUES (?,?,?,?,?)',
                        ((r['symbol'],r['day'],r['name'],r['buy'],r['sell']) for r in batch.rows))
                    kind='institutional'
                else:
                    self.db.executemany('INSERT INTO d_margin VALUES (?,?,?,?)',
                        ((r['symbol'],r['day'],r['margin_balance'],r['short_balance']) for r in batch.rows))
                    kind='margin'
            self._log(kind,batch.symbol,batch.start,batch.end,len(batch.rows),'FinMind:'+batch.dataset)
