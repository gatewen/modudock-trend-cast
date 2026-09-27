"""Cancelable automatic live data/prediction worker; DBWriter owns all writes."""
from datetime import timedelta
import os
import threading

from .daily_forward import (clock_now,ensure_schema,load_model,build_model,install_model,
    candidates,prepare_day,save_baselines,claim_request,save_jev,settle,next_deadline,closed_end)
from .daily_experiment import load_experiment
from .daily_store import DailyStore
from .daily_incremental import attached,ensure_daily,windows,append_batch
from .daily_sources import FinmindClient,CALENDAR,INSTITUTIONAL,MARGIN
from .daily_sync import TWSE_LIMITER
from .daily_forward_client import ForwardBudget,ForwardClient
from .data import DataError,day_value
from .fugle import FugleClient
from .twse import TwseClient
from .http_client import ClientError
from .jobs import sync_reason

SAFE={'forward_frozen_settings_changed','forward_missing_p6_plan','daily_source_digest_changed',
    'daily_frozen_settings_changed','forward_not_predictable','daily_source_revision',
    'evolution_budget_exhausted','forward_retry_exhausted','forward_retry_expired','forward_retry_uncertain',
    'forward_retry_wait_calendar','missing_key','auth_disabled','http_status','network_error',
    'tls_certificate_error','tls_error','request_timeout','cancelled','database_operation_failed'}


class DailyForwardService:
    def __init__(self,writer,gate,*,now=clock_now,on_change=None,client=None,budget=None,
                 fugle=None,twse=None,finmind=None,poll_seconds=900):
        self.writer,self.gate,self.now=writer,gate,now;self.on_change=on_change
        self.client,self.budget=client,budget
        self.fugle=fugle or FugleClient();self.twse=twse or TwseClient(limiter=TWSE_LIMITER);self.finmind=finmind or FinmindClient()
        self.poll_seconds=poll_seconds;self.event=threading.Event();self.cancel=threading.Event();self.lock=threading.Lock()
        self.sync_requested=False;self.state={'state':'idle'}
        self.worker=threading.Thread(target=self._loop,name='daily-forward-worker',daemon=True);self.worker.start()

    def trigger(self,*,sync=False):
        with self.lock:self.sync_requested |= sync
        self.event.set()

    def _emit(self,state,code=None):
        self.state=dict(state=state,**({'error':code} if code else {}))
        if self.on_change and not self.cancel.is_set():self.on_change()

    def _write(self,fn):
        if self.cancel.is_set():raise DataError('cancelled')
        def guarded(store):
            if self.cancel.is_set():raise DataError('cancelled')
            return fn(attached(store))
        return self.writer.submit(guarded).result()

    def _read(self,fn):
        with DailyStore(self.writer.path,readonly=True) as store:return fn(store)

    def _sync(self,row):
        generation=self.gate.claim('sync')
        try:
            end=closed_end(self.now());jobs=self._read(lambda s:windows(s,row['config'],end))
            floor=(day_value(row['config']['hold_end'])+timedelta(days=1)).isoformat()
            self._emit('syncing')
            for kind,start,last in jobs:
                if self.cancel.is_set():raise DataError('cancelled')
                if kind=='bars':batch=self.fugle.candles(row['config']['symbol'],start,last,'D')
                elif kind=='corp':batch=self.twse.events(row['config']['symbol'],start,last)
                else:batch=self.finmind.fetch({'calendar':CALENDAR,'institutional':INSTITUTIONAL,'margin':MARGIN}[kind],start,last,row['config']['symbol'])
                self._write(lambda s:append_batch(s,kind,batch,floor))
        finally:self.gate.release('sync',generation)

    def _ensure_client(self):
        if self.client is not None:return
        self.budget=self.budget or ForwardBudget(now=self.now,deadline=lambda d:self._read(lambda s:next_deadline(s,d)))
        self.client=ForwardClient(self.budget)

    def cycle(self,*,sync=False):
        # No experiment is a normal state for installations without research data.
        with DailyStore(self.writer.path,readonly=True) as s:
            if s.db.execute("SELECT 1 FROM sqlite_master WHERE name='d_experiments'").fetchone() is None:return
            if s.db.execute('SELECT 1 FROM d_experiments WHERE id=4').fetchone() is None:return
        self._write(ensure_schema)
        row,bundle=self._read(load_model)
        if sync and os.environ.get('FUGLE_API_KEY'):
            try:self._sync(row)
            except DataError as error:
                if str(error)!='busy':raise
                return  # Terminal legacy sync callback (or next poll) will retry.
        days=self._read(lambda s:candidates(s,row,self.now()))
        if days and bundle is None:
            self._emit('preparing')
            bundle=self._read(build_model);self._write(lambda s:install_model(s,bundle))
        self._emit('recording')
        for day in days:
            saved=self._read(lambda s:s.db.execute('SELECT * FROM d_forward_days WHERE day=?',(day,)).fetchone())
            if saved is None:
                try:prepared=self._read(lambda s:prepare_day(s,row,bundle,day))
                except DataError as e:
                    if str(e)=='forward_not_predictable':continue
                    raise
                self._write(lambda s:save_baselines(s,prepared,self.now))
            # Missing key does not reserve a network attempt. The baselines remain available.
            if self.client is None and not os.environ.get('TYPESAFE_API_KEY'):continue
            self._ensure_client()
            saved=self._read(lambda s:s.db.execute('SELECT * FROM d_forward_days WHERE day=?',(day,)).fetchone())
            if saved['jev_state']=='done' or saved['jev_state']=='failed':continue
            if saved['jev_state']=='reserved':
                # Restart recovery is based on the durable HTTP status, never a blind retry.
                state=self.budget.retry_status(day) if self.budget else 'uncertain'
                if state=='initial':state='uncertain'  # process stopped between reservation and transport
                self._set_failure(day,state,'forward_retry_'+state)
            if self.budget:
                state=self.budget.retry_status(day)
                if state not in ('initial','retry'):
                    self._set_failure(day,state,'forward_retry_'+state);continue
            body=self._write(lambda s:claim_request(s,day))
            if body is None:continue
            try:
                answer=self.client.predict(day,body,cancel=self.cancel)
                self._write(lambda s:save_jev(s,day,answer,self.now))
            except ClientError as error:
                state=self.budget.retry_status(day) if self.budget else 'uncertain'
                code=error.code if error.code in SAFE else 'response_invalid'
                def failed(s):
                    with s.transaction():s.db.execute('UPDATE d_forward_days SET jev_state=?,error=? WHERE day=?',
                        ('retry_wait' if state in ('retry','wait_calendar') else 'failed',code,day))
                self._write(failed)
        self._write(lambda s:settle(s,self.now()))
        self._emit('idle')

    def _set_failure(self,day,state,code):
        def write(store):
            with store.transaction():
                store.db.execute("UPDATE d_forward_days SET jev_state=?,error=? WHERE day=? AND jev_state!='done'",
                    ('retry_wait' if state in ('retry','wait_calendar') else 'failed',code,day))
        self._write(write)

    def _loop(self):
        while not self.cancel.is_set():
            signaled=self.event.wait(self.poll_seconds);self.event.clear()
            if self.cancel.is_set():return
            with self.lock:sync=self.sync_requested or not signaled;self.sync_requested=False
            try:self.cycle(sync=sync)
            except Exception as error:
                code=str(error) if str(error) in SAFE else sync_reason(error)
                self._emit('error',code)

    def close(self):self.cancel.set();self.event.set()
