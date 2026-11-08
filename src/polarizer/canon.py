"""Canonical bytes, the v1 subset and entry hashes (docs/LEDGER-SPEC.md, Part 1)."""

import hashlib
import re

import rfc8785

MAX_INT = 2**53 - 1
MAX_LINE = 16 * 1024  # bytes, including the newline
HASH_PREFIX = b"LEDGER-SPEC/1\n"
ZERO_HASH = "0" * 64
MAX_DEPTH = 64  # arrays and objects nest at most this deep in a line; the entry is level 1
_SURROGATE = re.compile("[\ud800-\udfff]")
_STRING = re.compile(rb'"(?:[^"\\]|\\.)*"', re.S)
_NOT_BRACKET = bytes(b for b in range(256) if b not in b"[]{}")


class EntryRefused(ValueError):
    """An entry a writer must not write: outside the subset, or longer than 16 KiB."""


def pointer_part(key: str) -> str:
    """One JSON pointer segment, printed as ASCII: ~ is ~0, / is ~1, and anything outside
    printable ASCII is a \\uXXXX escape (UTF-16 code units)."""
    key = key.replace("~", "~0").replace("/", "~1")
    return "".join(c if " " <= c <= "~" else _escape(c) for c in key)


def _escape(c: str) -> str:
    units = c.encode("utf-16-be", "surrogatepass")
    return "".join(
        f"\\u{int.from_bytes(units[i : i + 2], 'big'):04x}" for i in range(0, len(units), 2)
    )


def nesting(raw: bytes) -> int:
    """How deep a line's arrays and objects nest, read from its bytes without parsing them
    (docs/LEDGER-SPEC.md, Canonical bytes and the subset): brackets inside strings don't
    count. On a line that isn't JSON the number means nothing, and the parse refuses it."""
    marks = _STRING.sub(b"", raw).translate(None, _NOT_BRACKET)
    depth = deepest = 0
    for mark in marks:
        depth += 1 if mark in b"[{" else -1
        deepest = max(deepest, depth)
    return deepest


def subset_problem(value, path: str = "", depth: int = 1) -> str | None:
    """The first value outside the v1 subset, in document order, as a rule; None if all fit.
    `depth` is the nesting level of `value` when it is an array or object: 1 for an entry."""
    if isinstance(value, (dict, list)) and depth > MAX_DEPTH:
        return f"nested more than {MAX_DEPTH} deep at {path}"
    if isinstance(value, dict):
        for key, item in value.items():
            where = f"{path}/{pointer_part(key)}"
            if not key.isascii():
                return f"non-ASCII key at {where}"
            found = subset_problem(item, where, depth + 1)
            if found:
                return found
        return None
    if isinstance(value, list):
        for i, item in enumerate(value):
            found = subset_problem(item, f"{path}/{i}", depth + 1)
            if found:
                return found
        return None
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return None if -MAX_INT <= value <= MAX_INT else f"integer out of range at {path}"
    if isinstance(value, float):
        return f"float at {path}"
    if isinstance(value, str):
        return f"lone surrogate at {path}" if _SURROGATE.search(value) else None
    return f"unsupported value at {path}"


def canonical(value) -> bytes:
    """RFC 8785 canonical bytes. On the subset, identical to the stdlib's sorted compact form."""
    return rfc8785.dumps(value)


def entry_hash(entry: dict) -> str:
    body = {k: v for k, v in entry.items() if k != "hash"}
    return hashlib.sha256(HASH_PREFIX + canonical(body)).hexdigest()


def make_entry(seq: int, ts: str, kind: str, data: dict, prev: str) -> tuple[dict, bytes]:
    """A complete v1 entry and its line (canonical bytes plus newline). Raises EntryRefused."""
    entry = {"v": 1, "seq": seq, "ts": ts, "kind": kind, "data": data, "prev": prev}
    problem = subset_problem(entry)
    if problem:
        raise EntryRefused(f"{kind}: {problem}")
    entry["hash"] = entry_hash(entry)
    line = canonical(entry) + b"\n"
    if len(line) > MAX_LINE:
        raise EntryRefused(f"{kind}: entry is {len(line)} bytes, over the 16 KiB limit")
    return entry, line
