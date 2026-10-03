"""Differential fuzz: Polarizer's verifier and conformance/reference_verify.py agree on status,
line and seq for randomly mutated chains.

From a fixed seed, build valid v1 chains (and sometimes the v0 fixture), apply random mutations
(bit flips in lines, deleted, swapped and duplicated lines, a truncated last line, a reformatted
line, a truncated file, edits to ledger.head, and structure-level edits: reordered keys with the
hash kept, whitespace between tokens, a character as its \\uXXXX escape, an integer as 1.0 or 1e0,
a UTF-8 BOM at a line's start, and a CR before the newline), and run both verifiers on each result. Any disagreement writes a
reproducer to .repro/ (gitignored) and fails, naming the seed and case.

    POLARIZER_FUZZ_SEED=<n> POLARIZER_FUZZ_CASES=<n> uv run pytest tests/test_differential.py -s
"""

import json
import os
import random
import time

import reference_verify as ref
from conftest import CONFORMANCE, ROOT

from polarizer.ledger import verify_bytes

SEED = int(os.environ.get("POLARIZER_FUZZ_SEED", "20261002"))
CASES = int(os.environ.get("POLARIZER_FUZZ_CASES", "5000"))
REPRO = ROOT / ".repro"
KINDS = ["session.started", "call.sent", "call.returned", "tool.approved", "note"]
TEXT = ["", "a", "probe__wait", "café", " ", "\U0001f600", "\x00\x1f", '"\\', "﻿"]


def value(rng, depth=0):
    pick = rng.randrange(6 if depth < 2 else 3)
    if pick == 0:
        return rng.choice([0, 1, -1, 2**53 - 1, -(2**53 - 1), rng.randrange(10**6)])
    if pick == 1:
        return rng.choice(TEXT)
    if pick == 2:
        return rng.choice([True, False, None])
    if pick == 3:
        return [value(rng, depth + 1) for _ in range(rng.randrange(3))]
    return {rng.choice("abxyz") + str(i): value(rng, depth + 1) for i in range(rng.randrange(3))}


def chain(rng):
    """A valid v1 chain, hashed with the reference verifier's functions, and maybe its head."""
    chain_id = f"{rng.getrandbits(128):032x}"
    lines, hashes, prev = [], [], "0" * 64
    for seq in range(rng.randint(1, 12)):
        kind = "ledger.genesis" if seq == 0 else rng.choice(KINDS)
        data = {"chain_id": chain_id} if seq == 0 else value(rng, 1) if rng.random() < 0.3 else {}
        if not isinstance(data, dict):
            data = {"x": data}
        e = {"v": 1, "seq": seq, "ts": f"2026-10-02T00:00:{seq % 60:02d}.000Z", "kind": kind,
             "data": data, "prev": prev}  # fmt: skip
        e["hash"] = ref.entry_hash(e)
        lines.append(ref.canonical(e) + b"\n")
        hashes.append(e["hash"])
        prev = e["hash"]
    head = None
    if rng.random() < 0.7:
        at = rng.randrange(len(hashes))
        head = ref.canonical({"chain_id": chain_id, "hash": hashes[at], "seq": at}) + b"\n"
    return b"".join(lines), head


def split(data):
    lines = data.split(b"\n")
    return lines[:-1], lines[-1]  # complete lines, tail


def join(lines, tail=b""):
    return b"".join(line + b"\n" for line in lines) + tail


def bit_flips(rng, data, head):
    lines, tail = split(data)
    if not lines:
        return data, head
    for _ in range(rng.randint(1, 3)):
        i = rng.randrange(len(lines))
        if lines[i]:
            j = rng.randrange(len(lines[i]))
            flipped = lines[i][j] ^ (1 << rng.randrange(8))
            lines[i] = lines[i][:j] + bytes([flipped]) + lines[i][j + 1 :]
    return join(lines, tail), head


def delete_line(rng, data, head):
    lines, tail = split(data)
    if lines:
        del lines[rng.randrange(len(lines))]
    return join(lines, tail), head


def swap_adjacent(rng, data, head):
    lines, tail = split(data)
    if len(lines) > 1:
        i = rng.randrange(len(lines) - 1)
        lines[i], lines[i + 1] = lines[i + 1], lines[i]
    return join(lines, tail), head


def duplicate_line(rng, data, head):
    lines, tail = split(data)
    if lines:
        i = rng.randrange(len(lines))
        lines.insert(i + 1, lines[i])
    return join(lines, tail), head


def truncate_last_line(rng, data, head):
    lines, tail = split(data)
    if lines:
        last = lines.pop()
        return join(lines, last[: rng.randrange(len(last) + 1)]), head
    return data, head


def reformat_line(rng, data, head):
    """The same entry in other bytes: spaces, ASCII escapes, or unsorted keys."""
    lines, tail = split(data)
    if not lines:
        return data, head
    i = rng.randrange(len(lines))
    try:
        e = json.loads(lines[i])
    except ValueError:
        return data, head
    style = rng.randrange(3)
    if style == 0:
        lines[i] = json.dumps(e, sort_keys=True).encode()
    elif style == 1:
        lines[i] = json.dumps(e, sort_keys=True, separators=(",", ":")).encode()
    else:
        lines[i] = json.dumps(dict(reversed(list(e.items()))), separators=(",", ":")).encode()
    return join(lines, tail), head


# Structure-level mutations: the same JSON values, or nearly, in other bytes. Each one edits a
# random complete line, or now and then the ledger.head line.


def spans(text):
    """[(kind, start, end)] for each string literal (with its quotes) and number in JSON text."""
    out, i = [], 0
    while i < len(text):
        c = text[i]
        if c == '"':
            j = i + 1
            while j < len(text) and text[j] != '"':
                j += 2 if text[j] == "\\" else 1
            out.append(("string", i, min(j + 1, len(text))))
            i = j + 1
        elif c in "-0123456789":
            j = i + 1
            while j < len(text) and text[j] in "0123456789.eE+-":
                j += 1
            out.append(("number", i, j))
            i = j
        else:
            i += 1
    return out


def outside_strings(text):
    inside = set()
    for kind, a, b in spans(text):
        if kind == "string":
            inside.update(range(a, b))
    return [i for i in range(len(text) + 1) if i not in inside]


def on_text(edit):
    """Lift a str -> str edit to (rng, data, head) on a random line or on the head line."""

    def mutation(rng, data, head):
        lines, tail = split(data)
        target_head = head is not None and (not lines or rng.random() < 0.2)
        raw = head[:-1] if target_head and head.endswith(b"\n") else head if target_head else None
        if not target_head:
            if not lines:
                return data, head
            i = rng.randrange(len(lines))
            raw = lines[i]
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            return data, head
        new = edit(rng, text).encode("utf-8", "surrogatepass")
        if target_head:
            return data, new + (b"\n" if head.endswith(b"\n") else b"")
        lines[i] = new
        return join(lines, tail), head

    mutation.__name__ = edit.__name__
    return mutation


def reorder_keys_keep_hash(rng, text):
    """The same entry, hash field and all, with object keys in another order."""
    try:
        value = json.loads(text)
    except ValueError:
        return text

    def shuffle(x):
        if isinstance(x, dict):
            items = [(k, shuffle(v)) for k, v in x.items()]
            rng.shuffle(items)
            return dict(items)
        if isinstance(x, list):
            return [shuffle(v) for v in x]
        return x

    new = json.dumps(shuffle(value), separators=(",", ":"), ensure_ascii=False)
    if new == text and isinstance(value, dict) and len(value) > 1:
        new = json.dumps(
            dict(reversed(list(value.items()))), separators=(",", ":"), ensure_ascii=False
        )
    return new


def add_whitespace(rng, text):
    """Spaces, tabs or a CR/LF-free run of whitespace between two tokens, or at either end."""
    where = rng.choice(outside_strings(text))
    return text[:where] + rng.choice([" ", "  ", "\t", " \t"]) + text[where:]


def escape_char(rng, text):
    """One character inside a string literal written as its \\uXXXX escape."""
    candidates = []
    for kind, a, b in spans(text):
        if kind != "string":
            continue
        j = a + 1
        while j < b - 1:
            if text[j] == "\\":
                j += 6 if text[j + 1 : j + 2] == "u" else 2
                continue
            candidates.append(j)
            j += 1
    if not candidates:
        return text
    j = rng.choice(candidates)
    units = text[j].encode("utf-16-be", "surrogatepass")
    digits = "".join(
        f"\\u{int.from_bytes(units[k : k + 2], 'big'):04x}" for k in range(0, len(units), 2)
    )
    return (
        text[:j]
        + (digits.upper().replace("\\U", "\\u") if rng.random() < 0.3 else digits)
        + text[j + 1 :]
    )


def integer_as_float(rng, text):
    """One integer written as N.0 or as Ne0."""
    numbers = [
        (a, b) for kind, a, b in spans(text) if kind == "number" and text[a:b].lstrip("-").isdigit()
    ]
    if not numbers:
        return text
    a, b = rng.choice(numbers)
    return text[:a] + text[a:b] + rng.choice([".0", "e0"]) + text[b:]


def bom_at_line_start(rng, text):
    return "\ufeff" + text


def cr_before_newline(rng, text):
    return text + "\r"


def truncate_file(rng, data, head):
    return data[: rng.randrange(len(data) + 1)], head


def edit_head(rng, data, head):
    if head is None:
        head = b'{"chain_id":"%032x","hash":"%064x","seq":0}\n' % (0, 0)
    h = json.loads(head)
    pick = rng.randrange(8)
    if pick == 0:
        h["seq"] += rng.choice([-1, 1, 2, 50])
    elif pick == 1:
        h["hash"] = f"{rng.getrandbits(256):064x}"
    elif pick == 2:
        h["chain_id"] = f"{rng.getrandbits(128):032x}"
    elif pick == 3:
        return data, head[:-1]  # no newline
    elif pick == 4:
        return data, json.dumps(h).encode() + b"\n"  # spaces: not canonical
    elif pick == 5:
        j = rng.randrange(len(head))
        return data, head[:j] + bytes([head[j] ^ (1 << rng.randrange(8))]) + head[j + 1 :]
    elif pick == 6:
        return data, None  # deleted
    else:
        h["extra"] = 1
    try:
        return data, ref.canonical(h) + b"\n"
    except Exception:
        return data, json.dumps(h, separators=(",", ":")).encode() + b"\n"


MUTATIONS = [
    bit_flips,
    delete_line,
    swap_adjacent,
    duplicate_line,
    truncate_last_line,
    reformat_line,
    truncate_file,
    edit_head,
    on_text(reorder_keys_keep_hash),
    on_text(add_whitespace),
    on_text(escape_char),
    on_text(integer_as_float),
    on_text(bom_at_line_start),
    on_text(cr_before_newline),
]


def run_reference(tmp_path, data, head):
    ledger = tmp_path / "case.jsonl"
    ledger.write_bytes(data)
    head_path = None
    if head is not None:
        head_path = tmp_path / "case.head"
        head_path.write_bytes(head)
    got = ref.verify(ledger, head_path)
    return got["status"], got["line"], got["seq"]


def run_polarizer(data, head):
    result = verify_bytes(data, head)
    return result.status, result.line, result.seq


def test_both_verifiers_agree_on_mutated_chains(tmp_path, capsys):
    rng = random.Random(SEED)
    v0 = (CONFORMANCE / "valid" / "v0-parallax.jsonl").read_bytes()
    start = time.monotonic()
    disagreements, statuses, by_mutation = [], {}, {}
    for case in range(CASES):
        data, head = (v0, None) if rng.random() < 0.1 else chain(rng)
        applied = rng.sample(MUTATIONS, rng.choice([0, 1, 1, 1, 2, 2, 3]))
        for mutate in applied:
            data, head = mutate(rng, data, head)
        theirs = run_reference(tmp_path, data, head)
        ours = run_polarizer(data, head)
        statuses[ours[0]] = statuses.get(ours[0], 0) + 1
        for m in applied:
            row = by_mutation.setdefault(m.__name__, {})
            row[ours[0]] = row.get(ours[0], 0) + 1
        if theirs != ours:
            REPRO.mkdir(exist_ok=True)
            stem = REPRO / f"seed{SEED}-case{case}"
            stem.with_suffix(".jsonl").write_bytes(data)
            if head is not None:
                stem.with_suffix(".head").write_bytes(head)
            names = [m.__name__ for m in applied]
            disagreements.append(
                f"case {case} {names}: reference {theirs}, polarizer {ours}; "
                f"reproducer {stem}.jsonl; ledger {data[:400]!r}; head {head!r}"
            )
    took = time.monotonic() - start
    with capsys.disabled():
        print(f"\ndifferential: seed {SEED}, {CASES} cases, {took:.1f} s")
        print("  cases per status: " + ", ".join(f"{k} {v}" for k, v in sorted(statuses.items())))
        for name, row in sorted(by_mutation.items()):
            print(f"  {name}: " + ", ".join(f"{k} {v}" for k, v in sorted(row.items())))
    assert not disagreements, f"seed {SEED}: {len(disagreements)} disagreements\n" + "\n".join(
        disagreements[:10]
    )
    assert took < 60, f"the default run took {took:.1f} s, over the 60 s limit"
    assert set(statuses) == {
        "intact",
        "tampered",
        "invalid",
        "not canonical",
        "torn tail",
        "truncated",
    }
