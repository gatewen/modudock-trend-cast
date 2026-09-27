"""Durable per-day three-attempt quota; only explicit 429/529 may retry."""
from datetime import datetime, time, timedelta
import sqlite3
import threading
from pathlib import Path

from .daily_forward import clock_now, parsed, stamp
from .daily_jev_client import DailyJevClient, CAMPAIGN
from .evolution_budget import LEDGER, LIMIT
from .http_client import ClientError
from .data import TAIPEI


class ForwardBudget:
    def __init__(self, *, path=LEDGER, now=clock_now, deadline=None):
        self.path=Path(path);self.path.parent.mkdir(parents=True,exist_ok=True)
        self.now=now;self.deadline=deadline or (lambda day:None);self.calls=0;self.lock=threading.Lock()
        with sqlite3.connect(self.path) as db:
            db.executescript('''CREATE TABLE IF NOT EXISTS budget(campaign TEXT PRIMARY KEY,used INTEGER NOT NULL CHECK(used>=0));
                CREATE TABLE IF NOT EXISTS daily_forward_http(
                id INTEGER PRIMARY KEY,day TEXT NOT NULL,attempt INTEGER NOT NULL,started_at TEXT NOT NULL,
                status INTEGER,error TEXT,latency REAL,UNIQUE(day,attempt));''')
            db.execute('INSERT OR IGNORE INTO budget VALUES (?,0)',(CAMPAIGN,))

    def last(self,day):
        with sqlite3.connect(self.path) as db:
            db.row_factory=sqlite3.Row
            r=db.execute('SELECT * FROM daily_forward_http WHERE day=? ORDER BY attempt DESC LIMIT 1',(day,)).fetchone()
            return dict(r) if r else None

    def retry_status(self,day,previous=None):
        previous=self.last(day) if previous is None else previous
        if not previous:return 'initial'
        if previous['attempt']>=3:return 'exhausted'
        if previous['status'] not in (429,529):return 'uncertain'
        cutoff=self.deadline(day)
        if cutoff is None:
            # This lower bound is safe without guessing weekends or holidays.
            # Once it passes, no retry until an actual next session is known.
            earliest=datetime.combine(parsed(day+'T00:00:00+08:00').date()+timedelta(days=1),time(9),TAIPEI)
            return 'retry' if self.now()<earliest else 'wait_calendar'
        return 'retry' if self.now()<cutoff else 'expired'

    def reserve(self,day):
        with self.lock,sqlite3.connect(self.path,timeout=15) as db:
            db.row_factory=sqlite3.Row;db.execute('BEGIN IMMEDIATE')
            r=db.execute('SELECT * FROM daily_forward_http WHERE day=? ORDER BY attempt DESC LIMIT 1',(day,)).fetchone()
            state=self.retry_status(day,dict(r)) if r else 'initial'
            if state not in ('initial','retry'):raise ClientError('forward_retry_'+state)
            used=db.execute('SELECT used FROM budget WHERE campaign=?',(CAMPAIGN,)).fetchone()[0]
            if used>=LIMIT:raise ClientError('evolution_budget_exhausted')
            db.execute('UPDATE budget SET used=used+1 WHERE campaign=?',(CAMPAIGN,))
            seq=db.execute('INSERT INTO daily_forward_http(day,attempt,started_at) VALUES (?,?,?)',
                (day,r['attempt']+1 if r else 1,stamp(self.now()))).lastrowid
        self.calls+=1;return seq

    def finish(self,seq,*,status=None,error=None,latency=None):
        if seq is None:return
        with sqlite3.connect(self.path,timeout=15) as db:
            db.execute('UPDATE daily_forward_http SET status=coalesce(?,status),error=?,latency=coalesce(?,latency) WHERE id=?',(status,error,latency,seq))

    def exhausted(self):
        with sqlite3.connect(self.path) as db:return db.execute('SELECT used FROM budget WHERE campaign=?',(CAMPAIGN,)).fetchone()[0]>=LIMIT

class ForwardClient(DailyJevClient):
    """Also honor the remaining backoff when resuming a durable 429/529."""
    def predict(self,day,body,*,cancel=None):
        cancel=cancel if cancel is not None else threading.Event()
        previous=self.budget.last(day)
        if previous and previous['status'] in (429,529) and previous['attempt']<3:
            due=parsed(previous['started_at'])+timedelta(seconds=(.5,1.)[previous['attempt']-1])
            delay=max(0.,(due-self.budget.now()).total_seconds())
            if delay:
                if self._sleep is None:cancel.wait(delay)
                else:self._sleep(delay)
        return super().predict(day,body,cancel=cancel)
