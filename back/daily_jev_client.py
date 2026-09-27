"""Three-question p6 client; legacy verified HTTPS transport, durable dual quota."""
from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import sqlite3
import threading

from .daily_replay import HORIZONS
from .daily_prompt import ROUND_CALL_LIMIT
from .data import DataError
from .evolution_budget import LEDGER, LIMIT
from .experiment import MODEL
from .http_client import ClientError, secure_opener
from .jevcast import JevClient, validate_response, _AUTH_DISABLED, _HTTP_SLOTS

SCOPE='daily-4-p6-round-7'
CAMPAIGN='evolve/2026-09-27'


class RoundBudget:
    """Reserve round+campaign permits in ONE commit immediately before HTTP.

    Interrupted reservations are conservatively charged, never refunded. Both
    limits survive process restart and copying the research database.
    """
    def __init__(self, plan_hash, *, path=LEDGER, max_calls=ROUND_CALL_LIMIT):
        if type(max_calls) is not int or not 0<=max_calls<=ROUND_CALL_LIMIT:
            raise DataError('invalid_p6_max_calls')
        self.path=Path(path);self.path.parent.mkdir(parents=True,exist_ok=True)
        self.max_calls=max_calls;self.calls=0;self.lock=threading.Lock()
        with sqlite3.connect(self.path) as db:
            db.executescript('''CREATE TABLE IF NOT EXISTS budget(campaign TEXT PRIMARY KEY,used INTEGER NOT NULL CHECK(used>=0));
                CREATE TABLE IF NOT EXISTS p6_budget(scope TEXT PRIMARY KEY,plan_hash TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS p6_attempts(scope TEXT NOT NULL,seq INTEGER NOT NULL,day TEXT NOT NULL,
                    retry INTEGER NOT NULL,status INTEGER,error TEXT,latency REAL,PRIMARY KEY(scope,seq));''')
            db.execute('BEGIN IMMEDIATE')
            db.execute('INSERT OR IGNORE INTO budget VALUES (?,0)',(CAMPAIGN,))
            db.execute('INSERT OR IGNORE INTO p6_budget VALUES (?,?)',(SCOPE,plan_hash))
            if db.execute('SELECT plan_hash FROM p6_budget WHERE scope=?',(SCOPE,)).fetchone()[0]!=plan_hash:
                raise DataError('p6_budget_plan_changed')

    def reserve(self,day):
        with self.lock:
            if self.calls>=self.max_calls: raise ClientError('p6_call_limit')
            with sqlite3.connect(self.path,timeout=15) as db:
                db.execute('BEGIN IMMEDIATE')
                used=db.execute('SELECT used FROM budget WHERE campaign=?',(CAMPAIGN,)).fetchone()[0]
                count=db.execute('SELECT count(*) FROM p6_attempts WHERE scope=?',(SCOPE,)).fetchone()[0]
                if count>=ROUND_CALL_LIMIT: raise ClientError('p6_call_limit')
                if used>=LIMIT: raise ClientError('evolution_budget_exhausted')
                retry=int(db.execute('SELECT 1 FROM p6_attempts WHERE scope=? AND day=? LIMIT 1',(SCOPE,day)).fetchone() is not None)
                db.execute('UPDATE budget SET used=used+1 WHERE campaign=?',(CAMPAIGN,))
                db.execute('INSERT INTO p6_attempts(scope,seq,day,retry) VALUES (?,?,?,?)',(SCOPE,count+1,day,retry))
            self.calls+=1
            return count+1

    def finish(self,seq,*,status=None,error=None,latency=None):
        if seq is None:return
        with sqlite3.connect(self.path,timeout=15) as db:
            db.execute('UPDATE p6_attempts SET status=coalesce(?,status),error=?,latency=coalesce(?,latency) WHERE scope=? AND seq=?',
                       (status,error,latency,SCOPE,seq))

    def summary(self):
        with sqlite3.connect(self.path,timeout=15) as db:
            used,retries=db.execute('SELECT count(*),coalesce(sum(retry),0) FROM p6_attempts WHERE scope=?',(SCOPE,)).fetchone()
            campaign=db.execute('SELECT used FROM budget WHERE campaign=?',(CAMPAIGN,)).fetchone()[0]
        return dict(http_calls_this_run=self.calls,http_calls_round=used,retries_round=retries,campaign_used=campaign,
                    round_limit=ROUND_CALL_LIMIT)

    def exhausted(self):
        r=self.summary()
        return self.calls>=self.max_calls or r['http_calls_round']>=ROUND_CALL_LIMIT or r['campaign_used']>=LIMIT


@dataclass(frozen=True)
class DailyAnswer:
    answers: dict = field(repr=False)
    model_reported: str | None
    latency_seconds: float=0.


def validate_daily_response(payload):
    names={f'direction_{H}d' for H in HORIZONS}
    if not isinstance(payload,dict) or not isinstance(payload.get('answers'),dict) or set(payload['answers'])!=names:
        raise ClientError('invalid_daily_answers')
    answers={}
    for H in HORIZONS:
        one=dict(answers={'direction':payload['answers'][f'direction_{H}d']})
        if 'model' in payload:one['model']=payload['model']
        answers[H]=validate_response(one,MODEL,prompt_version='p1')
    return DailyAnswer(answers,payload.get('model'))


class DailyJevClient(JevClient):
    def __init__(self,budget,*,opener=None,sleep=None,clock=None):
        self.budget=budget;self.local=threading.local()
        kwargs=dict(opener=opener if opener is not None else secure_opener(),sleep=sleep,before_request=self._reserve)
        if clock is not None:kwargs['clock']=clock
        # Explicit secure opener: our atomic hook replaces the base transport's
        # separate campaign-only reservation. TLS, redirect/size/error guards stay.
        super().__init__(**kwargs)

    def _reserve(self):
        self.local.seq=self.budget.reserve(self.local.day)

    def predict(self,day,body,*,cancel=None):
        cancel=cancel if cancel is not None else threading.Event()
        payload=json.loads(body)
        if (payload.get('model')!=MODEL or set(payload.get('questions',{}))!={f'direction_{h}d' for h in HORIZONS}):
            raise DataError('invalid_p6_request')
        key=os.environ.get('TYPESAFE_API_KEY','')
        if not key:raise ClientError('missing_key')
        if '\r' in key or '\n' in key:raise ClientError('invalid_key')
        self.local.day=day;self.local.seq=None
        started=self._clock()
        try:
            for attempt in range(3):
                self._check(cancel)
                while not _HTTP_SLOTS.acquire(timeout=.05):self._check(cancel)
                try:
                    self._check(cancel);self.local.seq=None;http_start=self._clock()
                    response=self._request(body,key)
                    self.budget.finish(self.local.seq,status=response.status,latency=self._clock()-http_start)
                    if response.status in (401,403):_AUTH_DISABLED.set()
                finally:_HTTP_SLOTS.release()
                self._check(cancel)
                if response.status in (429,529) and attempt<2:
                    if self._sleep is None:cancel.wait((.5,1.)[attempt])
                    else:self._sleep((.5,1.)[attempt])
                    continue
                if response.status!=200:raise ClientError('http_status',response.status)
                answer=validate_daily_response(response.payload)
                return DailyAnswer(answer.answers,answer.model_reported,self._clock()-started)
        except ClientError as error:
            self.budget.finish(self.local.seq,error=error.code)
            raise
        raise AssertionError('unreachable')
