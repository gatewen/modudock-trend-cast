"""Cancelable automatic live data/prediction worker; DBWriter owns all writes."""
from contextlib import ExitStack
from datetime import timedelta
import os
import threading

from .daily_forward import (clock_now,ensure_schema,load_model,build_model,install_model,
    candidates,prepare_day,save_baselines,claim_request,save_jev,settle,next_deadline,closed_end)
from .daily_store import DailyStore
from .daily_incremental import attached,windows,append_batch
from .daily_sources import FinmindClient,CALENDAR,INSTITUTIONAL,MARGIN
from .daily_sync import TWSE_LIMITER
from .daily_forward_client import ForwardBudget,ForwardClient,ledger_usage
from .daily_lock import forward_lock
from .db_lifecycle import DatabaseGate
from .evolution_budget import LEDGER,LIMIT
from .data import DataError,day_value
from .fugle import FugleClient
from .twse import TwseClient
from .http_client import ClientError
from .jobs import sync_reason
from . import news_digest, news_forward
from .news_forward import record_input as record_news_input

SAFE={'forward_frozen_settings_changed','forward_missing_p6_plan','daily_source_digest_changed',
    'daily_frozen_settings_changed','forward_not_predictable','daily_source_revision',
    'evolution_budget_exhausted','forward_retry_exhausted','forward_retry_expired','forward_retry_uncertain',
    'forward_retry_wait_calendar','news_policy_changed','news_input_changed','news_forward_only','missing_key','auth_disabled','http_status','network_error',
    'tls_certificate_error','tls_error','request_timeout','cancelled','database_operation_failed'}


class DailyForwardService:
    def __init__(self,writer,gate,*,now=clock_now,on_change=None,client=None,budget=None,
                 fugle=None,twse=None,finmind=None,poll_seconds=900,autostart=True,ledger_path=LEDGER,
                 news_client=None,news_budget=None):
        self.writer,self.gate,self.now=writer,gate,now;self.on_change=on_change
        self.client,self.budget=client,budget
        self.news_client,self.news_budget=news_client,news_budget
        self.ledger_path=budget.path if budget is not None else ledger_path
        self.fugle=fugle or FugleClient();self.twse=twse or TwseClient(limiter=TWSE_LIMITER);self.finmind=finmind or FinmindClient()
        self.database=DatabaseGate()
        self.poll_seconds=poll_seconds;self.event=threading.Event();self.cancel=threading.Event();self.lock=threading.Lock()
        self.sync_requested=False;self.state={'state':'idle'}
        self.worker=None
        if autostart:
            self.worker=threading.Thread(target=self._loop,name='daily-forward-worker',daemon=True);self.worker.start()

    def trigger(self,*,sync=False):
        if self.cancel.is_set():return
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
        with self.database.store(DailyStore,self.writer.path,readonly=True) as store:return fn(store)

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

    def _ensure_client(self,method='jev_ind'):
        client_name,budget_name=('client','budget') if method=='jev_ind' else ('news_client','news_budget')
        if getattr(self,client_name) is not None:return
        with self.database.admission():
            budget=getattr(self,budget_name) or ForwardBudget(path=self.ledger_path,now=self.now,
                deadline=lambda d:self._read(lambda s:next_deadline(s,d)),method=method)
            setattr(self,budget_name,budget)
            if self.cancel.is_set():
                budget.close();raise DataError('cancelled')
            setattr(self,client_name,ForwardClient(budget))

    def _calls(self):
        return sum(b.calls for b in (self.budget,self.news_budget) if b is not None)

    def _counts(self):
        def read(store):
            names={r[0] for r in store.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            return tuple(store.db.execute('SELECT count(*) FROM '+name).fetchone()[0] if name in names else 0
                         for name in ('d_forward_predictions','d_forward_outcomes'))
        return self._read(read)

    def summary(self,status='complete',**extra):
        return dict(status=status,new_predictions=0,scored_outcomes=0,jev_http_calls=0,
                    ledger_used=ledger_usage(self.ledger_path,database=self.database),ledger_limit=LIMIT,**extra)

    def cycle(self,*,sync=False,lease=None):
        self.database.check()
        if lease is not None:
            lease.require(self.writer.path)
            return self._cycle_summary(sync=sync)
        try:
            with ExitStack() as stack:
                with self.database.admission():stack.enter_context(forward_lock(self.writer.path))
                return self._cycle_summary(sync=sync)
        except DataError as error:
            if str(error)!='daily_forward_busy':raise
            return self.summary('busy')

    def _cycle_summary(self,*,sync):
        before=self._counts();calls=self._calls()
        status='complete';code=None
        try:
            result=self._cycle(sync=sync)
            if result:status=result
        except Exception as error:
            status='error'
            code=str(error) if str(error) in SAFE else sync_reason(error)
            self._emit('error',code)
        after=self._counts();summary=self.summary(status,**({'error':code} if code else {}))
        def missing(store):
            if not store.db.execute("SELECT 1 FROM sqlite_master WHERE name='d_forward_days'").fetchone():return 0
            return store.db.execute("SELECT count(*) FROM d_forward_days WHERE jev_state!='done'").fetchone()[0]
        summary['missing_jev_days']=self._read(missing)
        def missing_news(store):
            if not news_digest.exists(store,'news_forward_requests'):return 0
            return store.db.execute("SELECT count(*) FROM news_forward_requests WHERE jev_state NOT IN ('done','no_news')").fetchone()[0]
        summary['missing_news_days']=self._read(missing_news)
        if status=='complete' and (summary['missing_jev_days'] or summary['missing_news_days']):summary['status']='partial'
        summary.update(new_predictions=after[0]-before[0],scored_outcomes=after[1]-before[1],
                       jev_http_calls=self._calls()-calls)
        return summary

    def _cycle(self,*,sync=False):
        # No experiment is a normal state for installations without research data.
        with self.database.store(DailyStore,self.writer.path,readonly=True) as s:
            if s.db.execute("SELECT 1 FROM sqlite_master WHERE name='d_experiments'").fetchone() is None:return 'no_experiment'
            if s.db.execute('SELECT 1 FROM d_experiments WHERE id=4').fetchone() is None:return 'no_experiment'
        self._write(ensure_schema)
        row,bundle=self._read(load_model)
        if sync and os.environ.get('FUGLE_API_KEY'):
            try:self._sync(row)
            except DataError as error:
                if str(error)!='busy':raise
                return 'busy'  # Terminal legacy sync callback (or next poll) will retry.
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
            self._write(lambda s:record_news_input(s,day,self.now()))
            self._run_request(day,'jev_ind')
            self._run_request(day,'jev_news')
        self._write(lambda s:settle(s,self.now()))
        self._emit('idle')

    def _run_request(self,day,method):
        table='d_forward_days' if method=='jev_ind' else 'news_forward_requests'
        client_name,budget_name=('client','budget') if method=='jev_ind' else ('news_client','news_budget')
        if method=='jev_news' and not news_digest.JEV_NEWS_ENABLED:return
        saved=self._read(lambda s:s.db.execute(f'SELECT * FROM {table} WHERE day=?',(day,)).fetchone())
        if saved is None or saved['jev_state'] in ('done','failed','no_news'):return
        if getattr(self,client_name) is None and not os.environ.get('TYPESAFE_API_KEY'):return
        self._ensure_client(method)
        client,budget=getattr(self,client_name),getattr(self,budget_name)
        if saved['jev_state']=='reserved':
            state=budget.retry_status(day) if budget else 'uncertain'
            if state=='initial':state='uncertain'
            self._set_failure(day,state,'forward_retry_'+state,table)
            if state not in ('retry','wait_calendar'):return
        if budget:
            state=budget.retry_status(day)
            if state not in ('initial','retry'):
                self._set_failure(day,state,'forward_retry_'+state,table);return
        claim,save=(claim_request,save_jev) if method=='jev_ind' else (news_forward.claim_request,news_forward.save_jev)
        body=self._write(lambda s:claim(s,day))
        if body is None:return
        try:
            answer=client.predict(day,body,cancel=self.cancel)
            self._write(lambda s:save(s,day,answer,self.now))
        except ClientError as error:
            state=budget.retry_status(day) if budget else 'uncertain'
            code=error.code if error.code in SAFE else 'response_invalid'
            self._set_failure(day,state,code,table)

    def _set_failure(self,day,state,code,table='d_forward_days'):
        def write(store):
            with store.transaction():
                store.db.execute(f"UPDATE {table} SET jev_state=?,error=? WHERE day=? AND jev_state!='done'",
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

    def close(self):
        self.cancel.set();self.event.set();self.database.stop()
        for budget in (self.budget,self.news_budget):
            if budget is not None and hasattr(budget,'close'):budget.close()
