"""One advisory process lock for the entire daily forward workflow.

Never unlink the lock file: a second inode would permit simultaneous owners.
The kernel releases flock when the descriptor closes or the process dies.
"""
from contextlib import contextmanager
import fcntl
import os
from pathlib import Path

from .data import DataError


class Lease:
    def __init__(self,db_path,fd):
        self.db_path,self.fd=Path(db_path).resolve(),fd

    def require(self,db_path):
        if self.fd is None or Path(db_path).resolve()!=self.db_path:
            raise DataError('daily_forward_lock_required')


@contextmanager
def forward_lock(db_path):
    path=Path(db_path).resolve()
    lock_path=path.with_name(path.name+'.daily-forward.lock')
    fd=None;lease=None
    try:
        try:
            fd=os.open(lock_path,os.O_RDWR|os.O_CREAT|os.O_CLOEXEC|os.O_NOFOLLOW,0o600)
            fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            raise DataError('daily_forward_busy') from None
        except OSError:
            raise DataError('daily_forward_lock_failed') from None
        lease=Lease(path,fd)
        yield lease
    finally:
        if lease is not None:lease.fd=None
        if fd is not None:os.close(fd)
