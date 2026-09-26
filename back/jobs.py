"""Cancelable local replay and network sync, sharing one DBWriter/ActivityGate."""
from concurrent.futures import Future
from dataclasses import dataclass, field
from datetime import datetime
from queue import Queue
import threading

from .baselines import Baselines, METHODS
from .data import DataError, TAIPEI
from .experiment import canonical, experiment_row, load_plan, require_final_holdout, verify_digest
from .fugle import FugleClient
from .http_client import ClientError
from .replay import Replay, observation, persist_outcome
from .store import now_string
from .sync import history_start, month_ranges
from .twse import TwseClient


@dataclass(eq=False)
class LocalRun:
    generation: int
    experiment_id: int
    split: str
    method: str
    cancel_event: threading.Event = field(default_factory=threading.Event)
    done: Future = field(default_factory=Future)
    run_id: int | None = None
    n_ok: int = 0
    n_fail: int = 0
    skipped: int = 0
    unpredictable: int = 0


class BaselineRunner:
    """One point per DB queue item: cancel cannot be stuck behind a whole replay."""
    def __init__(self, writer, gate, *, on_progress=None):
        self.writer, self.gate, self.on_progress = writer, gate, on_progress
        self._lock = threading.Lock()
        self._active = None
        self._closed = False

    def start(self, experiment_id, *, method, split='dev'):
        if method not in METHODS or split not in ('dev', 'holdout'):
            raise DataError('invalid_run')
        if split == 'holdout':
            require_final_holdout(experiment_id)
        with self._lock:
            if self._closed:
                raise DataError('runner_closed')
            handle = LocalRun(self.gate.claim('replay'), experiment_id, split, method)
            self._active = handle
        self._queue(handle, lambda store: self._begin(store, handle))
        return handle

    def _current(self, handle):
        return self._active is handle and not handle.cancel_event.is_set()

    def _queue(self, handle, operation):
        future = self.writer.submit(operation)
        future.add_done_callback(lambda result: self._failed(handle, result))

    def _failed(self, handle, future):
        error = future.exception()
        if error is None or handle.done.done():
            return
        status = 'cancelled' if handle.cancel_event.is_set() else 'failed'
        handle.cancel_event.set()
        def cleanup(store):
            if handle.run_id is not None:
                with store.transaction():
                    store.db.execute('UPDATE runs SET status=?,finished_at=? WHERE id=?',
                                     (status, now_string(), handle.run_id))
        ending = self.writer.submit(cleanup)
        def finished(_):
            self._release(handle)
            if not handle.done.done():
                if status == 'cancelled':
                    handle.done.set_result(self.summary(handle, status))
                else:
                    handle.done.set_exception(DataError('replay_failed'))
        ending.add_done_callback(finished)

    def _begin(self, store, handle):
        if not self._current(handle):
            return self._finish(store, handle, 'cancelled')
        with store.transaction():
            row = experiment_row(store, handle.experiment_id)
            if handle.split == 'holdout':
                require_final_holdout(handle.experiment_id, row['prompt_version'])
            verify_digest(store, row)
            handle.engine = Replay(store, load_plan(store, handle.experiment_id))
            handle.points = iter(handle.engine.candidates(handle.split))
            handle.history = []
            handle.warmup = iter(handle.engine.candidates('dev')) if handle.split == 'holdout' else iter(())
            handle.run_id = store.db.execute('''INSERT INTO runs
                (experiment_id,method,split,started_at,status) VALUES (?,?,?,?,?)''',
                (handle.experiment_id, handle.method, handle.split, now_string(), 'running')).lastrowid
            store.db.execute('INSERT INTO run_scopes VALUES (?,1)', (handle.run_id,))
            if not self._current(handle):
                raise DataError('cancelled')
        self._queue(handle, lambda db: self._step(db, handle))

    def _step(self, store, handle):
        if not self._current(handle):
            return self._finish(store, handle, 'cancelled')
        t = next(handle.warmup, None)
        if t is not None:
            point = handle.engine.prepare(t)
            outcome = handle.engine.outcome(point)
            if point.predictable and outcome.scorable:
                handle.history.append(observation(point, outcome))
            self._queue(handle, lambda db: self._step(db, handle))
            return
        t = next(handle.points, None)
        if t is None:
            return self._finish(store, handle, 'complete')
        point = handle.engine.prepare(t)
        outcome = handle.engine.outcome(point)
        old = store.db.execute('''SELECT input_hash,input_json FROM predictions
            WHERE experiment_id=? AND method=? AND t=?''',
            (handle.experiment_id, handle.method, t.isoformat())).fetchone()
        answer = Baselines(handle.engine, handle.history).predict(handle.method, point) if point.predictable and old is None else None
        if old is not None and tuple(old) != (point.input_hash, point.input_json):
            raise DataError('prediction_input_mismatch')
        success = int(answer is not None and answer.answer is not None)
        failed = int(point.predictable and old is None and not success)
        with store.transaction():
            if not self._current(handle):
                return
            persist_outcome(store, point, outcome)
            if success:
                store.db.execute('INSERT INTO predictions VALUES (?,?,?,?,?,?,?,?,?,?)',
                    (handle.experiment_id, handle.method, t.isoformat(), handle.run_id,
                     point.input_hash, point.input_json, answer.answer, canonical(answer.probabilities), None, now_string()))
            store.db.execute('UPDATE runs SET n_ok=n_ok+?,n_fail=n_fail+? WHERE id=?',
                             (success, failed, handle.run_id))
            if not self._current(handle):
                raise DataError('cancelled')
        handle.n_ok += success
        handle.n_fail += failed
        handle.skipped += int(old is not None)
        handle.unpredictable += int(not point.predictable)
        if point.predictable and outcome.scorable:
            handle.history.append(observation(point, outcome))
        self._progress(handle, 'running')
        self._queue(handle, lambda db: self._step(db, handle))

    @staticmethod
    def summary(handle, status):
        return {key: getattr(handle, key) for key in ('experiment_id', 'generation', 'method',
            'split', 'run_id', 'n_ok', 'n_fail', 'skipped', 'unpredictable')} | {'status': status}

    def _progress(self, handle, status):
        if self.on_progress:
            self.on_progress(self.summary(handle, status))

    def _release(self, handle):
        with self._lock:
            if self._active is handle:
                self._active = None
            self.gate.release('replay', handle.generation)

    def _finish(self, store, handle, status):
        if handle.done.done():
            return
        if handle.run_id is not None:
            with store.transaction():
                store.db.execute('UPDATE runs SET status=?,finished_at=? WHERE id=?',
                                 (status, now_string(), handle.run_id))
        self._release(handle)
        self._progress(handle, status)
        handle.done.set_result(self.summary(handle, status))

    def cancel(self):
        with self._lock:
            handle = self._active
            if handle is not None:
                handle.cancel_event.set()
        future = self.writer.submit(lambda store: self._finish(store, handle, 'cancelled') if handle is not None else None)
        if handle is not None:
            future.add_done_callback(lambda result: self._failed(handle, result))
        return future

    def close(self):
        self._closed = True
        return self.cancel()


@dataclass(eq=False)
class SyncRun:
    generation: int
    cancel_event: threading.Event = field(default_factory=threading.Event)
    done: Future = field(default_factory=Future)
    n_ok: int = 0
    n_fail: int = 0
    reasons: set = field(default_factory=set)


def sync_reason(exc):
    # Only fixed local codes leave the worker, never exception text or bodies.
    if isinstance(exc, ClientError):
        return exc.code if exc.code in {'tls_certificate_error', 'tls_error',
            'auth_disabled', 'missing_key', 'invalid_key', 'network_error',
            'request_timeout', 'http_status'} else 'invalid_response'
    return 'database_operation_failed' if str(exc) == 'database_operation_failed' else 'operation_failed'


class SyncRunner:
    """A fixed network worker; all Store mutations execute on DBWriter."""
    def __init__(self, writer, gate, *, fugle=None, twse=None, on_progress=None):
        self.writer, self.gate = writer, gate
        self.fugle, self.twse = fugle or FugleClient(), twse or TwseClient()
        self.on_progress = on_progress
        self._active = None
        self._closed = False
        self._lock = threading.Lock()
        self._jobs = Queue(maxsize=2)
        self.worker = threading.Thread(target=self._work, name='sync-worker', daemon=True)
        self.worker.start()

    def start(self, *, symbol='2330', today=None, start=None):
        with self._lock:
            if self._closed:
                raise DataError('runner_closed')
            handle = SyncRun(self.gate.claim('sync'))
            self._active = handle
        today = today or datetime.now(TAIPEI).date()
        self._jobs.put_nowait((handle, symbol, today, start or history_start(today).isoformat()))
        return handle

    def _current(self, handle):
        return self._active is handle and not handle.cancel_event.is_set()

    def _write(self, handle, operation):
        def guarded(store):
            if not self._current(handle):
                raise DataError('cancelled')
            return operation(store)
        return self.writer.submit(guarded).result()

    def _progress(self, handle, status):
        summary = {'generation': handle.generation, 'status': status,
                   'n_ok': handle.n_ok, 'n_fail': handle.n_fail,
                   'reasons': sorted(handle.reasons)}
        if self.on_progress:
            self.on_progress(summary)
        return summary

    def _work(self):
        while True:
            job = self._jobs.get()
            if job is None:
                return
            handle, symbol, today, start = job
            aborted = False
            stop = False
            try:
                for begin, end in reversed(month_ranges(start, today.isoformat())):
                    if not self._current(handle):
                        break
                    if not self._write(handle, lambda db: db.should_fetch(symbol, begin[:7], today=today)):
                        continue
                    for kind in ('daily', 'corp', 'bars'):
                        if not self._current(handle):
                            break
                        try:
                            if kind == 'corp':
                                batch = self.twse.events(symbol, begin, end)
                                self._write(handle, lambda db: db.write_corp(batch))
                            else:
                                batch = self.fugle.candles(symbol, begin, end, 'D' if kind == 'daily' else '1')
                                self._write(handle, lambda db: db.write_candles(batch, today=today))
                            handle.n_ok += 1
                            self._progress(handle, 'running')
                        except ClientError as exc:
                            handle.n_fail += 1
                            handle.reasons.add(sync_reason(exc))
                            self._write(handle, lambda db: db.record_failure(symbol, begin, end, kind, exc.status))
                            if exc.code in ('auth_disabled', 'missing_key', 'invalid_key'):
                                stop = True
                                break
                        except DataError as exc:
                            if not self._current(handle):
                                break
                            handle.n_fail += 1
                            handle.reasons.add(sync_reason(exc))
                            self._write(handle, lambda db: db.record_failure(symbol, begin, end, kind, None))
                    if stop:
                        break
            except Exception as exc:
                aborted = True
                handle.reasons.add(sync_reason(exc))
            status = ('partial' if handle.n_ok else 'failed') if handle.n_fail or aborted else 'complete'
            if not self._current(handle):
                status = 'cancelled'
            with self._lock:
                if self._active is handle:
                    self._active = None
                self.gate.release('sync', handle.generation)
            handle.done.set_result(self._progress(handle, status))

    def close(self):
        with self._lock:
            if self._closed:
                return
            self._closed = True
            if self._active is not None:
                self._active.cancel_event.set()
        self._jobs.put_nowait(None)
