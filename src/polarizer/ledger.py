"""Verifying a ledger: statuses, their order and their exact messages (docs/LEDGER-SPEC.md).

Everything here only reads. The writer (writer.py) reuses ChainState to check lines that other
processes appended.
"""

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from polarizer.canon import MAX_INT, MAX_LINE, ZERO_HASH, canonical, entry_hash, subset_problem
from polarizer.lock import LedgerLock

LEDGER = "ledger.jsonl"
LOCK = "ledger.jsonl.lock"
HEAD = "ledger.head"
LOCK_WAIT = 2.0
LOCKED_LINE = (
    "locked: ledger is locked by another process (waited 2 s); "
    "try again or close other Polarizer sessions"
)

V1_TYPES = {"v": int, "seq": int, "ts": str, "kind": str, "data": dict, "prev": str, "hash": str}
V0_KEYS = ("id", "ts", "kind", "actor", "reason", "data", "prev", "hash")
HEAD_KEYS = {"chain_id", "hash", "seq"}
EXIT_CODES = {
    "intact": 0,
    "tampered": 1,
    "invalid": 3,
    "not canonical": 4,
    "torn tail": 5,
    "truncated": 6,
}
_HEX32 = re.compile("[0-9a-f]{32}")
_HEX64 = re.compile("[0-9a-f]{64}")


class Locked(Exception):
    """The ledger lock was still held after the wait."""


@dataclass(frozen=True)
class Head:
    chain_id: str
    hash: str
    seq: int


def parse_head(raw: bytes) -> Head | None:
    """A valid ledger.head record, or None: canonical JSON of exactly chain_id, hash and seq,
    plus one newline."""
    try:
        h = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError):
        return None
    if not isinstance(h, dict) or set(h) != HEAD_KEYS:
        return None
    seq, chain_id, hash_ = h["seq"], h["chain_id"], h["hash"]
    if type(seq) is not int or not 0 <= seq <= MAX_INT:
        return None
    if not isinstance(chain_id, str) or not _HEX32.fullmatch(chain_id):
        return None
    if not isinstance(hash_, str) or not _HEX64.fullmatch(hash_):
        return None
    if raw != canonical(h) + b"\n":
        return None
    return Head(chain_id, hash_, seq)


def head_bytes(chain_id: str, hash_: str, seq: int) -> bytes:
    return canonical({"chain_id": chain_id, "hash": hash_, "seq": seq}) + b"\n"


@dataclass
class Problem:
    """The first problem in a line: status, check and the text after "line <l> (seq <q>): "."""

    status: str
    check: str
    detail: str
    seq: int | None


@dataclass
class ChainState:
    """What verification knows after the lines checked so far."""

    version: int | None = None  # 1 or 0, decided by the first line
    lines: int = 0
    last_hash: str | None = None
    chain_id: str | None = None
    sessions: int = 0
    calls: int = 0
    calls_sent: list = field(default_factory=list)  # (seq, args_commit) for verify --args
    holds_created: list = field(default_factory=list)  # (seq, args_commit), likewise
    watch_seq: int | None = None  # remember the hash at this seq (the head's)
    watched_hash: str | None = None
    last_entry: dict | None = None  # the line check_line adopted last, parsed

    @property
    def last_seq(self) -> int | None:
        return self.lines - 1 if self.version == 1 and self.lines else None

    def check_line(self, raw: bytes) -> Problem | None:
        """Check the next complete line (without its newline). On success, adopt it."""
        n = self.lines + 1
        try:
            entry = json.loads(raw.decode("utf-8"))
        except UnicodeDecodeError:
            return Problem("invalid", "structure", "not valid UTF-8", None)
        except (ValueError, RecursionError):
            return Problem("invalid", "structure", "not valid JSON", None)
        if not isinstance(entry, dict):
            return Problem("invalid", "structure", "not a JSON object", None)
        if self.version is None:
            self.version = 1 if "v" in entry else 0
        if self.version == 1:
            problem = self._check_v1(entry, raw, n)
        else:
            problem = self._check_v0(entry, raw, n)
        if problem:
            return problem
        self.lines = n
        self.last_hash = entry["hash"]
        self.last_entry = entry
        if self.watch_seq is not None and n - 1 == self.watch_seq:
            self.watched_hash = entry["hash"]
        if self.version == 1:
            self._count(entry, n)
        return None

    def adopt(self, entry: dict) -> None:
        """Adopt an entry this process just built and wrote."""
        self.lines += 1
        self.last_hash = entry["hash"]
        self._count(entry, self.lines)

    def _count(self, entry: dict, n: int) -> None:
        kind = entry["kind"]
        if n == 1:
            self.chain_id = entry["data"]["chain_id"]
        if kind == "session.started":
            self.sessions += 1
        elif kind == "call.sent":
            self.calls += 1
            self.calls_sent.append((entry["seq"], entry["data"].get("args_commit")))
        elif kind == "hold.created":
            self.holds_created.append((entry["seq"], entry["data"].get("args_commit")))

    def _check_v1(self, e: dict, raw: bytes, n: int) -> Problem | None:
        seq = e.get("seq") if type(e.get("seq")) is int else None

        def invalid(rule):
            return Problem("invalid", "structure", rule, seq)

        if "v" not in e:
            return invalid("v0 and v1 entries mixed")
        if len(raw) + 1 > MAX_LINE:
            return invalid("line longer than 16 KiB")
        for key in e:
            if key not in V1_TYPES:
                return invalid(f"unknown key {json.dumps(key)}")
        for key in V1_TYPES:
            if key not in e:
                return invalid(f'missing key "{key}"')
        for key, kind in V1_TYPES.items():
            if type(e[key]) is not kind:
                return invalid(f'"{key}" has the wrong type')
        if e["v"] != 1:
            return invalid("v is not 1")
        rule = subset_problem(e)
        if rule:
            return invalid(rule)
        genesis = e["kind"] == "ledger.genesis"
        if n == 1 and not genesis:
            return invalid("first entry is not a genesis")
        if n > 1 and genesis:
            return invalid("second genesis")
        if n == 1:
            chain_id = e["data"].get("chain_id")
            if not isinstance(chain_id, str) or not _HEX32.fullmatch(chain_id):
                return invalid("genesis chain_id is not 32 lowercase hex")
        if e["seq"] != n - 1:
            return Problem("tampered", "seq", f"seq is {e['seq']}, expected {n - 1}", seq)
        problem = self._check_prev(e, n, seq)
        if problem:
            return problem
        if e["hash"] != entry_hash(e):
            return Problem("tampered", "hash", "hash does not match the entry", seq)
        if raw != canonical(e):
            detail = "stored bytes are not the canonical form"
            return Problem("not canonical", "canonical", detail, seq)
        return None

    def _check_v0(self, e: dict, raw: bytes, n: int) -> Problem | None:
        """Frozen v0 rules: Parallax's own format, checked exactly as its ledger.py writes it."""
        if "v" in e:
            return Problem("invalid", "structure", "v0 and v1 entries mixed", None)
        for key in e:
            if key not in V0_KEYS:
                return Problem("invalid", "structure", f"unknown key {json.dumps(key)}", None)
        for key in V0_KEYS:
            if key not in e:
                return Problem("invalid", "structure", f'missing key "{key}"', None)
        problem = self._check_prev(e, n, None)
        if problem:
            return problem
        body = {k: v for k, v in e.items() if k != "hash"}
        if e["hash"] != hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest():
            return Problem("tampered", "hash", "hash does not match the entry", None)
        if raw != json.dumps(e, sort_keys=True).encode():
            detail = "stored bytes are not the v0 line form"
            return Problem("not canonical", "canonical", detail, None)
        return None

    def _check_prev(self, e: dict, n: int, seq: int | None) -> Problem | None:
        if n == 1 and e["prev"] != ZERO_HASH:
            return Problem("tampered", "prev", "prev is not 64 zeros", seq)
        if n > 1 and e["prev"] != self.last_hash:
            return Problem("tampered", "prev", f"prev does not match line {n - 1}", seq)
        return None


@dataclass
class Result:
    status: str
    check: str | None
    line: int | None
    seq: int | None
    message: str  # the first stdout line of verify
    state: ChainState
    head_missing: bool = False
    torn_bytes: int = 0
    complete_bytes: int = 0  # bytes up to and including the last newline

    @property
    def exit_code(self) -> int:
        return EXIT_CODES[self.status]

    @property
    def version(self) -> int | None:
        return self.state.version

    def output_lines(self) -> list[str]:
        """verify's exact stdout, without --args."""
        state = self.state
        if self.status != "intact":
            return [self.message]
        if state.version == 0:
            return [self.message, f"head {state.last_hash} at line {state.lines}"]
        lines = [self.message, f"chain {state.chain_id}, head {state.last_hash} at seq {self.seq}"]
        if self.head_missing:
            lines.append("ledger.head: missing; serve will rebuild it")
        return lines


def verify_bytes(data: bytes, head: bytes | None, on_entry=None) -> Result:
    """Verify a ledger's bytes and its ledger.head bytes (None when the file is missing).
    `on_entry`, if given, gets each entry that passed its line checks, in order, in the same
    pass (pin state is folded this way, with no second read). An entry it was given may still
    be followed by a problem; the caller then discards what it folded.

    The first problem wins, in this order: per-line problems in file order; then head problems
    (an invalid head file, a head hash that doesn't match, a head past the last entry); then a
    torn tail. See docs/LEDGER-SPEC.md, Statuses.
    """
    complete = data.rfind(b"\n") + 1
    tail = len(data) - complete
    parsed_head = parse_head(head) if head is not None else None
    state = ChainState(watch_seq=parsed_head.seq if parsed_head else None)
    for n, raw in enumerate(data[:complete].split(b"\n")[:-1], 1):
        problem = state.check_line(raw)
        if problem is None and on_entry is not None and state.version == 1:
            on_entry(state.last_entry)
        if problem:
            shown = "?" if problem.seq is None else problem.seq
            where = f"line {n}" if state.version == 0 else f"line {n} (seq {shown})"
            message = f"{problem.status}: {where}: {problem.detail}"
            seq = problem.seq if state.version != 0 else None
            return Result(problem.status, problem.check, n, seq, message, state)

    def result(status, check, line, seq, message):
        return Result(status, check, line, seq, message, state, head is None, tail, complete)

    if head is not None:
        if parsed_head is None:
            return result(
                "invalid", "head", None, None, "invalid: ledger.head: not a valid head record"
            )
        h = parsed_head
        if state.version == 0 or (state.lines and h.chain_id != state.chain_id):
            message = "invalid: ledger.head: chain_id does not match the ledger"
            return result("invalid", "head", None, h.seq, message)
        last = state.last_seq
        if last is not None and h.seq <= last and h.hash != state.watched_hash:
            message = f"tampered: ledger.head: hash does not match seq {h.seq}"
            return result("tampered", "head", None, h.seq, message)
        if last is None or h.seq > last:
            if last is None:
                message = (
                    f"truncated: ledger has no complete entries but ledger.head records seq {h.seq}"
                )
            else:
                message = (
                    f"truncated: ledger ends at seq {last} but ledger.head records seq {h.seq}"
                )
            return result("truncated", "head", None, h.seq, message)
    if tail:
        if state.version == 0:
            message = f"torn tail: {tail} bytes after line {state.lines}"
        else:
            q = "?" if state.last_seq is None else state.last_seq
            message = (
                f"torn tail: {tail} bytes after line {state.lines} (seq {q}); run polarizer repair"
            )
        return result("torn tail", None, state.lines, state.last_seq, message)
    if state.version == 0:
        message = f"intact (v0): {state.lines} entries"
    else:
        message = f"intact: {state.lines} entries, {state.sessions} sessions, {state.calls} calls"
    return result("intact", None, state.lines, state.last_seq, message)


def read_files(ledger_dir: Path, wait: float = LOCK_WAIT) -> tuple[bytes | None, bytes | None]:
    """Read ledger.jsonl and ledger.head (None when missing), under the lock if the lock file
    exists. Never creates, deletes or modifies anything: every file is opened read-only, and a
    missing lock file is not created. Raises Locked, or OSError for an unreadable file."""
    lock = LedgerLock.open_existing(Path(ledger_dir) / LOCK)
    if lock is not None and not lock.acquire(wait):
        lock.close()
        raise Locked()
    try:
        return _read_optional(Path(ledger_dir) / LEDGER), _read_optional(Path(ledger_dir) / HEAD)
    finally:
        if lock is not None:
            lock.release()
            lock.close()


def _read_optional(path: Path) -> bytes | None:
    try:
        with open(path, "rb") as f:
            return f.read()
    except FileNotFoundError:
        return None
