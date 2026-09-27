"""Fixed five-method forward workflow; explicit reveal remains a separate op."""
from concurrent.futures import Future
from dataclasses import dataclass, field
import threading

from .baselines import METHODS
from .data import DataError
from .experiment import ActivityGate
from .forward import enroll, require_complete, selected
from .jevcast import JevRunner
from .jobs import BaselineRunner


@dataclass
class ForwardHandle:
    generation: int
    done: Future = field(default_factory=Future)
    cancel: threading.Event = field(default_factory=threading.Event)


class ForwardRunner:
    def __init__(self, writer, gate, *, client=None, on_progress=None):
        self.writer, self.gate, self.client, self.on_progress = writer, gate, client, on_progress
        self._lock = threading.Lock()
        self._active = self.jev = self.baselines = self.thread = None
        self.closed = False

    def start(self, experiment_id=1):
        if type(experiment_id) is not int or experiment_id != 1:
            raise DataError('forward_settings_changed')
        with self._lock:
            if self.closed:
                raise DataError('runner_closed')
            handle = ForwardHandle(self.gate.claim('replay'))
            self._active = handle
        self.thread = threading.Thread(target=self._run, args=(handle,), name='forward-runner', daemon=True)
        self._emit(handle, 'running')
        self.thread.start()
        return handle

    def _emit(self, handle, status):
        if self.on_progress:
            try:
                self.on_progress(dict(status=status, generation=handle.generation,
                    experiment_id=1, method='all', split='forward'))
            except Exception:
                pass

    def _run(self, handle):
        error = None
        def check():
            if handle.cancel.is_set():
                raise DataError('cancelled')
        try:
            def prepare(store):
                check()
                with store.transaction():
                    if not enroll(store):
                        raise DataError('forward_empty')
            self.writer.submit(prepare).result()
            local = ActivityGate()
            with self._lock:
                self.baselines = BaselineRunner(self.writer, local)
                self.jev = JevRunner(self.writer, local, client=self.client)
            self._emit(handle, 'running')
            for method in METHODS:
                with self._lock:
                    check()
                    run = self.baselines.start(1, method=method, split='forward')
                run.done.result()
            with self._lock:
                check()
                run = self.jev.start(1, split='forward')
            run.done.result()
            check()
            from .evolution_forward import run_forward_vol
            self.writer.submit(lambda store: run_forward_vol(store, cancel=handle.cancel)).result()
            check()
            def verify(store):
                from .forward import active_days
                require_complete(store, selected(store), active_days(store))
            self.writer.submit(verify).result()
        except Exception as exc:
            error = exc
        finally:
            if self.jev:
                try: self.jev.close().result()
                except Exception: pass  # Shutdown may have closed the writer.
                for worker in self.jev.workers: worker.join(timeout=16)
            if self.baselines:
                try: self.baselines.close().result()
                except Exception: pass
            with self._lock:
                self._active = None
                self.gate.release('replay', handle.generation)
        status = 'cancelled' if handle.cancel.is_set() else 'failed' if error else 'complete'
        self._emit(handle, status)
        if error and status != 'cancelled':
            handle.done.set_exception(error)
        else:
            handle.done.set_result({'status': status})

    def cancel(self):
        with self._lock:
            if self._active:
                self._active.cancel.set()
                if self.jev: self.jev.cancel()
                if self.baselines: self.baselines.cancel()

    def close(self):
        with self._lock:
            self.closed = True
        self.cancel()
