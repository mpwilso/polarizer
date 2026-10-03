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


@dataclass
class ArgsReport:
    lines: list[str]
    tampered: int


def check_args(ledger_dir: Path, calls_sent: list) -> ArgsReport:
    """verify --args: classify every side file. calls_sent is [(seq, args_commit), ...]."""
    directory = Path(ledger_dir) / ARGS
    try:
        present = {entry.name for entry in os.scandir(directory)}
    except FileNotFoundError:
        present = set()
    counts = {"matching": 0, "missing": 0, "tampered": 0}
    problems = []
    referenced = set()
    for seq, commit in calls_sent:
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
