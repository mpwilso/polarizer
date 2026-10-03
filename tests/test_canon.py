"""The canonical subset: RFC 8785 and the stdlib agree on it, and its rules are enforced."""

import json
import random

import pytest
import rfc8785

from polarizer.canon import (
    MAX_INT,
    EntryRefused,
    canonical,
    make_entry,
    pointer_part,
    subset_problem,
)

CHARS = [chr(c) for c in range(32)] + list("az AZ09\"\\/~") + [
    "\x7f", "\x80", "\xa0", "\xe9", "\u2028", "\u2029", "\ufeff", "\ufffd", "\ue000",
    "\U000e0049", "\U0001f600", "\U0010ffff",
]  # fmt: skip


def stdlib_form(value) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")


def random_value(rng, depth=0):
    kind = rng.randrange(7 if depth < 4 else 4)
    if kind == 0:
        return rng.randint(-MAX_INT, MAX_INT)
    if kind == 1:
        return "".join(rng.choice(CHARS) for _ in range(rng.randrange(8)))
    if kind == 2:
        return rng.choice([True, False, None])
    if kind == 3:
        return rng.choice([0, 1, -1, MAX_INT, -MAX_INT])
    if kind == 4:
        return [random_value(rng, depth + 1) for _ in range(rng.randrange(4))]
    keys = ["".join(rng.choice("abcXYZ_-~/ 09") for _ in range(rng.randrange(1, 5)))]
    keys += [rng.choice(["a", "B", "_x", "seq"]) for _ in range(rng.randrange(4))]
    return {k: random_value(rng, depth + 1) for k in keys}


def test_differential_rfc8785_matches_stdlib_on_the_subset():
    rng = random.Random(7)
    for _ in range(3000):
        value = random_value(rng)
        assert subset_problem(value) is None
        assert rfc8785.dumps(value) == stdlib_form(value)


def test_non_ascii_keys_break_the_equivalence():
    value = {"\ue000": 1, "\U0001f600": 2}
    assert rfc8785.dumps(value) != stdlib_form(value)
    assert subset_problem(value) == "non-ASCII key at /\\ue000"


@pytest.mark.parametrize(
    "value, rule",
    [
        ({"a": 1.5}, "float at /a"),
        ({"a": [1, 2**53]}, "integer out of range at /a/1"),
        ({"a": -(2**53)}, "integer out of range at /a"),
        ({"x": {"caf\xe9": 1}}, "non-ASCII key at /x/caf\\u00e9"),
        ({"s": "ok\ud800"}, "lone surrogate at /s"),
        ({"a/b~c": 0.5}, "float at /a~1b~0c"),
        ({"\U0001f600": 1}, "non-ASCII key at /\\ud83d\\ude00"),
        ({"t": (1, 2)}, "unsupported value at /t"),
    ],
)
def test_subset_rules(value, rule):
    assert subset_problem(value) == rule


def test_first_problem_in_document_order():
    assert subset_problem({"b": 1.5, "a": 2**60}) == "float at /b"


def test_integer_bounds_are_inclusive():
    assert subset_problem({"a": MAX_INT, "b": -MAX_INT}) is None


def test_pointer_part_escapes_controls():
    assert pointer_part("a\nb") == "a\\u000ab"


def test_make_entry_refuses_floats_and_oversize_lines():
    with pytest.raises(EntryRefused, match="float at /data/x"):
        make_entry(1, "t", "note", {"x": 0.5}, "0" * 64)
    with pytest.raises(EntryRefused, match="16 KiB"):
        make_entry(1, "t", "note", {"x": "y" * 16400}, "0" * 64)


def test_make_entry_line_is_canonical():
    entry, line = make_entry(
        3, "2026-10-02T00:00:00.000Z", "note", {"b": 1, "a": "\u2028"}, "f" * 64
    )
    assert line == canonical(entry) + b"\n" == stdlib_form(entry) + b"\n"
