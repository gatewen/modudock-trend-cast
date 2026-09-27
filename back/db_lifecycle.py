"""Admission/abort barrier for runtime SQLite connections, including readers.

Readonly WAL connections can create sidecars too. Track constructors as well as
open handles; stop admits no new handle, and wait acknowledges owner-thread close.
"""
from contextlib import contextmanager
import sqlite3
import threading
import time

from .data import DataError


class Connection:
    def __init__(self, raw, gate):
        self.raw, self.gate = raw, gate
        self.timeout = raw.execute('PRAGMA busy_timeout').fetchone()[0] / 1000
        # Poll a busy statement so close can interrupt lock waits promptly.
        raw.execute('PRAGMA busy_timeout=25')
        raw.set_progress_handler(lambda: int(gate.stopped.is_set()), 100)

    def __getattr__(self, name):return getattr(self.raw, name)
    @property
    def row_factory(self):return self.raw.row_factory
    @row_factory.setter
    def row_factory(self, value):self.raw.row_factory=value

    def execute(self, *args, **kwargs):
        end=time.monotonic()+self.timeout
        while True:
            self.gate.check()
            try:return self.raw.execute(*args, **kwargs)
            except sqlite3.OperationalError as error:
                if getattr(error,'sqlite_errorcode',None) not in (sqlite3.SQLITE_BUSY,sqlite3.SQLITE_LOCKED) or time.monotonic()>=end:raise
                # Retry only a single failed statement, never a partially run script/batch.
                self.gate.stopped.wait(.005)

    def executemany(self, *args, **kwargs):
        self.gate.check();return self.raw.executemany(*args, **kwargs)
    def executescript(self, *args, **kwargs):
        self.gate.check();return self.raw.executescript(*args, **kwargs)
    def commit(self):
        self.gate.check();return self.raw.commit()


class DatabaseGate:
    def __init__(self):
        self.stopped=threading.Event();self.condition=threading.Condition()
        self.active=0;self.connections=set()

    def check(self):
        if self.stopped.is_set():raise DataError('cancelled')

    @contextmanager
    def admission(self):
        with self.condition:
            self.check();self.active+=1
        try:yield
        finally:
            with self.condition:self.active-=1;self.condition.notify_all()

    @contextmanager
    def protect(self, raw):
        with self.condition:
            self.check();self.connections.add(raw)
        try:yield Connection(raw,self)
        finally:
            with self.condition:self.connections.discard(raw)

    @contextmanager
    def store(self, factory, *args, **kwargs):
        with self.admission():
            store=factory(*args, **kwargs)
            try:
                with self.protect(store.db) as connection:
                    store.db=connection
                    yield store
            finally:store.close()

    @contextmanager
    def connection(self, *args, **kwargs):
        with self.admission():
            raw=sqlite3.connect(*args, **kwargs)
            try:
                with self.protect(raw) as connection:
                    try:
                        yield connection
                        connection.commit()
                    except BaseException:
                        raw.rollback();raise
            finally:raw.close()

    def stop(self):
        with self.condition:
            self.stopped.set()
            for raw in self.connections:
                try:raw.interrupt()
                except sqlite3.Error:pass

    def wait(self, timeout):
        with self.condition:return self.condition.wait_for(lambda:self.active==0,timeout)
