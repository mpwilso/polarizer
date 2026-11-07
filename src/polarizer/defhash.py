"""Tool definition hashes and stored copies (docs/PIN-SPEC.md, sections 2 and 5, and
docs/SCAN-SPEC.md, sections 7 and 8).

The hashed form is what the SDK serves on a 2026-07-28 connection. Its _meta holds only the
keys the upstream's meta_allowlist names; a form that serves any is hashed under
POLARIZER-TOOLDEF/2, one that serves none under POLARIZER-TOOLDEF/1, as in M1a. An upstream's
instructions are stored as {"instructions": <text>} under POLARIZER-INSTRUCTIONS/1. The stored
copy holds its canonical bytes exactly, and its prefix follows from its form, so rehashing a
copy is one sha256.
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

from polarizer.ledgerdir import make_private
from polarizer.upstream import describe

PREFIX = b"POLARIZER-TOOLDEF/1\n"
META_PREFIX = b"POLARIZER-TOOLDEF/2\n"  # a tool form that serves _meta (SCAN-SPEC.md, 8)
INSTRUCTIONS_PREFIX = b"POLARIZER-INSTRUCTIONS/1\n"  # an upstream's instructions (section 7)
DEFS = "defs"
# A definition whose canonical bytes are longer is never stored or served (section 2).
MAX_DEFINITION_BYTES = 262144
TOO_LARGE = f"definition larger than {MAX_DEFINITION_BYTES} bytes"
# Why a definition can't be hashed, chosen by the exception's type and never taken from its
# message, which can quote the upstream's definition (section 2).
NOT_A_TOOL = "not a valid tool definition"
BIG_INTEGER = "integer outside the safe range"
BAD_TEXT = "text that is not valid Unicode"
NOT_CANONICAL = "could not be canonicalized"
FORM_VERSION = "2026-07-28"
# Required by the 2026-07-28 result model; they don't touch the tool.
ENVELOPE = {"resultType": "complete", "ttlMs": 0, "cacheScope": "private"}
_BINARY = getattr(os, "O_BINARY", 0)


class Unhashable(Exception):
    """A definition that can't be hashed. The message is one of the four fixed reasons."""


class CopyProblem(Exception):
    """A stored copy that fails its check. The message is the problem after "stored copy":
    "missing", "unreadable: <why>", "does not match its hash" or "is not a valid tool"."""


def served_meta(tool: types.Tool, meta_allow=()) -> dict:
    """The tool's _meta keys that its upstream's meta_allowlist names, with their values."""
    meta = tool.meta or {}
    return {key: meta[key] for key in meta_allow if key in meta}


def canonical(tool: types.Tool, meta_allow=()) -> bytes:
    """The canonical bytes of a tool's hashed form (PIN-SPEC.md, section 2, steps 2 to 4),
    with the listed _meta keys it has (SCAN-SPEC.md, section 8)."""
    try:
        mono = tool.model_dump(by_alias=True, mode="json", exclude_none=True, exclude={"meta"})
        meta = served_meta(tool, meta_allow)
        if meta:
            mono["_meta"] = types.Tool.model_validate({**mono, "_meta": meta}).model_dump(
                by_alias=True, mode="json", exclude_none=True
            )["_meta"]
        form = serialize_server_result("tools/list", FORM_VERSION, {"tools": [mono], **ENVELOPE})
        form = form["tools"][0]
        if meta and "_meta" not in form:
            raise ValueError("the served form dropped _meta")
    except Exception:
        raise Unhashable(NOT_A_TOOL) from None
    return _canon(form)


def _canon(form) -> bytes:
    """RFC 8785 bytes, with the four fixed reasons for what can't be canonicalized."""
    try:
        return rfc8785.dumps(form)
    except rfc8785.IntegerDomainError:
        raise Unhashable(BIG_INTEGER) from None
    except rfc8785.CanonicalizationError as e:
        # rfc8785 reports a lone surrogate as its base error, raised from the UnicodeError.
        reason = BAD_TEXT if isinstance(e.__cause__, UnicodeError) else NOT_CANONICAL
        raise Unhashable(reason) from None
    except (ValueError, TypeError, RecursionError):
        raise Unhashable(NOT_CANONICAL) from None


def hash_of(canon: bytes, prefix: bytes = PREFIX) -> str:
    return hashlib.sha256(prefix + canon).hexdigest()


def definition(tool: types.Tool, meta_allow=()) -> tuple[str, bytes]:
    """(def_hash, canonical bytes), under /2 when the form serves _meta. Raises Unhashable."""
    canon = canonical(tool, meta_allow)
    prefix = META_PREFIX if served_meta(tool, meta_allow) else PREFIX
    return hash_of(canon, prefix), canon


def instructions_definition(text: str) -> tuple[str, bytes]:
    """(def_hash, canonical bytes) of an upstream's instructions (SCAN-SPEC.md, section 7).
    Raises Unhashable."""
    canon = _canon({"instructions": text})
    return hash_of(canon, INSTRUCTIONS_PREFIX), canon


def is_instructions(obj: dict) -> bool:
    """A stored copy of instructions, rather than of a tool."""
    return set(obj) == {"instructions"} and isinstance(obj["instructions"], str)


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
    make_private(defs)
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
    if not is_hash(def_hash):
        raise CopyProblem("missing")  # a name from a hand-edited ledger is never used as a path
    path = Path(ledger_dir) / DEFS / f"{def_hash}.json"
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        raise CopyProblem("missing") from None
    except OSError as e:
        raise CopyProblem(f"unreadable: {e.strerror or describe(e)}") from None
    form = next(
        (f for f in (PREFIX, META_PREFIX, INSTRUCTIONS_PREFIX) if hash_of(raw, f) == def_hash),
        None,
    )
    if form is None:
        raise CopyProblem("does not match its hash")
    try:
        obj = json.loads(raw.decode("utf-8"))
        if not isinstance(obj, dict):
            raise ValueError("not an object")
        if form == INSTRUCTIONS_PREFIX:
            if not is_instructions(obj):
                raise ValueError("not instructions")
            return obj
        # The prefix follows from the form: /2 exactly when a top-level _meta is served.
        if ("_meta" in obj) != (form == META_PREFIX):
            raise ValueError("the prefix does not fit the form")
        types.Tool.model_validate(obj, by_alias=True, by_name=False)
    except Exception:
        raise CopyProblem("is not a valid tool") from None
    return obj


def served(obj: dict, exposed_name: str) -> types.Tool:
    """The tool Polarizer serves: the stored object, renamed, parsed by alias."""
    return types.Tool.model_validate({**obj, "name": exposed_name}, by_alias=True, by_name=False)


def render(obj: dict) -> str:
    """A definition as `pending` and `approve` print it: every character outside printable
    ASCII escaped. ensure_ascii leaves DEL as it is, so it is escaped here."""
    text = json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=True)
    return text.replace(chr(0x7F), chr(0x5C) + "u007f")
