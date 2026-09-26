"""Shell application: bounded read worker, shared write owner and two activities."""
from datetime import datetime
import json
import os
from queue import Queue, Empty, Full
import threading

from .baselines import METHODS
from .data import DataError, TAIPEI
from .experiment import ActivityGate, create_experiment, experiment_row, reveal_holdout
from .http_client import _AUTH_LOCK, _DISABLED
from .jevcast import JevRunner, JevClient, _AUTH_DISABLED
from .jobs import BaselineRunner, SyncRunner
from .report import build_report, current_experiment, day_view, status_view
from .store import Store

ERRORS = {'busy', 'confirmation_required', 'experiment_required', 'stale_experiment',
    'invalid_request', 'invalid_run', 'missing_key', 'auth_disabled', 'unknown_operation',
    'database_operation_failed', 'database_open_failed', 'frozen_data_changed',
    'unfinalized_month', 'insufficient_warmup', 'unknown_corporate_action',
    'missing_warmup', 'incomplete_trading_day', 'range_outside_calendar',
    'insufficient_calendar_for_split',
    'incomplete_data', 'invalid_experiment_range', 'invalid_threshold', 'packet_too_large'}
SYMBOL = '2330'


def key_state():
    with _AUTH_LOCK:
        fugle_disabled = 'api.fugle.tw' in _DISABLED
    return {'fugle': 'invalid' if fugle_disabled else 'available' if os.environ.get('FUGLE_API_KEY') else 'missing',
            'typesafe': 'invalid' if _AUTH_DISABLED.is_set() else 'available' if os.environ.get('TYPESAFE_API_KEY') else 'missing'}


class Application:
    def __init__(self, writer, outbox, seq, *, gate=None, jev_client=None, fugle=None, twse=None, symbol=SYMBOL):
        self.writer, self.outbox, self.seq = writer, outbox, seq
        self.gate = gate or ActivityGate()
        self.symbol = symbol
        self.closed = False
        self._lock = threading.RLock()
        self._view_generation = 0
        self._experiment = None
        self._metadata = {'status': 'loading'}
        self._sync = {'status': 'idle'}
        self._replay = {'status': 'idle'}
        self._reads = Queue(maxsize=8)
        self.reader = threading.Thread(target=self._read_loop, name='view-reader', daemon=True)
        self.reader.start()
        self.jev = JevRunner(writer, self.gate, client=jev_client, on_progress=self._on_replay)
        self.baselines = BaselineRunner(writer, self.gate, on_progress=self._on_replay)
        self.sync = SyncRunner(writer, self.gate, fugle=fugle, twse=twse, on_progress=self._on_sync)

    def emit(self, body):
        if self.closed:
            return
        try:
            self.outbox.put({'t': 'msg', 'seq': self.seq, 'body': body})
        except (ValueError, TypeError):
            self.outbox.put({'t': 'msg', 'seq': self.seq,
                'body': {'op': 'error', 'code': 'packet_too_large', 'request_id': body.get('request_id')}})

    def error(self, exc, request_id=None):
        code = str(exc) if str(exc) in ERRORS else 'operation_failed'
        self.emit({'op': 'error', 'code': code, 'request_id': request_id})

    def _status(self, request_id=None):
        with self._lock:
            progress = dict(self._replay)
            # Running a holdout never grants permission to display its results,
            # including successes, failures, skipped points or completion totals.
            if progress.get('split') == 'holdout' and self._metadata.get('holdout', {}).get('state') != 'revealed':
                progress = {k: progress[k] for k in ('status', 'method', 'split') if k in progress}
            body = {**self._metadata, 'op': 'status', 'keys': key_state(),
                    'sync': dict(self._sync), 'replay': progress,
                    'busy': {'sync': self.gate.busy('sync'), 'replay': self.gate.busy('replay')},
                    'request_id': request_id}
        self.emit(body)

    def _on_sync(self, progress):
        with self._lock:
            if progress['generation'] < self._sync.get('generation', 0):
                return
            if (progress['generation'] == self._sync.get('generation') and progress['status'] == 'running'
                    and self._sync.get('status') in ('complete', 'partial', 'failed', 'cancelled')):
                return
            self._sync = progress
        self._status()

    def _on_replay(self, progress):
        with self._lock:
            if progress['generation'] < self._replay.get('generation', 0):
                return
            if (progress['generation'] == self._replay.get('generation') and progress['status'] == 'running'
                    and self._replay.get('status') in ('complete', 'failed', 'cancelled')):
                return
            self._replay = progress
        self._status()

    def start(self):
        self._status()
        self.request({'op': 'status'})
        if key_state()['fugle'] == 'available':
            self.request({'op': 'sync'})

    def _queue_read(self, body):
        with self._lock:
            item = (self._view_generation, self._experiment, dict(body))
        try:
            self._reads.put_nowait(item)
        except Full:
            self.error(DataError('busy'), body.get('request_id'))

    def _read_status(self, store, experiment_id):
        result = status_view(store, experiment_id=experiment_id)
        result['data_range'] = dict(store.db.execute('''SELECT min(day) first_day,max(day) last_day
            FROM daily WHERE symbol=?''', (self.symbol,)).fetchone())
        if result['status'] == 'ok':
            row = experiment_row(store, result['experiment_id'])
            symbol = json.loads(row['config_json'])['symbol']
            # Date metadata is supplied only for the part already authorized.
            last = row['hold_end'] if result['holdout']['state'] == 'revealed' else row['dev_end']
            result['days'] = [r[0] for r in store.db.execute('''SELECT day FROM daily
                WHERE symbol=? AND day BETWEEN ? AND ? ORDER BY day''', (symbol, row['dev_start'], last))]
            result['threshold_permille'] = row['threshold_permille']
        return result

    def _read_loop(self):
        while True:
            item = self._reads.get()
            if item is None:
                return
            generation, experiment_id, body = item
            if self.closed or generation != self._view_generation:
                continue
            try:
                with Store(self.writer.path, readonly=True) as store:
                    op = body['op']
                    if op == 'status':
                        value = self._read_status(store, experiment_id)
                    elif op == 'day':
                        value = day_view(store, body.get('date'), experiment_id=experiment_id)
                    else:
                        value = build_report(store, experiment_id=experiment_id)
                with self._lock:
                    if self.closed or generation != self._view_generation:
                        continue
                    if op == 'status':
                        self._metadata = value
                        self._experiment = value.get('experiment_id')
                        self._status(body.get('request_id'))
                    else:
                        self.emit({**value, 'op': op, 'request_id': body.get('request_id')})
            except Exception as exc:
                self.error(exc, body.get('request_id'))

    def request(self, body):
        if self.closed or not isinstance(body, dict):
            return
        request_id = body.get('request_id')
        if request_id is not None and (type(request_id) is not int or not 0 <= request_id < 2**53):
            return self.error(DataError('invalid_request'))
        op = body.get('op')
        try:
            if op in ('status', 'day', 'report'):
                self._queue_read(body)
            elif op == 'sync':
                state = key_state()['fugle']
                if state != 'available':
                    raise DataError('auth_disabled' if state == 'invalid' else 'missing_key')
                handle = self.sync.start(symbol=self.symbol)
                self._on_sync({'status': 'running', 'generation': handle.generation})
            elif op == 'cancel':
                self.jev.cancel()
                self.baselines.cancel()
                with self._lock:
                    if self.gate.busy('replay'):
                        self._replay = {**self._replay, 'status': 'cancelling'}
                self._status(request_id)
            elif op == 'run':
                experiment_id = self._bound_experiment(body)
                method, split = body.get('method'), body.get('split')
                if method not in ('jev', *METHODS) or split not in ('dev', 'holdout'):
                    raise DataError('invalid_run')
                if method == 'jev':
                    state = key_state()['typesafe']
                    if state != 'available':
                        raise DataError('auth_disabled' if state == 'invalid' else 'missing_key')
                    handle = self.jev.start(experiment_id, split=split)
                else:
                    handle = self.baselines.start(experiment_id, method=method, split=split)
                self._on_replay(dict(status='running', generation=handle.generation,
                    experiment_id=experiment_id, method=method, split=split))
                handle.done.add_done_callback(lambda future: self._run_done(future))
            elif op == 'reveal':
                experiment_id = self._bound_experiment(body)
                if body.get('confirmed') is not True:
                    raise DataError('confirmation_required')
                result = self.writer.submit(lambda store: reveal_holdout(store, experiment_id))
                result.add_done_callback(lambda future: self._changed(future, request_id))
            elif op == 'new_experiment':
                if body.get('confirmed') is not True:
                    raise DataError('confirmation_required')
                if 'experiment_id' in body and body['experiment_id'] != self._experiment:
                    raise DataError('stale_experiment')
                reservation = self.gate.claim('create')
                result = self.writer.submit(lambda store: self._create(store, body, reservation))
                result.add_done_callback(lambda future: self._changed(future, request_id))
                result.add_done_callback(lambda future: self.gate.release('create', reservation))
            else:
                raise DataError('unknown_operation')
        except Exception as exc:
            self.error(exc, request_id)

    def _bound_experiment(self, body):
        with self._lock:
            current = self._experiment
        if current is None:
            raise DataError('experiment_required')
        if body.get('experiment_id', current) != current:
            raise DataError('stale_experiment')
        return current

    def _create(self, store, body, reservation):
        # Reservation begins on admission, spanning the queue wait and checks.
        today = datetime.now(TAIPEI).date()
        days = [r[0] for r in store.db.execute('SELECT day FROM daily WHERE symbol=? ORDER BY day', (self.symbol,))]
        final = {}
        for day in days:
            if day[:7] not in final:
                final[day[:7]] = not store.should_fetch(self.symbol, day[:7], today=today)
        usable = [day for day in days if final[day[:7]]]
        if len(usable) < 27:
            raise DataError('insufficient_warmup')
        return create_experiment(store, self.gate, symbol=self.symbol, start=body.get('start', usable[25]),
            end=body.get('end', usable[-1]), threshold_permille=body.get('threshold_permille', 3),
            today=today, reservation=reservation)

    def _changed(self, future, request_id):
        if future.exception() is not None:
            return self.error(future.exception(), request_id)
        with self._lock:
            self._view_generation += 1
            value = future.result()
            if isinstance(value, dict) and 'id' in value:
                self._experiment = value['id']
                self._replay = {'status': 'idle'}
            self._metadata = {'status': 'loading'}
        self.request({'op': 'status', 'request_id': request_id})
        self.request({'op': 'report'})

    def _run_done(self, future):
        if future.exception() is not None:
            with self._lock:
                self._replay = {**self._replay, 'status': 'failed'}
            self.error(future.exception())
        self.request({'op': 'status'})

    def close(self):
        self.closed = True
        self.jev.close()
        self.baselines.close()
        self.sync.close()
        while True:
            try:
                self._reads.get_nowait()
            except Empty:
                break
        self._reads.put_nowait(None)
        self.writer.close()
