"""Deterministic ledgers with holds, their side files and session lock files, for the hold
command tests and their golden files (docs/HOLD-SPEC.md, section 8).

holds_mixed.txt's ledger is built from named pieces: one function per hold, each returning the
entries for that hold and writing its side file, with a docstring saying what the hold shows
(the owner's decision). Timestamps come from conftest.build_chain (2026-10-02T15:00:00.<i>Z),
and NOW is the clock the golden tests inject.

Session lock files are created here; whether a session reads as running is up to the test,
which holds a lock on the file (sessions.hold_running) or doesn't.
"""

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

from conftest import build_chain

from polarizer.lock import LedgerLock

NOW = datetime(2026, 10, 2, 15, 7, 30, 500000, tzinfo=UTC)
RUNNING = "a1a1a1a1a1a1a1a1"  # a session whose serve still runs: the test holds its lock
ENDED = "e2e2e2e2e2e2e2e2"  # a session whose serve has ended: its lock file is free
NO_LOCK = "f3f3f3f3f3f3f3f3"  # a session with no lock file at all
ESC, DEL = chr(0x1B), chr(0x7F)

# Hold ids: fixed, 16 lowercase hex characters.
WRITE_PATTERN = "1000000000000001"
UNCLASSIFIED = "2000000000000002"
FROM_ANNOTATIONS = "3000000000000003"
DENIED = "4000000000000004"
EXPIRED = "5000000000000005"
ABANDONED = "6000000000000006"
ALTERED = "7000000000000007"
MISSING = "8000000000000008"
NOT_JSON = "9000000000000009"
UNKNOWN_SESSION = "a00000000000000a"


def side_file(ledger_dir: Path, arguments, salt: int) -> str:
    """Write a side file exactly as sidefiles.write_args does, with a fixed salt."""
    stored = json.dumps(arguments, separators=(",", ":"), ensure_ascii=False)
    return raw_side_file(ledger_dir, stored.encode("utf-8"), salt)


def raw_side_file(ledger_dir: Path, stored: bytes, salt: int) -> str:
    blob = bytes([salt]) * 32 + stored
    commit = hashlib.sha256(blob).hexdigest()
    (ledger_dir / "args").mkdir(parents=True, exist_ok=True)
    (ledger_dir / "args" / f"{commit}.bin").write_bytes(blob)
    return commit


def started(session: str):
    return ("session.started", {"session": session, "polarizer_version": "0.1.0",
                                "config_sha256": "7" * 64})  # fmt: skip


def created(session, hold, tool, commit, cls, class_from, rule, reason):
    return ("hold.created", {"session": session, "hold": hold, "tool": tool,
                             "args_commit": commit, "class": cls, "class_from": class_from,
                             "rule": rule, "reason": reason, "timeout_seconds": 300})  # fmt: skip


def egress(ledger_dir, session, hold, tool, arguments, salt):
    commit = side_file(ledger_dir, arguments, salt)
    reason = "class egress is held on every call"
    return created(session, hold, tool, commit, "egress", "config", "egress", reason), commit


# The pieces of holds_mixed.txt ------------------------------------------------------------


def write_pattern_hold(ledger_dir: Path) -> list:
    """Open, in the running session: fs__write_file, local-write, held because its path matches
    the built-in pattern .git/hooks/**. Its arguments hold non-ASCII text, an ESC sequence and
    DEL, which `holds` prints as JSON escapes, so nothing hidden reaches the terminal."""
    path = "/home/me/proj/.git/hooks/pre-commit"
    arguments = {"path": path, "content": f"caf{chr(0xE9)} {ESC}[31m red{DEL}"}
    commit = side_file(ledger_dir, arguments, 1)
    reason = f'argument "path": {path} matches .git/hooks/**'
    return [
        created(RUNNING, WRITE_PATTERN, "fs__write_file", commit, "local-write", "config",
                "write-pattern", reason),
    ]  # fmt: skip


def unclassified_hold(ledger_dir: Path) -> list:
    """Open, in the running session: probe__wait, a tool with no class in polarizer.toml, so
    held on every call and listed as unclassified."""
    commit = side_file(ledger_dir, {"seconds": 5}, 2)
    reason = "probe__wait has no class in polarizer.toml"
    return [
        created(RUNNING, UNCLASSIFIED, "probe__wait", commit, None, None, "unclassified", reason)
    ]


def annotations_hold(ledger_dir: Path) -> list:
    """Open, in the ended session: web__post_comment, whose class egress came from its trusted
    upstream's annotations, so its class reads "egress (from annotations)" and its reason ends
    with the suffix. Its session has ended: no process will act on a decision."""
    commit = side_file(ledger_dir, {"issue": 7, "body": "Looks good."}, 3)
    reason = "class egress is held on every call (class from annotations)"
    return [
        created(ENDED, FROM_ANNOTATIONS, "web__post_comment", commit, "egress", "annotations",
                "egress", reason),
    ]  # fmt: skip


def denied_hold(ledger_dir: Path) -> list:
    """Ended, so left out of the listing: a hold denied by the person, with its call.refused."""
    entry, commit = egress(ledger_dir, RUNNING, DENIED, "web__post_comment", {"body": "no"}, 4)
    return [
        entry,
        ("hold.decided", {"hold": DENIED, "args_commit": commit, "decision": "deny",
                          "actor": "person", "reason": "not this one"}),
        ("call.refused", {"session": RUNNING, "tool": "web__post_comment",
                          "reason": f"hold {DENIED} was denied", "hold": DENIED}),
    ]  # fmt: skip


def expired_hold(ledger_dir: Path) -> list:
    """Ended, so left out of the listing: a hold that timed out, with its call.refused."""
    entry, _ = egress(ledger_dir, RUNNING, EXPIRED, "web__post_comment", {"body": "late"}, 5)
    return [
        entry,
        ("hold.expired", {"session": RUNNING, "hold": EXPIRED,
                          "reason": "timeout after 300 s"}),
        ("call.refused", {"session": RUNNING, "tool": "web__post_comment",
                          "reason": f"hold {EXPIRED} expired: timeout after 300 s",
                          "hold": EXPIRED}),
    ]  # fmt: skip


def lock_files(ledger_dir: Path, *sessions: str) -> None:
    (ledger_dir / "sessions").mkdir(parents=True, exist_ok=True)
    for session in sessions:
        (ledger_dir / "sessions" / f"{session}.lock").write_bytes(b"")


def hold_running(ledger_dir: Path, session: str = RUNNING) -> LedgerLock:
    """Take the exclusive lock a running serve holds on its session file; close() releases."""
    lock = LedgerLock.create(ledger_dir / "sessions" / f"{session}.lock")
    assert lock.acquire(0)
    return lock


def mixed(ledger_dir: Path) -> Path:
    """holds_mixed.txt: three open holds from two sessions, one running and one ended, and one
    denied and one expired hold, both left out."""
    ledger_dir.mkdir(parents=True, exist_ok=True)
    specs = [started(RUNNING), started(ENDED)]
    specs += write_pattern_hold(ledger_dir)
    specs += denied_hold(ledger_dir)
    specs += unclassified_hold(ledger_dir)
    specs += expired_hold(ledger_dir)
    specs += annotations_hold(ledger_dir)
    build_chain(ledger_dir, specs, head_at=0)
    lock_files(ledger_dir, RUNNING, ENDED)
    return ledger_dir


def nothing(ledger_dir: Path) -> Path:
    """holds_nothing.txt: a session whose only hold was denied."""
    ledger_dir.mkdir(parents=True, exist_ok=True)
    build_chain(ledger_dir, [started(RUNNING), *denied_hold(ledger_dir)], head_at=0)
    lock_files(ledger_dir, RUNNING)
    return ledger_dir


def state_unknown(ledger_dir: Path) -> Path:
    """holds_state_unknown.txt: one open hold whose session has no lock file."""
    ledger_dir.mkdir(parents=True, exist_ok=True)
    entry, _ = egress(ledger_dir, NO_LOCK, UNKNOWN_SESSION, "web__post_comment", {"x": 1}, 6)
    build_chain(ledger_dir, [started(NO_LOCK), entry], head_at=0)
    return ledger_dir


def side_file_problem(problem: str):
    """holds_side_file_<problem>.txt: one open hold of the running session whose side file is
    missing, altered (one byte more), or holds bytes that are not JSON arguments."""

    def build(ledger_dir: Path) -> Path:
        ledger_dir.mkdir(parents=True, exist_ok=True)
        if problem == "not_json":
            commit = raw_side_file(ledger_dir, b"not json", 7)
        else:
            commit = side_file(ledger_dir, {"body": "hello"}, 7)
            path = ledger_dir / "args" / f"{commit}.bin"
            if problem == "missing":
                path.unlink()
            else:
                path.write_bytes(path.read_bytes() + b" ")
        reason = "class egress is held on every call"
        entry = created(RUNNING, ALTERED, "web__post_comment", commit, "egress", "config",
                        "egress", reason)  # fmt: skip
        build_chain(ledger_dir, [started(RUNNING), entry], head_at=0)
        lock_files(ledger_dir, RUNNING)
        return ledger_dir

    return build


def endings(ledger_dir: Path) -> Path:
    """For allow's and deny's refusals: a denied, an expired and an abandoned hold, an open one
    of the running session, and an open one whose side file was altered."""
    ledger_dir.mkdir(parents=True, exist_ok=True)
    abandoned, _ = egress(ledger_dir, ENDED, ABANDONED, "web__post_comment", {"body": "gone"}, 8)
    altered, commit = egress(ledger_dir, RUNNING, ALTERED, "web__post_comment", {"b": 1}, 9)
    path = ledger_dir / "args" / f"{commit}.bin"
    specs = [started(RUNNING), started(ENDED)]
    specs += write_pattern_hold(ledger_dir)
    specs += denied_hold(ledger_dir)
    specs += expired_hold(ledger_dir)
    specs += [abandoned, ("hold.abandoned", {"session": RUNNING, "hold": ABANDONED,
                                             "held_by": ENDED}), altered]  # fmt: skip
    build_chain(ledger_dir, specs, head_at=0)
    path.write_bytes(path.read_bytes() + b"!")
    lock_files(ledger_dir, RUNNING, ENDED)
    return ledger_dir


def seq_of(ledger_dir: Path, kind: str, hold: str) -> int:
    for line in (ledger_dir / "ledger.jsonl").read_bytes().splitlines():
        entry = json.loads(line)
        if entry["kind"] == kind and entry["data"].get("hold") == hold:
            return entry["seq"]
    raise LookupError((kind, hold))
