"""One-point TypeSafe client and resumable, generation-bound replay execution.

The shell should share one ActivityGate and DBWriter with sync/experiments.
All public runner commands enqueue work; no protocol thread waits on the DB.
Only committed predictions count as successes. Workers and writer are daemons.
"""
from concurrent.futures import Future
from dataclasses import dataclass, field
from decimal import Decimal
from fractions import Fraction
import hashlib
import json
import os
import ssl
from queue import Queue
import threading
import time
import urllib.error
import urllib.request

from .data import DataError
from .experiment import (MODEL, PROMPT_VERSION, canonical, experiment_row,
                         load_plan, require_final_holdout, verify_digest)
from .http_client import (ClientError, Response, _invalid_constant, _pairs,
                          secure_opener, transport_error_code)
from .replay import LABELS, Replay, persist_outcome, threshold_value
from .prompts import PROMPT_VERSIONS, instructions
from .store import now_string

URL = 'https://api.typesafe.ai/v1/systemone'
MAX_BYTES = 1024 * 1024
MAX_WORKERS = 6
_AUTH_DISABLED = threading.Event()
_HTTP_SLOTS = threading.BoundedSemaphore(MAX_WORKERS)


def request_payload(point, model=MODEL, *, prompt_version=PROMPT_VERSION):
    if not point.predictable or point.input_json is None:
        raise DataError('point_not_predictable')
    k = threshold_value(point.threshold_permille)
    state = json.loads(point.input_json)
    if (set(state) != {'clock', 'minutes_to_close', 'today', 'recent', 'prev_days'}
            or hashlib.sha256(point.input_json.encode()).hexdigest() != point.input_hash):
        raise DataError('invalid_feature_input')
    return {'state': state, 'model': model, 'questions': {'direction': {
        'type': 'choice',
        'instructions': instructions(prompt_version),
        'criteria': {'up': f'上漲 {k}‰ 或更多', 'flat': f'漲跌都小於 {k}‰',
                     'down': f'下跌 {k}‰ 或更多'}}}}


@dataclass(frozen=True)
class JevAnswer:
    choice: str = field(repr=False)
    probabilities: dict = field(repr=False)
    model_reported: str | None
    latency_seconds: float = 0.0


def validate_response(payload, model=MODEL):
    if not isinstance(payload, dict):
        raise ClientError('invalid_response')
    if 'model' in payload and payload['model'] != model:
        raise ClientError('model_mismatch')
    answers = payload.get('answers')
    direction = answers.get('direction') if isinstance(answers, dict) else None
    if not isinstance(direction, dict):
        raise ClientError('invalid_response')
    choice, probabilities = direction.get('choice'), direction.get('probabilities')
    if not isinstance(choice, str) or choice not in LABELS:
        raise ClientError('invalid_choice')
    if not isinstance(probabilities, dict) or set(probabilities) != set(LABELS):
        raise ClientError('invalid_probabilities')
    values = {}
    for label, number in probabilities.items():
        if isinstance(number, bool) or not isinstance(number, (int, float, Decimal)):
            raise ClientError('invalid_probability')
        value = Decimal(str(number))
        if not value.is_finite() or not 0 <= value <= 1:
            raise ClientError('invalid_probability')
        values[label] = Fraction(value)
    if abs(sum(values.values()) - 1) > Fraction(1, 100):
        raise ClientError('invalid_probability_sum')
    if values[choice] != max(values.values()):
        raise ClientError('choice_not_maximum')
    return JevAnswer(choice, {k: float(v) for k, v in values.items()}, payload.get('model'))


class JevClient:
    def __init__(self, *, opener=None, sleep=None, clock=time.monotonic, before_request=None):
        self._opener, self._sleep, self._clock = opener, sleep, clock
        self._before_request = before_request  # Offline tests / durable smoke quota.

    @staticmethod
    def enabled():
        return bool(os.environ.get('TYPESAFE_API_KEY')) and not _AUTH_DISABLED.is_set()

    @staticmethod
    def _check(cancel):
        if cancel.is_set():
            raise ClientError('cancelled')
        if _AUTH_DISABLED.is_set():
            raise ClientError('auth_disabled')

    def predict(self, point, *, model=MODEL, cancel=None, prompt_version=PROMPT_VERSION):
        cancel = cancel if cancel is not None else threading.Event()
        payload = canonical(request_payload(point, model, prompt_version=prompt_version)).encode()
        key = os.environ.get('TYPESAFE_API_KEY', '')
        if not key:
            raise ClientError('missing_key')
        if '\r' in key or '\n' in key:
            raise ClientError('invalid_key')
        started = self._clock()
        for attempt in range(3):
            self._check(cancel)
            while not _HTTP_SLOTS.acquire(timeout=0.05):
                self._check(cancel)
            try:
                self._check(cancel)
                response = self._request(payload, key)
                if response.status in (401, 403):
                    _AUTH_DISABLED.set()
            finally:
                _HTTP_SLOTS.release()
            self._check(cancel)
            if response.status in (429, 529) and attempt < 2:
                delay = (0.5, 1.0)[attempt]
                if self._sleep is None:
                    cancel.wait(delay)
                else:
                    self._sleep(delay)
                continue
            if response.status != 200:
                raise ClientError('http_status', response.status)
            result = validate_response(response.payload, model)
            return JevAnswer(result.choice, result.probabilities, result.model_reported,
                             self._clock() - started)
        raise AssertionError('unreachable')

    def _request(self, body, key):
        error = None
        try:
            opener = self._opener if self._opener is not None else secure_opener()
            request = urllib.request.Request(URL, data=body, method='POST', headers={
                'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json',
                'Accept': 'application/json', 'Accept-Encoding': 'identity',
                'User-Agent': 'trend-cast/0.1'})
            started = self._clock()
            if self._before_request is not None:
                self._before_request()
            try:
                response = opener.open(request, timeout=15)
            except urllib.error.HTTPError as exc:
                status = exc.code
                exc.close()
                return Response(status, None)
            with response:
                if response.status != 200:
                    return Response(response.status, None)
                size = response.headers.get('Content-Length')
                if size is not None and (not size.isdigit() or int(size) > MAX_BYTES):
                    raise ClientError('response_too_large')
                if response.headers.get('Content-Encoding', 'identity').lower() != 'identity':
                    raise ClientError('unsupported_encoding')
                chunks, total = [], 0
                while True:
                    if self._clock() - started > 15:
                        raise ClientError('request_timeout')
                    chunk = response.read(min(65536, MAX_BYTES + 1 - total))
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > MAX_BYTES:
                        raise ClientError('response_too_large')
                    chunks.append(chunk)
                if self._clock() - started > 15:
                    raise ClientError('request_timeout')
                if size is not None and total != int(size):
                    raise ClientError('truncated_response')
                result = json.loads(b''.join(chunks).decode('utf-8'), parse_float=Decimal,
                                    parse_constant=_invalid_constant, object_pairs_hook=_pairs)
                return Response(200, result)
        except ClientError as exc:
            error = ClientError(exc.code, exc.status)
        except ssl.SSLError as exc:
            error = ClientError(transport_error_code(exc))
        except (UnicodeError, ValueError, RecursionError):
            error = ClientError('invalid_json')
        except Exception as exc:
            error = ClientError(transport_error_code(exc))
        # Raise outside handlers so a transport exception (possibly containing
        # Authorization) is neither chained nor retained as __context__.
        raise error


@dataclass(eq=False)
class RunHandle:
    generation: int
    experiment_id: int
    split: str
    cancel_event: threading.Event = field(default_factory=threading.Event, repr=False)
    done: Future = field(default_factory=Future, repr=False)
    run_id: int | None = None
    n_ok: int = 0
    n_fail: int = 0
    skipped: int = 0
    unpredictable: int = 0
    pending: int = 0
    exhausted: bool = False


class JevRunner:
    """Six fixed workers, a bounded dispatch window, and one external DB owner.

    cancel() flags network work immediately and queues a DB cancellation barrier.
    Its Future acknowledges the barrier: after that no old result can commit.
    A transaction already committing is ordered before that barrier. The control
    thread never waits on this Future, including when SQLite is externally locked.
    """
    def __init__(self, writer, gate, *, client=None, on_progress=None, stop_dispatch=None):
        self.writer, self.gate = writer, gate
        self.client = client if client is not None else JevClient()
        self.on_progress = on_progress
        # Optional quota stop: drain in-flight jobs and retain their commits.
        # Cancellation remains a separate operation that discards old results.
        self.stop_dispatch = stop_dispatch
        self._jobs = Queue()
        self._lock = threading.Lock()
        self._active = None
        self._closed = False
        self.workers = [threading.Thread(target=self._worker, name=f'jev-{i}', daemon=True)
                        for i in range(MAX_WORKERS)]
        for worker in self.workers:
            worker.start()

    def start(self, experiment_id, *, split='dev', times=None):
        if split not in ('dev', 'holdout'):
            raise DataError('invalid_split_name')
        if split == 'holdout':
            require_final_holdout(experiment_id)
        with self._lock:
            if self._closed:
                raise DataError('runner_closed')
            generation = self.gate.claim('replay')
            handle = RunHandle(generation, experiment_id, split)
            self._active = handle
        future = self.writer.submit(lambda store: self._begin(store, handle, times))
        future.add_done_callback(lambda result: self._begin_failed(handle, result))
        return handle

    def _begin_failed(self, handle, result):
        error = result.exception()
        if error is not None:
            handle.cancel_event.set()
            if handle.run_id is None:
                self._finish_error(handle, error)
            else:
                def mark_failed(store):
                    with store.transaction():
                        store.db.execute('UPDATE runs SET status=?,finished_at=? WHERE id=?',
                                         ('failed', now_string(), handle.run_id))
                cleanup = self.writer.submit(mark_failed)
                cleanup.add_done_callback(lambda result: self._finish_error(handle, result.exception() or error))

    def _finish_error(self, handle, error):
        self._release(handle)
        if not handle.done.done():
            handle.done.set_exception(error)

    def cancel(self):
        with self._lock:
            handle = self._active
            if handle is not None:
                handle.cancel_event.set()
        future = self.writer.submit(lambda store: self._cancel(store, handle))
        if handle is not None:
            future.add_done_callback(lambda result: self._finish_error(handle, result.exception())
                                     if result.exception() is not None else None)
        return future

    def close(self):
        with self._lock:
            self._closed = True
        result = self.cancel()
        for _ in self.workers:
            self._jobs.put(None)
        return result

    def _release(self, handle):
        with self._lock:
            if self._active is handle:
                self._active = None
            self.gate.release('replay', handle.generation)

    def _current(self, handle):
        return self._active is handle and not handle.cancel_event.is_set()

    def _begin(self, store, handle, times):
        with store.transaction():
            row = experiment_row(store, handle.experiment_id)
            if handle.split == 'holdout':
                require_final_holdout(handle.experiment_id, row['prompt_version'])
            if row['model'] != MODEL or row['prompt_version'] not in PROMPT_VERSIONS:
                raise DataError('unsupported_experiment_version')
            verify_digest(store, row)
            plan = load_plan(store, handle.experiment_id)
            handle.replay = Replay(store, plan)
            handle.model = row['model']
            handle.prompt_version = row['prompt_version']
            if times is None:
                points = handle.replay.candidates(handle.split)
            else:
                points = tuple(handle.replay._point_time(t) for t in times)
                if any(plan.split_of(t.date().isoformat()) != handle.split for t in points):
                    raise DataError('point_outside_run_split')
            handle.points = iter(points)
            handle.seen = set()
            cursor = store.db.execute('''INSERT INTO runs
                (experiment_id,method,split,started_at,status) VALUES (?,?,?,?,?)''',
                (handle.experiment_id, 'jev', handle.split, now_string(), 'running'))
            handle.run_id = cursor.lastrowid
            store.db.execute('INSERT INTO run_scopes VALUES (?,?)', (handle.run_id, int(times is None)))
        self._pump(store, handle)

    def _pump(self, store, handle):
        if not self._current(handle):
            return self._cancel(store, handle)
        while handle.pending < MAX_WORKERS and not handle.exhausted:
            if not self._current(handle):
                return self._cancel(store, handle)
            t = next(handle.points, None)
            if t is None:
                handle.exhausted = True
                break
            point_time = handle.replay._point_time(t)
            if handle.replay.plan.split_of(point_time.date().isoformat()) != handle.split:
                raise DataError('point_outside_run_split')
            stamp = point_time.isoformat()
            if stamp in handle.seen:
                continue
            handle.seen.add(stamp)
            exists = store.db.execute('''SELECT 1 FROM predictions
                WHERE experiment_id=? AND method=? AND t=?''',
                (handle.experiment_id, 'jev', stamp)).fetchone()
            if exists:
                handle.skipped += 1
                continue
            point = handle.replay.prepare(point_time)
            if not point.predictable:
                handle.unpredictable += 1
                continue
            # A quota stops new HTTP work, not the final traversal of already
            # committed/unpredictable points. The last permit can finish a run.
            if self.stop_dispatch is not None and self.stop_dispatch.is_set():
                handle.exhausted = True
                handle.stop_reason = 'call_limit'
                break
            handle.pending += 1
            self._jobs.put((handle, point))
        if handle.exhausted and handle.pending == 0:
            self._finish(store, handle, getattr(handle, 'stop_reason', 'complete'))

    def _worker(self):
        while True:
            job = self._jobs.get()
            if job is None:
                return
            handle, point = job
            answer = None
            if not handle.cancel_event.is_set():
                try:
                    answer = self.client.predict(point, model=handle.model, cancel=handle.cancel_event,
                                                 prompt_version=handle.prompt_version)
                except Exception:
                    pass  # No upstream response/error text is persisted or emitted.
            future = self.writer.submit(lambda store, h=handle, p=point, a=answer:
                                        self._accept(store, h, p, a))
            future.add_done_callback(lambda result, h=handle: self._write_failed(h, result))

    def _write_failed(self, handle, future):
        if future.exception() is not None and not handle.done.done():
            status = 'cancelled' if handle.cancel_event.is_set() else 'failed'
            handle.cancel_event.set()
            # Failed commit contributes zero successes. Persist terminal status
            # in a separate writer job, allowing lock/trigger failures to surface.
            ending = self.writer.submit(lambda store: self._finish(store, handle, status))
            ending.add_done_callback(lambda result: self._begin_failed(handle, result))

    def _accept(self, store, handle, point, answer):
        if not self._current(handle):
            return
        with store.transaction():
            # BEGIN IMMEDIATE may have waited for an external writer: recheck.
            if not self._current(handle):
                return
            persist_outcome(store, point, handle.replay.outcome(point))
            if answer is not None:
                store.db.execute('''INSERT INTO predictions VALUES (?,?,?,?,?,?,?,?,?,?)''',
                    (handle.experiment_id, 'jev', point.t.isoformat(), handle.run_id,
                     point.input_hash, point.input_json, answer.choice,
                     canonical(answer.probabilities), answer.model_reported, now_string()))
            store.db.execute('UPDATE runs SET n_ok=n_ok+?, n_fail=n_fail+? WHERE id=?',
                             (int(answer is not None), int(answer is None), handle.run_id))
            if not self._current(handle):
                raise DataError('cancelled')
        handle.n_ok += int(answer is not None)
        handle.n_fail += int(answer is None)
        handle.pending -= 1
        self._progress(handle, 'running')
        self._pump(store, handle)

    @staticmethod
    def _summary(handle, status):
        return dict(run_id=handle.run_id, status=status, n_ok=handle.n_ok, n_fail=handle.n_fail,
                    skipped=handle.skipped, unpredictable=handle.unpredictable,
                    experiment_id=handle.experiment_id, generation=handle.generation,
                    method='jev', split=handle.split)

    def _progress(self, handle, status):
        if self.on_progress is not None:
            try:
                self.on_progress(self._summary(handle, status))
            except Exception:
                pass  # An output sink cannot undo a committed prediction.

    def _finish(self, store, handle, status):
        if handle.done.done():
            return
        if handle.run_id is not None:
            with store.transaction():
                store.db.execute('UPDATE runs SET status=?,finished_at=? WHERE id=?',
                                 (status, now_string(), handle.run_id))
        self._release(handle)
        self._progress(handle, status)
        handle.done.set_result(self._summary(handle, status))

    def _cancel(self, store, handle):
        if handle is not None and not handle.done.done():
            self._finish(store, handle, 'cancelled')
