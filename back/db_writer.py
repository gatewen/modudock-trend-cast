"""One daemon owns the write connection; submit/close never wait on SQLite."""
from concurrent.futures import Future
from queue import Queue, Empty
import threading

from .data import DataError
from .http_client import ClientError
from .store import Store
from .db_lifecycle import DatabaseGate


class DBWriter:
    def __init__(self, path):
        self.path = path
        self._queue = Queue()
        self._lock = threading.Lock()
        self._closed = False
        self.database = DatabaseGate()
        self.ready = Future()
        self.thread = threading.Thread(target=self._work, name='db-writer', daemon=True)
        self.thread.start()

    def submit(self, operation):
        result = Future()
        with self._lock:
            if self._closed:
                result.set_exception(DataError('writer_closed'))
            else:
                self._queue.put((operation, result))
        return result

    def close(self, *, abort=False):
        pending=[]
        with self._lock:
            if abort:
                self.database.stop()
                while True:
                    try:
                        item=self._queue.get_nowait()
                        if item is not None:pending.append(item[1])
                    except Empty:break
            if not self._closed:
                self._closed = True
                self._queue.put(None)
            elif abort:self._queue.put(None)
        # Future callbacks may submit cleanup work: never invoke them under _lock.
        for future in pending:
            if future.set_running_or_notify_cancel():future.set_exception(DataError('writer_closed'))

    def _work(self):
        try:
            with self.database.store(Store,self.path) as store:self._process(store)
        except Exception:
            if not self.ready.done():self.ready.set_exception(DataError('database_open_failed'))

    def _process(self, store):
        self.ready.set_result(None)
        while True:
            item = self._queue.get()
            if item is None:
                return
            operation, future = item
            if not future.set_running_or_notify_cancel():
                continue
            error = None
            try:
                self.database.check()
                value = operation(store)
            except ClientError as exc:
                error = ClientError(exc.code, exc.status)
            except DataError as exc:
                error = DataError(str(exc))
            except Exception:
                # SQLite and injected transport errors must never reach UI.
                error = DataError('database_operation_failed')
            if error is None:
                future.set_result(value)
            else:
                future.set_exception(error)
