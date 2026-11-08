"""Checkpoints and anchors (docs/EXPORT-SPEC.md, section 4).

A checkpoint names one position of one chain, (chain id, size, head hash), signed with
HMAC-SHA256 under the key in <ledger_dir>/checkpoint.key and written as a commit trailer,
`Polarizer-Checkpoint: <chain>:<size>:<head>:<sig>`. `polarizer checkpoint` prints one and never
runs git; `polarizer verify --anchor` reads one from the argument, or takes the furthest
anchored position among the trailers of the commits from a git ref, checks its signature, and
says whether the ledger still extends it.

Reading a key opens it read-only. Only create_key writes, exclusively, with mode 0600.
"""

import hashlib
import hmac
import os
import re
import secrets
import stat
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from polarizer.canon import MAX_INT
from polarizer.text import printable, safe

TRAILER = "Polarizer-Checkpoint"
PREFIX = b"POLARIZER-CHECKPOINT/1\n"
KEY_FILE = "checkpoint.key"
MAX_COMMITS = 10000
GIT = "git"  # tests replace it to see git missing
GIT_TIMEOUT = 60
_VALUE = re.compile(r"([0-9a-f]{32}):([1-9][0-9]*):([0-9a-f]{64}):([0-9a-f]{64})")
_KEY = re.compile(rb"[0-9a-f]{64}\n")
_LINE = re.compile(rf"^{TRAILER}:[ \t]*(\S+)[ \t\r]*$", re.M)
EXPECTED = f"expected {TRAILER}: <chain>:<size>:<head>:<sig>"
_BINARY = getattr(os, "O_BINARY", 0)
_NONBLOCK = getattr(os, "O_NONBLOCK", 0)


class AnchorError(Exception):
    """A usage error: one line for stderr, exit 2."""


class KeyProblem(Exception):
    """The key is missing (`missing`), not a key (`bad`) or open to others (`mode`)."""

    def __init__(self, kind: str, path: Path, mode: int | None = None):
        super().__init__(kind)
        self.kind, self.path, self.mode = kind, path, mode


@dataclass(frozen=True)
class Checkpoint:
    chain_id: str
    size: int
    head: str
    sig: str
    commit: str | None = None  # the commit it came from, for --anchor <ref>

    @property
    def seq(self) -> int:
        return self.size - 1

    def value(self) -> str:
        return f"{self.chain_id}:{self.size}:{self.head}:{self.sig}"

    def trailer(self) -> str:
        return f"{TRAILER}: {self.value()}"


def sign(key: bytes, chain_id: str, size: int, head: str) -> str:
    message = PREFIX + f"{chain_id}:{size}:{head}".encode("ascii")
    return hmac.new(key, message, hashlib.sha256).hexdigest()


def make(key: bytes, chain_id: str, size: int, head: str) -> Checkpoint:
    return Checkpoint(chain_id, size, head, sign(key, chain_id, size, head))


def parse_value(text: str) -> Checkpoint | None:
    match = _VALUE.fullmatch(text)
    if match is None:
        return None
    chain_id, size, head, sig = match.groups()
    if int(size) > MAX_INT + 1:
        return None
    return Checkpoint(chain_id, int(size), head, sig)


def parse_argument(text: str) -> Checkpoint | None:
    """A trailer line or a bare value, or None when it is neither."""
    if text.startswith(f"{TRAILER}:"):
        return parse_value(text[len(TRAILER) + 1 :].strip(" \t"))
    return parse_value(text)


def looks_like_value(text: str) -> bool:
    """Whether --anchor's argument was meant as a checkpoint, not a git ref."""
    return text.startswith(f"{TRAILER}:") or bool(re.fullmatch(r"[0-9a-f]{32}:.*", text))


def from_argument(text: str, cwd: Path | None = None) -> list[Checkpoint]:
    """--anchor's candidates: the one trailer or value given, or every well-formed trailer in
    the commits from a git ref, newest first. choose() picks the one to check. Raises
    AnchorError."""
    if looks_like_value(text):
        found = parse_argument(text)
        if found is None:
            raise AnchorError(f"polarizer: --anchor: not a checkpoint; {EXPECTED}")
        return [found]
    return from_git(text, cwd)


def choose(candidates: list[Checkpoint], chain_id: str) -> Checkpoint:
    """The furthest anchored position (docs/EXPORT-SPEC.md, section 4): of the candidates of
    the ledger's own chain, the one with the largest size, or of all of them when none is of
    that chain; the newest on a tie. Candidates are newest first."""
    own = [cp for cp in candidates if cp.chain_id == chain_id] or candidates
    return max(own, key=lambda cp: cp.size)  # max keeps the first of equal sizes


_COMMIT = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")


def from_git(ref: str, cwd: Path | None = None) -> list[Checkpoint]:
    """Every well-formed trailer line in the MAX_COMMITS commits from `ref`, newest first (and
    within one message, the last line first), each with its commit. Raises AnchorError."""
    shown = printable(ref)
    if not ref or ref.startswith("-") or any(ord(c) < 0x20 or ord(c) == 0x7F for c in ref):
        raise AnchorError(f"polarizer: --anchor {shown}: not a git ref Polarizer will pass to git")
    # log.showSignature=true would print a signature check before each commit's hash.
    argv = [GIT, "-c", "log.showSignature=false", "log", f"--max-count={MAX_COMMITS}"]
    argv += ["--format=%H%x00%B%x00", ref, "--"]
    try:
        done = subprocess.run(argv, cwd=cwd, capture_output=True, timeout=GIT_TIMEOUT)
    except OSError as e:
        raise AnchorError(
            f"polarizer: --anchor {shown}: git is not available: {e.strerror or e}"
        ) from None
    except subprocess.TimeoutExpired:
        raise AnchorError(
            f"polarizer: --anchor {shown}: git log failed: no answer in {GIT_TIMEOUT} s"
        ) from None
    if done.returncode != 0:
        first = done.stderr.decode("utf-8", "replace").strip().splitlines()
        why = safe(first[0]) if first else f"exit code {done.returncode}"
        raise AnchorError(f"polarizer: --anchor {shown}: git log failed: {why}")
    fields = done.stdout.decode("utf-8", "replace").split("\0")
    commits = 0
    found = []
    for i in range(0, len(fields) - 1, 2):
        commit, message = fields[i].strip(), fields[i + 1]
        if not _COMMIT.fullmatch(commit):
            why = f"not a commit hash: {safe(commit)[:80]}"
            raise AnchorError(f"polarizer: --anchor {shown}: git log failed: {why}")
        commits += 1
        values = [parse_value(match.group(1)) for match in _LINE.finditer(message)]
        for cp in reversed([v for v in values if v is not None]):
            found.append(Checkpoint(cp.chain_id, cp.size, cp.head, cp.sig, commit))
    if found:
        return found
    noun = "commit" if commits == 1 else "commits"
    raise AnchorError(
        f"polarizer: --anchor {shown}: no {TRAILER} trailer in the {commits} {noun} from {shown}"
    )


# The key ------------------------------------------------------------------------------------


def key_path(ledger_dir: Path) -> Path:
    return Path(ledger_dir) / KEY_FILE


def read_key(ledger_dir: Path, check_mode: bool = False) -> bytes:
    """The key's 32 bytes. Opens the file read-only, without blocking, so a FIFO in its place
    is refused rather than waited on; anything but a regular file, or a file that can't be
    read, is not a key. With `check_mode` (checkpoint), a key its group or others can read
    or write is refused on POSIX. Raises KeyProblem."""
    path = key_path(ledger_dir)
    try:
        fd = os.open(path, os.O_RDONLY | _BINARY | _NONBLOCK)
    except FileNotFoundError:
        raise KeyProblem("missing", path) from None
    except OSError:
        raise KeyProblem("bad", path) from None
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise KeyProblem("bad", path)
        mode = stat.S_IMODE(info.st_mode)
        raw = os.read(fd, 128)
    except OSError:
        raise KeyProblem("bad", path) from None
    finally:
        os.close(fd)
    if not _KEY.fullmatch(raw):
        raise KeyProblem("bad", path)
    if check_mode and sys.platform != "win32" and mode & 0o077:
        raise KeyProblem("mode", path, mode)
    return bytes.fromhex(raw[:64].decode("ascii"))


def create_key(ledger_dir: Path) -> bytes | None:
    """A new key, written exclusively with mode 0600 and fsynced, or None when a key file
    appeared meanwhile (another checkpoint made it; read that one)."""
    path = key_path(ledger_dir)
    key = secrets.token_bytes(32)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | _BINARY, 0o600)
    except FileExistsError:
        return None
    try:
        os.write(fd, key.hex().encode("ascii") + b"\n")
        os.fsync(fd)
    finally:
        os.close(fd)
    if sys.platform != "win32":
        os.chmod(path, 0o600)
        dir_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    return key


def key_line(problem: KeyProblem) -> str:
    """checkpoint's refusal for a key it can't use."""
    if problem.kind == "mode":
        return (
            f"polarizer: {problem.path} must be readable and writable only by its owner "
            f"(mode 0600); it is {problem.mode:04o}"
        )
    return (
        f"polarizer: {problem.path} is not a checkpoint key "
        "(64 lowercase hex characters and a newline)"
    )


# The check ----------------------------------------------------------------------------------


@dataclass(frozen=True)
class Outcome:
    result: str  # extends, rewound, rewritten, other chain, bad signature, no key, bad key
    line: str
    exit_code: int


def first_line(cp: Checkpoint) -> str:
    line = f"anchor: checkpoint of chain {cp.chain_id} at seq {cp.seq}, head {cp.head}"
    return line + (f", from commit {cp.commit}" if cp.commit else "")


def check(cp: Checkpoint, key: bytes, chain_id: str, last_seq: int, hash_at: str | None):
    """The anchor's result for an intact ledger whose chain id is `chain_id`, whose last seq is
    `last_seq`, and whose entry at the checkpoint's seq has hash `hash_at` (None past the
    end). The signature is checked first, in constant time."""
    if not hmac.compare_digest(sign(key, cp.chain_id, cp.size, cp.head), cp.sig):
        line = "anchor: signature does not match; this ledger's key did not make the checkpoint"
        return Outcome("bad signature", line, 10)
    if cp.chain_id != chain_id:
        return Outcome("other chain", f"anchor: other chain: the ledger's chain is {chain_id}", 9)
    if last_seq < cp.seq:
        line = (
            f"anchor: rewound: the ledger ends at seq {last_seq}, before the anchored seq {cp.seq}"
        )
        return Outcome("rewound", line, 9)
    if hash_at != cp.head:
        line = (
            f"anchor: rewritten: the ledger's entry at seq {cp.seq} is {hash_at}, "
            "not the anchored head"
        )
        return Outcome("rewritten", line, 9)
    line = f"anchor: intact: the ledger extends the checkpoint by {last_seq - cp.seq} entries"
    return Outcome("extends", line, 0)


def key_outcome(problem: KeyProblem) -> Outcome:
    """verify's line when the key can't be read."""
    if problem.kind == "missing":
        line = f"anchor: no key at {problem.path}; cannot check the signature"
        return Outcome("no key", line, 10)
    return Outcome("bad key", f"anchor: {problem.path} is not a checkpoint key; cannot check "
                   "the signature", 10)  # fmt: skip
