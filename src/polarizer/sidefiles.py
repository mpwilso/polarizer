"""Argument side files (docs/LEDGER-SPEC.md, Part 3).

Each call's arguments live in args/<args_commit>.bin: 32 random bytes of salt, then the
arguments as compact UTF-8 JSON. args_commit is the sha256 of the whole file. Checking only
reads; nothing here deletes anything.
"""

import hashlib
import json
import os
import re
import secrets
from dataclasses import dataclass
from pathlib import Path

ARGS = "args"
_BINARY = getattr(os, "O_BINARY", 0)
_COMMIT = re.compile("[0-9a-f]{64}")


def write_args(ledger_dir: Path, arguments) -> str:
    """Write one side file, created exclusively, and return its commitment."""
    stored = json.dumps(arguments, separators=(",", ":"), ensure_ascii=False)
    blob = secrets.token_bytes(32) + stored.encode("utf-8", "surrogatepass")
    commit = hashlib.sha256(blob).hexdigest()
    directory = Path(ledger_dir) / ARGS
    try:
        directory.mkdir(mode=0o700)
        os.chmod(directory, 0o700)
    except FileExistsError:
        pass
    fd = os.open(directory / f"{commit}.bin", os.O_WRONLY | os.O_CREAT | os.O_EXCL | _BINARY, 0o600)
    try:
        view = memoryview(blob)
        while view:
            view = view[os.write(fd, view) :]
    finally:
        os.close(fd)
    return commit


class ArgsProblem(Exception):
    """A side file that fails its check. `problem` is one of the fixed texts below; `detail`
    is the operating system's message for an unreadable file, or None."""

    def __init__(self, problem: str, detail: str | None = None, size: int | None = None):
        super().__init__(problem if detail is None else f"{problem}: {detail}")
        self.problem = problem
        self.detail = detail
        self.size = size  # bytes of arguments (the file's size minus the salt), when known


MISSING = "is missing"
MISMATCH = "does not match its name"
UNREADABLE = "is unreadable"
NOT_ARGUMENTS = "does not hold JSON arguments"


def read_args(ledger_dir: Path, commit: str):
    """Read a side file back and check it as `verify --args` does: (arguments, size), where
    size is the bytes of arguments after the 32-byte salt. The arguments are parsed from the
    stored bytes and must be a JSON object or null. Raises ArgsProblem. A commitment that
    isn't 64 lowercase hex characters is never used as a path: it counts as missing."""
    if not isinstance(commit, str) or not _COMMIT.fullmatch(commit):
        raise ArgsProblem(MISSING)
    try:
        with open(Path(ledger_dir) / ARGS / f"{commit}.bin", "rb") as f:
            blob = f.read()
    except FileNotFoundError:
        raise ArgsProblem(MISSING) from None
    except OSError as e:
        raise ArgsProblem(UNREADABLE, e.strerror or type(e).__name__) from None
    size = max(len(blob) - 32, 0)
    if hashlib.sha256(blob).hexdigest() != commit:
        raise ArgsProblem(MISMATCH, size=size)
    try:
        arguments = json.loads(blob[32:].decode("utf-8", "surrogatepass")) if len(blob) > 32 else 0
    except (UnicodeDecodeError, ValueError, RecursionError):
        arguments = 0
    if not (arguments is None or isinstance(arguments, dict)):
        raise ArgsProblem(NOT_ARGUMENTS, size=size)
    return arguments, size


@dataclass
class ArgsReport:
    lines: list[str]
    tampered: int


def check_args(ledger_dir: Path, calls_sent: list, holds_created: list = ()) -> ArgsReport:
    """verify --args: classify every side file. calls_sent and holds_created are
    [(seq, args_commit), ...]. A hold.created refers to its side file as a call.sent does, so
    the file of a hold that was never forwarded isn't orphaned; a file both refer to (an
    allowed hold) is counted once, at its call.sent (HOLD-SPEC.md, section 5)."""
    directory = Path(ledger_dir) / ARGS
    try:
        present = {entry.name for entry in os.scandir(directory)}
    except FileNotFoundError:
        present = set()
    counts = {"matching": 0, "missing": 0, "tampered": 0}
    problems = []
    referenced = set()
    sent = {commit for _, commit in calls_sent if isinstance(commit, str)}
    held = [(seq, c) for seq, c in holds_created if not (isinstance(c, str) and c in sent)]
    for seq, commit in sorted([*calls_sent, *held], key=lambda ref: ref[0]):
        name = f"{commit}.bin" if isinstance(commit, str) and _COMMIT.fullmatch(commit) else None
        if name is None or name not in present:
            counts["missing"] += 1
            problems.append(f"missing   seq {seq}  {ARGS}/{commit}.bin")
            continue
        referenced.add(name)
        with open(directory / name, "rb") as f:
            matches = hashlib.sha256(f.read()).hexdigest() == commit
        if matches:
            counts["matching"] += 1
        else:
            counts["tampered"] += 1
            problems.append(f"tampered  seq {seq}  {ARGS}/{name}")
    orphans = sorted(present - referenced)
    summary = (
        f"args: {counts['matching']} matching, {counts['missing']} missing, "
        f"{counts['tampered']} tampered, {len(orphans)} orphaned"
    )
    lines = [summary, *problems, *(f"orphaned           {ARGS}/{name}" for name in orphans)]
    return ArgsReport(lines, counts["tampered"])
