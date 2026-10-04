"""Writing the ledger: first run, the writer thread, ledger.head, and repair.

docs/LEDGER-SPEC.md, Part 2. One writer thread per process owns the file. Each append takes the
lock, checks the file size against the offset this process expects, reads only bytes another
process appended (if any), writes one line, and releases the lock. Security-state entries are
fsynced, and ledger.head moved forward, before their futures resolve.
"""

import asyncio
import hashlib
import os
import queue
import secrets
import sys
import threading
import time
from concurrent.futures import Future
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from polarizer.canon import ZERO_HASH, make_entry
from polarizer.ledger import (
    HEAD,
    LEDGER,
    LOCK,
    LOCK_WAIT,
    LOCKED_LINE,
    ChainState,
    Result,
    head_bytes,
    parse_head,
    verify_bytes,
)
from polarizer.lock import LedgerLock

SECURITY_KINDS = frozenset(
    {
        "ledger.genesis",
        "tool.approved",
        "tool.rejected",
        "tool.drift",
        "ledger.repaired",
        "ledger.head_rebuilt",
    }
)
_BINARY = getattr(os, "O_BINARY", 0)
REPLACE_TRIES = 6  # the first try plus five retries, 50 ms apart: about 250 ms
REPLACE_PAUSE = 0.05


class LedgerError(Exception):
    """A refusal with one line for the person and an exit code."""

    def __init__(self, line: str, exit_code: int):
        super().__init__(line)
        self.line = line
        self.exit_code = exit_code


class Stopped(LedgerError):
    """The ledger changed under this process; it writes nothing more."""


class FileOps:
    """The writer's file layer. Tests wrap it to count the bytes each append reads."""

    def size(self, fd: int) -> int:
        return os.fstat(fd).st_size

    def read_at(self, fd: int, offset: int, length: int) -> bytes:
        os.lseek(fd, offset, os.SEEK_SET)
        chunks = []
        while length > 0:
            chunk = os.read(fd, length)
            if not chunk:
                break
            chunks.append(chunk)
            length -= len(chunk)
        return b"".join(chunks)

    def write(self, fd: int, data: bytes) -> None:
        view = memoryview(data)
        while view:
            view = view[os.write(fd, view) :]

    def fsync(self, fd: int) -> None:
        os.fsync(fd)


def now_ts() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _fsync_dir(path: Path) -> None:
    if sys.platform == "win32":
        return
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _replace(src: Path, dst: Path) -> OSError | None:
    """os.replace, retried while Windows reports the target open without delete sharing."""
    error = None
    for attempt in range(REPLACE_TRIES):
        try:
            os.replace(src, dst)
            return None
        except PermissionError as e:
            error = e
            if attempt + 1 < REPLACE_TRIES:
                time.sleep(REPLACE_PAUSE)
    return error


def update_head(ledger_dir: Path, chain_id: str, hash_: str, seq: int, ops: FileOps) -> bool:
    """Move ledger.head forward to (seq, hash). The caller holds the ledger lock.

    Skipped if the recorded head is valid, names this chain and is already at or past seq.
    Returns False if the replace kept failing; the temp file is then left, one line goes to
    stderr, and the next security entry tries again.
    """
    path = ledger_dir / HEAD
    try:
        current = parse_head(path.read_bytes())
    except FileNotFoundError:
        current = None
    if current and current.chain_id == chain_id and current.seq >= seq:
        return True
    tmp = ledger_dir / f"{HEAD}.tmp-{os.getpid()}"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | _BINARY, 0o600)
    try:
        ops.write(fd, head_bytes(chain_id, hash_, seq))
        ops.fsync(fd)
    finally:
        os.close(fd)
    error = _replace(tmp, path)
    if error is not None:
        print(
            f"polarizer: warning: could not update ledger.head ({error}); "
            "the next security entry will try again",
            file=sys.stderr,
        )
        return False
    _fsync_dir(ledger_dir)
    return True


@dataclass(frozen=True)
class Appended:
    seq: int
    hash: str


def _open_append(path: Path) -> int:
    return os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | _BINARY, 0o600)


def _write_genesis(ledger_dir: Path, fd: int, ops: FileOps) -> ChainState:
    chain_id = secrets.token_hex(16)
    entry, line = make_entry(0, now_ts(), "ledger.genesis", {"chain_id": chain_id}, ZERO_HASH)
    ops.write(fd, line)
    ops.fsync(fd)
    _fsync_dir(ledger_dir)
    state = ChainState(version=1)
    state.adopt(entry)
    update_head(ledger_dir, chain_id, entry["hash"], 0, ops)
    return state


def _append_durable(
    ledger_dir: Path, fd: int, state: ChainState, kind: str, data: dict, ops: FileOps
) -> dict:
    entry, line = make_entry(state.lines, now_ts(), kind, data, state.last_hash)
    ops.write(fd, line)
    ops.fsync(fd)
    state.adopt(entry)
    update_head(ledger_dir, state.chain_id, entry["hash"], entry["seq"], ops)
    return entry


def refusal(result: Result) -> str:
    """serve's one stderr line for a ledger that isn't intact. A torn tail's message already
    names its command (repair); the others point at verify."""
    if result.status == "torn tail":
        return f"polarizer: {result.message}"
    return f"polarizer: {result.message}; run polarizer verify"


_CATCH_UP = object()  # a queue item kind: only adopt what other processes appended


class LedgerWriter:
    """The one writer of a ledger in this process. Open it with LedgerWriter.open().

    `on_entry`, if given, is called with every entry this writer verifies at open, appends, or
    adopts from another process, in seq order, on the thread that handled it. Pin state is
    folded this way. Before an adopted tool.approved is handed over, the writer fsyncs the
    ledger itself (docs/PIN-SPEC.md, section 8), so nothing acts on an approval that another
    process wrote but may not have made durable.
    """

    def __init__(self, ledger_dir, lock, fd, rfd, state, offset, ops, idle_fsync, on_entry=None):
        self.ledger_dir = ledger_dir
        self._on_entry = on_entry
        self._lock = lock
        self._fd = fd
        self._rfd = rfd
        self._state = state
        self._offset = offset
        self._ops = ops
        self._idle_fsync = idle_fsync
        self._dirty = False
        self._stopped: str | None = None
        self._closed = False
        self._queue: queue.Queue = queue.Queue()
        self._thread = threading.Thread(target=self._run, name="polarizer-ledger", daemon=True)
        self._thread.start()

    @classmethod
    def open(
        cls,
        ledger_dir: Path,
        *,
        lock_wait: float = LOCK_WAIT,
        ops: FileOps | None = None,
        idle_fsync: bool = True,
        on_entry=None,
    ) -> "LedgerWriter":
        """Open the ledger as every process does (LEDGER-SPEC.md, First run).

        Creates ledger_dir (0700) if missing, waits up to lock_wait for the lock, writes the
        genesis entry if the ledger is missing or empty, otherwise verifies it and rebuilds a
        missing ledger.head. Raises LedgerError (locked: 7, a v0 ledger: 3, any status other
        than intact: that status's code). The caller has already checked the location.
        """
        ops = ops or FileOps()
        ledger_dir = Path(ledger_dir)
        try:
            ledger_dir.mkdir(mode=0o700, parents=True)
            os.chmod(ledger_dir, 0o700)  # mkdir's mode is narrowed by the umask, never widened
        except FileExistsError:
            pass  # existing, or created a moment ago by another process starting with us
        lock = LedgerLock.create(ledger_dir / LOCK)
        if not lock.acquire(lock_wait):
            lock.close()
            raise LedgerError(f"polarizer: {LOCKED_LINE}", 7)
        fd = rfd = None
        try:
            path = ledger_dir / LEDGER
            fd = _open_append(path)
            rfd = os.open(path, os.O_RDONLY | _BINARY)
            data = ops.read_at(rfd, 0, ops.size(rfd))
            if not data:
                state = _write_genesis(ledger_dir, fd, ops)
            else:
                state = cls._check_existing(ledger_dir, data, fd, ops, on_entry)
            offset = ops.size(fd)
        except BaseException:
            for f in (fd, rfd):
                if f is not None:
                    os.close(f)
            lock.close()
            raise
        lock.release()
        return cls(ledger_dir, lock, fd, rfd, state, offset, ops, idle_fsync, on_entry)

    @staticmethod
    def _check_existing(
        ledger_dir: Path, data: bytes, fd: int, ops: FileOps, on_entry=None
    ) -> ChainState:
        try:
            head = (ledger_dir / HEAD).read_bytes()
        except FileNotFoundError:
            head = None
        verified: list[dict] = []
        result = verify_bytes(data, head, verified.append if on_entry else None)
        if result.version == 0:
            line = f"polarizer: ledger at {ledger_dir} is v0 (Parallax's format); "
            raise LedgerError(line + "Polarizer only writes v1", 3)
        if result.status != "intact":
            raise LedgerError(refusal(result), result.exit_code)
        state = result.state
        if on_entry is not None:
            # What another process wrote may not be durable yet; make it so before anything
            # acts on it (an approval in particular).
            ops.fsync(fd)
            for entry in verified:
                on_entry(entry)
        if head is None:
            data = {"from_seq": state.last_seq, "from_hash": state.last_hash}
            _append_durable(ledger_dir, fd, state, "ledger.head_rebuilt", data, ops)
        return state

    @property
    def head(self) -> Appended:
        return Appended(self._state.last_seq, self._state.last_hash)

    @property
    def chain_id(self) -> str:
        return self._state.chain_id

    def append(self, kind: str, data: dict, *, durable: bool | None = None) -> Future:
        """Queue one entry. The future resolves to Appended once the line is written, and
        fsynced first when durable (by default: when kind is a security-state kind)."""
        future: Future = Future()
        if durable is None:
            durable = kind in SECURITY_KINDS
        if self._closed:
            future.set_exception(Stopped("polarizer: the ledger writer is closed", 1))
            return future
        self._queue.put((kind, data, durable, future))
        return future

    async def append_async(self, kind: str, data: dict, *, durable: bool | None = None):
        return await asyncio.wrap_future(self.append(kind, data, durable=durable))

    def catch_up(self) -> Future:
        """Queue a catch-up: adopt whatever other processes appended, handing each entry to
        on_entry. The future resolves to the number of entries adopted, or raises Stopped.
        If the file hasn't grown, nothing is read and the lock isn't taken."""
        future: Future = Future()
        if self._closed:
            future.set_exception(Stopped("polarizer: the ledger writer is closed", 1))
            return future
        self._queue.put((_CATCH_UP, None, False, future))
        return future

    async def catch_up_async(self) -> int:
        return await asyncio.wrap_future(self.catch_up())

    @property
    def stopped(self) -> str | None:
        """The stderr line the writer printed when it stopped, or None while it writes."""
        return self._stopped

    def close(self) -> None:
        """Write what is queued, fsync if anything is unsynced, and release the files. A second
        call does nothing; an append after it fails at once with Stopped."""
        if self._closed:
            return
        self._closed = True
        self._queue.put(None)
        self._thread.join()
        if self._dirty:
            self._ops.fsync(self._fd)
        os.close(self._fd)
        os.close(self._rfd)
        self._lock.close()

    def _run(self) -> None:
        while True:
            item = self._queue.get()
            if item is None:
                return
            kind, data, durable, future = item
            if future.set_running_or_notify_cancel():
                try:
                    if kind is _CATCH_UP:
                        future.set_result(self._catch_up_only())
                    else:
                        future.set_result(self._append(kind, data, durable))
                except BaseException as e:
                    future.set_exception(e)
            if self._queue.empty() and self._dirty and self._idle_fsync and not self._stopped:
                try:
                    self._ops.fsync(self._fd)
                    self._dirty = False
                except OSError as e:
                    # An exception here would end this thread and leave every later append
                    # waiting forever; stop writing instead, as for a ledger that changed.
                    try:
                        self._stop(f"could not fsync the ledger: {e}")
                    except Stopped:
                        pass

    def _append(self, kind: str, data: dict, durable: bool) -> Appended:
        if self._stopped:
            raise Stopped(self._stopped, 1)
        self._lock.acquire(None)
        try:
            self._catch_up()
            state = self._state
            entry, line = make_entry(state.lines, now_ts(), kind, data, state.last_hash)
            self._ops.write(self._fd, line)
            self._offset += len(line)
            state.adopt(entry)
            if durable:
                self._ops.fsync(self._fd)
                self._dirty = False
                update_head(self.ledger_dir, state.chain_id, entry["hash"], entry["seq"], self._ops)
            else:
                self._dirty = True
            if self._on_entry is not None:
                self._on_entry(entry)
            return Appended(entry["seq"], entry["hash"])
        finally:
            self._lock.release()

    def _catch_up_only(self) -> int:
        if self._stopped:
            raise Stopped(self._stopped, 1)
        if self._ops.size(self._fd) == self._offset:
            return 0  # nothing new: no lock, no read
        self._lock.acquire(None)
        try:
            return self._catch_up()
        finally:
            self._lock.release()

    def _catch_up(self) -> int:
        """Under the lock: adopt lines other processes appended, or stop for good. Returns how
        many were adopted."""
        size = self._ops.size(self._fd)
        if size == self._offset:
            return 0
        if size < self._offset:
            self._stop("the ledger is shorter than this process last saw it")
        new = self._ops.read_at(self._rfd, self._offset, size - self._offset)
        if not new.endswith(b"\n"):
            self._stop("the ledger ends in a partial line")
        adopted = []
        for raw in new.split(b"\n")[:-1]:
            problem = self._state.check_line(raw)
            if problem:
                self._stop(f"another process appended a line that doesn't chain: {problem.detail}")
            adopted.append(self._state.last_entry)
        self._offset = size
        if self._on_entry is not None:
            if any(e["kind"] == "tool.approved" for e in adopted):
                try:
                    self._ops.fsync(self._fd)
                except OSError as e:
                    self._stop(f"could not fsync an approval another process wrote: {e}")
            for entry in adopted:
                self._on_entry(entry)
        return len(adopted)

    def _stop(self, why: str) -> None:
        self._stopped = f"polarizer: stopped writing the ledger: {why}; run polarizer verify"
        print(self._stopped, file=sys.stderr)
        raise Stopped(self._stopped, 1)


@dataclass
class Outcome:
    lines: list[str]
    exit_code: int


def repair(ledger_dir: Path, lock_wait: float = LOCK_WAIT, ops: FileOps | None = None) -> Outcome:
    """polarizer repair: remove a torn tail and nothing else (LEDGER-SPEC.md, Torn tail and
    repair). The caller has already checked the location."""
    ops = ops or FileOps()
    ledger_dir = Path(ledger_dir)
    path = ledger_dir / LEDGER
    if not path.exists() or path.stat().st_size == 0:
        return Outcome([f"no ledger at {ledger_dir}"], 2)
    lock = LedgerLock.create(ledger_dir / LOCK)
    try:
        if not lock.acquire(lock_wait):
            return Outcome([LOCKED_LINE], 7)
        data = path.read_bytes()
        try:
            head = (ledger_dir / HEAD).read_bytes()
        except FileNotFoundError:
            head = None
        result = verify_bytes(data, head)
        if result.version == 0:
            return Outcome(
                ["refused: ledger is v0 (Parallax's format); Polarizer only writes v1"], 3
            )
        if result.status == "intact":
            return Outcome(["nothing to repair: ledger is intact"], 0)
        if result.status != "torn tail":
            return Outcome([_refused(result)], result.exit_code)
        return Outcome([_repair_torn(ledger_dir, data, result, ops)], 0)
    finally:
        lock.close()


def _refused(result: Result) -> str:
    end = "; repair only fixes a torn tail"
    if result.status == "truncated":
        return f"refused: ledger is truncated (ledger.head records seq {result.seq}){end}"
    if result.line is None:
        return f"refused: ledger is {result.status} at ledger.head{end}"
    return f"refused: ledger is {result.status} at line {result.line}{end}"


def _repair_torn(ledger_dir: Path, data: bytes, result: Result, ops: FileOps) -> str:
    torn = data[result.complete_bytes :]
    next_seq = result.state.lines
    name = f"{LEDGER}.torn-{next_seq}-{hashlib.sha256(torn).hexdigest()[:12]}"
    fd = os.open(ledger_dir / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | _BINARY, 0o600)
    try:
        ops.write(fd, torn)
        ops.fsync(fd)
    finally:
        os.close(fd)
    _fsync_dir(ledger_dir)
    fd = os.open(ledger_dir / LEDGER, os.O_RDWR | _BINARY)
    try:
        os.ftruncate(fd, result.complete_bytes)
        ops.fsync(fd)
    finally:
        os.close(fd)
    fd = _open_append(ledger_dir / LEDGER)
    try:
        state = result.state
        if result.complete_bytes == 0:
            state = _write_genesis(ledger_dir, fd, ops)
        data = {"bytes": len(torn), "sha256": hashlib.sha256(torn).hexdigest(), "file": name}
        entry = _append_durable(ledger_dir, fd, state, "ledger.repaired", data, ops)
    finally:
        os.close(fd)
    moved = f"moved {len(torn)} bytes to {name}"
    return f"repaired: {moved}; appended ledger.repaired at seq {entry['seq']}"
