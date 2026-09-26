"""One daemon owns the write connection; submit/close never wait on SQLite."""
from concurrent.futures import Future
from queue import Queue
import threading

from .data import DataError
from .http_client import ClientError
from .store import Store


class DBWriter:
    def __init__(self, path):
        self.path = path
        self._queue = Queue()
        self._lock = threading.Lock()
        self._closed = False
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

    def close(self):
        with self._lock:
            if not self._closed:
                self._closed = True
                self._queue.put(None)

    def _work(self):
        store = None
        try:
            store = Store(self.path)
        except Exception:
            pass
        if store is None:
            self.ready.set_exception(DataError('database_open_failed'))
        else:
            self.ready.set_result(None)
        try:
            while True:
                item = self._queue.get()
                if item is None:
                    return
                operation, future = item
                if not future.set_running_or_notify_cancel():
                    continue
                error = None
                try:
                    if store is None:
                        raise DataError('database_open_failed')
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
        finally:
            if store is not None:
                store.close()
