"""Tool definition hashes and stored copies (docs/PIN-SPEC.md, sections 2 and 5).

The hashed form is what the SDK serves on a 2026-07-28 connection, without the tool's _meta.
The stored copy holds its canonical bytes exactly, so rehashing a copy is one sha256.
"""

import hashlib
import json
import os
import secrets
import sys
from pathlib import Path

import mcp_types as types
import rfc8785
from mcp_types.methods import serialize_server_result

from polarizer.upstream import describe

PREFIX = b"POLARIZER-TOOLDEF/1\n"
DEFS = "defs"
FORM_VERSION = "2026-07-28"
# Required by the 2026-07-28 result model; they don't touch the tool.
ENVELOPE = {"resultType": "complete", "ttlMs": 0, "cacheScope": "private"}
_BINARY = getattr(os, "O_BINARY", 0)


class Unhashable(Exception):
    """A definition that can't be hashed. The message is one line."""


class CopyProblem(Exception):
    """A stored copy that fails its check. The message is the problem after "stored copy":
    "missing", "unreadable: <why>", "does not match its hash" or "is not a valid tool"."""


def canonical(tool: types.Tool) -> bytes:
    """The canonical bytes of a tool's hashed form (section 2, steps 2 to 4)."""
    mono = tool.model_dump(by_alias=True, mode="json", exclude_none=True, exclude={"meta"})
    try:
        form = serialize_server_result("tools/list", FORM_VERSION, {"tools": [mono], **ENVELOPE})
        form = form["tools"][0]
    except Exception as e:
        raise Unhashable(describe(e)) from None
    try:
        return rfc8785.dumps(form)
    except (rfc8785.CanonicalizationError, ValueError, TypeError, RecursionError) as e:
        raise Unhashable(describe(e)) from None


def hash_of(canon: bytes) -> str:
    return hashlib.sha256(PREFIX + canon).hexdigest()


def definition(tool: types.Tool) -> tuple[str, bytes]:
    """(def_hash, canonical bytes). Raises Unhashable."""
    canon = canonical(tool)
    return hash_of(canon), canon


def is_hash(text: str) -> bool:
    return len(text) == 64 and all(c in "0123456789abcdef" for c in text)


def copy_name(def_hash: str) -> str:
    """The copy's path under ledger_dir, as printed: defs/<def_hash>.json."""
    return f"{DEFS}/{def_hash}.json"


def _fsync_dir(path: Path) -> None:
    if sys.platform == "win32":
        return
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def write_copy(ledger_dir: Path, def_hash: str, canon: bytes) -> bool:
    """Store a copy without ever replacing an existing file. Returns False if one was already
    there. Raises OSError if it can't be written."""
    defs = Path(ledger_dir) / DEFS
    final = defs / f"{def_hash}.json"
    if final.exists():
        return False
    try:
        defs.mkdir(mode=0o700)
        os.chmod(defs, 0o700)  # mkdir's mode is narrowed by the umask, never widened
    except FileExistsError:
        pass
    tmp = defs / f"{def_hash}.json.tmp-{os.getpid()}-{secrets.token_hex(4)}"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | _BINARY, 0o600)
    try:
        view = memoryview(canon)
        while view:
            view = view[os.write(fd, view) :]
        os.fsync(fd)
    finally:
        os.close(fd)
    try:
        if sys.platform == "win32":
            os.rename(tmp, final)  # fails if the target exists
        else:
            os.link(tmp, final)  # never replaces an existing file
    except FileExistsError:
        return False
    finally:
        if sys.platform != "win32" or tmp.exists():
            try:
                os.unlink(tmp)
            except FileNotFoundError:
                pass
    _fsync_dir(defs)
    return True


def read_copy(ledger_dir: Path, def_hash: str) -> dict:
    """Read a stored copy and check it: present, readable, rehashing to its name, and a valid
    tool. Returns the stored object. Raises CopyProblem."""
    path = Path(ledger_dir) / DEFS / f"{def_hash}.json"
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        raise CopyProblem("missing") from None
    except OSError as e:
        raise CopyProblem(f"unreadable: {e.strerror or describe(e)}") from None
    if hash_of(raw) != def_hash:
        raise CopyProblem("does not match its hash")
    try:
        obj = json.loads(raw.decode("utf-8"))
        if not isinstance(obj, dict):
            raise ValueError("not an object")
        types.Tool.model_validate(obj, by_alias=True, by_name=False)
    except Exception:
        raise CopyProblem("is not a valid tool") from None
    return obj


def served(obj: dict, exposed_name: str) -> types.Tool:
    """The tool Polarizer serves: the stored object, renamed, parsed by alias."""
    return types.Tool.model_validate({**obj, "name": exposed_name}, by_alias=True, by_name=False)


def render(obj: dict) -> str:
    """A definition as `pending` and `approve` print it: every non-ASCII character escaped."""
    return json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=True)
