"""Canonical bytes, the v1 subset and entry hashes (docs/LEDGER-SPEC.md, Part 1)."""

import hashlib
import re

import rfc8785

MAX_INT = 2**53 - 1
MAX_LINE = 16 * 1024  # bytes, including the newline
HASH_PREFIX = b"LEDGER-SPEC/1\n"
ZERO_HASH = "0" * 64
_SURROGATE = re.compile("[\ud800-\udfff]")


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


def subset_problem(value, path: str = "") -> str | None:
    """The first value outside the v1 subset, in document order, as a rule; None if all fit."""
    if isinstance(value, dict):
        for key, item in value.items():
            where = f"{path}/{pointer_part(key)}"
            if not key.isascii():
                return f"non-ASCII key at {where}"
            found = subset_problem(item, where)
            if found:
                return found
        return None
    if isinstance(value, list):
        for i, item in enumerate(value):
            found = subset_problem(item, f"{path}/{i}")
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
