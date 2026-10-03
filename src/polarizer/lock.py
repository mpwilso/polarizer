"""The one exclusive ledger lock, on ledger.jsonl.lock (docs/LEDGER-SPEC.md, The lock).

fcntl.flock on Unix, msvcrt.locking on byte 0 on Windows. Waiting is a loop of non-blocking
attempts every 50 ms, because msvcrt.locking has no timed wait.
"""

import os
import sys
import time
from pathlib import Path

POLL = 0.05
_BINARY = getattr(os, "O_BINARY", 0)

if sys.platform == "win32":
    import msvcrt

    def _try_lock(fd: int) -> bool:
        os.lseek(fd, 0, os.SEEK_SET)
        try:
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        except OSError:
            return False
        return True

    def _unlock(fd: int) -> None:
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)

else:
    import fcntl

    def _try_lock(fd: int) -> bool:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        return True

    def _unlock(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_UN)


class LedgerLock:
    def __init__(self, fd: int):
        self._fd = fd
        self.held = False

    @classmethod
    def create(cls, path: Path) -> "LedgerLock":
        """Open the lock file, creating it (mode 0600) if needed. Writers and repair use this."""
        return cls(os.open(path, os.O_RDWR | os.O_CREAT | _BINARY, 0o600))

    @classmethod
    def open_existing(cls, path: Path) -> "LedgerLock | None":
        """Open an existing lock file read-only, or return None. verify uses this, so it never
        creates the file."""
        try:
            return cls(os.open(path, os.O_RDONLY | _BINARY))
        except FileNotFoundError:
            return None

    def acquire(self, wait: float | None) -> bool:
        """Take the lock, waiting up to `wait` seconds, or forever when wait is None."""
        deadline = None if wait is None else time.monotonic() + wait
        while not _try_lock(self._fd):
            if deadline is not None and time.monotonic() >= deadline:
                return False
            time.sleep(POLL)
        self.held = True
        return True

    def release(self) -> None:
        if self.held:
            _unlock(self._fd)
            self.held = False

    def close(self) -> None:
        self.release()
        os.close(self._fd)
