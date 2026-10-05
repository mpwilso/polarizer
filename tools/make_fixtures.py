"""Write the ledger conformance fixtures deterministically.

    python tools/make_fixtures.py [--out DIR]      (default: conformance/)

Every chain is built from fixed chain ids, session ids, timestamps and values. Hashes and
canonical bytes come from conformance/reference_verify.py, never from Polarizer's code, so the
fixtures don't check the code that wrote them. Each broken fixture comes from a valid one through
one named mutation function. The expected status of each fixture is declared here, next to the
fixture, and written to expected.json; nothing here runs a verifier.

A fixture that needs a ledger.head is a pair, <name>.jsonl and <name>.head.

The one input is conformance/valid/v0-parallax.jsonl, made once by tools/make_v0_fixture.py from
Parallax's real code. It is read, never written, here.
"""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "conformance"))
import reference_verify as ref  # noqa: E402

V0_COMMIT = "22ef60083b56683ab5abda19bb224309d07fee88"
V0_FILE = "valid/v0-parallax.jsonl"
ZERO = "0" * 64
CHAIN = "5f0c9e2a7b14d3e8a1c6f9b2d4e7a0c3"
OTHER_CHAIN = "0123456789abcdef0123456789abcdef"
SESSION = "8c1d4f7a2b5e9036"
ARGS = ["3" * 64, "4" * 64, "5" * 64, "6" * 64]


# Building blocks. All hashing and canonical bytes go through the reference verifier.


def ts(i):
    return f"2026-10-02T14:{i // 60:02d}:{i % 60:02d}.{(i * 37) % 1000:03d}Z"


def build(specs, chain_id=CHAIN):
    """[(kind, data), ...] after the genesis entry -> a list of v1 entries."""
    entries = []
    for i, (kind, data) in enumerate([("ledger.genesis", {"chain_id": chain_id}), *specs]):
        e = {"v": 1, "seq": i, "ts": ts(i), "kind": kind, "data": data}
        e["prev"] = entries[-1]["hash"] if entries else ZERO
        e["hash"] = ref.entry_hash(e)
        entries.append(e)
    return entries


def line(e):
    """The entry's canonical line. An entry deliberately outside the subset that rfc8785 refuses
    (an integer past 2^53-1) is written in the stdlib's sorted compact form instead."""
    try:
        return ref.canonical(e) + b"\n"
    except Exception:
        return (
            json.dumps(e, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
            + b"\n"
        )


def text(entries):
    return b"".join(line(e) for e in entries)


def head_bytes(seq, hash_, chain_id=CHAIN):
    return ref.canonical({"chain_id": chain_id, "hash": hash_, "seq": seq}) + b"\n"


def head_of(entry, chain_id=CHAIN):
    return head_bytes(entry["seq"], entry["hash"], chain_id)


def relink(entries, start):
    """Re-hash entries[start:] in place so each chains from the one before it."""
    for i in range(start, len(entries)):
        e = entries[i]
        e["prev"] = entries[i - 1]["hash"] if i else ZERO
        e["hash"] = ref.entry_hash({k: v for k, v in e.items() if k != "hash"})


def copy(entries):
    return json.loads(json.dumps(entries))


def lines_of(data):
    return data.split(b"\n")[:-1]


# Valid chains.


def session_chain():
    call = {"session": SESSION, "meta_dropped": []}
    return build(
        [
            ("session.started", {"session": SESSION, "polarizer_version": "0.1.0",
                                 "config_sha256": "7" * 64}),
            ("session.client", {"session": SESSION, "client_name": "claude-code",
                                "client_version": "2.1.287", "protocol_version": "2026-07-28"}),
            ("upstream.connected", {"prefix": "probe", "protocol_version": "2025-11-25",
                                    "tools": 1, "skipped_tools": []}),
            ("upstream.connected", {"prefix": "every", "error": "timeout after 10 s"}),
            ("call.sent", {**call, "tool": "probe__wait", "args_commit": ARGS[0],
                           "client_call_id": "toolu_01AbCdEf"}),
            ("call.returned", {"call_seq": 5, "outcome": "ok", "latency_ms": 1004,
                               "result_bytes": 61}),
            ("call.sent", {**call, "tool": "probe__wait", "args_commit": ARGS[1],
                           "client_call_id": None, "meta_dropped": ["x-trace"]}),
            ("call.returned", {"call_seq": 7, "outcome": "protocol-error", "latency_ms": 3,
                               "result_bytes": 0, "error": "upstream says no", "code": -32042}),
            ("call.refused", {"session": SESSION, "tool": "nope__x",
                              "reason": 'no upstream with prefix "nope"'}),
            ("ledger.head_rebuilt", {"from_seq": 9, "from_hash": "8" * 64}),
            ("call.sent", {**call, "tool": "probe__wait", "args_commit": ARGS[2],
                           "client_call_id": "toolu_02GhIjKl"}),
            ("call.returned", {"call_seq": 11, "outcome": "cancelled", "latency_ms": 5009,
                               "result_bytes": 0, "error": "cancelled by the client"}),
        ]
    )  # fmt: skip


def pins_chain():
    """Every kind M1a adds (docs/PIN-SPEC.md, section 3), with the data fields it specifies."""
    tool = {"upstream": "probe", "tool": "wait"}
    seen = {"session": SESSION, **tool}
    return build(
        [
            ("session.started", {"session": SESSION, "polarizer_version": "0.1.0",
                                 "config_sha256": "7" * 64}),
            ("tool.seen", {**seen, "def_hash": "a" * 64}),
            ("tool.unservable", {**seen, "tool": "big", "def_hash": None,
                                 "problem": "cannot be hashed: 9223372036854775807 exceeds safe "
                                            "integer domain for JSON floats"}),
            ("tool.approved", {**tool, "def_hash": "a" * 64, "actor": "person", "group": None}),
            ("tool.approved", {"upstream": "probe", "tool": "env", "def_hash": "e" * 64,
                               "actor": "person", "group": "9" * 64}),
            ("tool.drift", {**seen, "approved_hash": "a" * 64, "live_hash": "b" * 64}),
            ("tool.unservable", {**seen, "def_hash": "b" * 64,
                                 "problem": "stored copy does not match its hash"}),
            ("upstream.refresh_failed", {"session": SESSION, "prefix": "probe",
                                         "trigger": "client-list", "error": "timeout after 10 s"}),
            ("upstream.refresh_failed", {"session": SESSION, "prefix": "every",
                                         "trigger": "connection-lost",
                                         "error": "Connection closed"}),
            ("tool.rejected", {**tool, "def_hash": "b" * 64, "actor": "person",
                               "reason": "it reads every file"}),
            ("call.refused", {"session": SESSION, "tool": "probe__wait",
                              "reason": 'upstream probe tool "wait" is pending approval'}),
        ]
    )  # fmt: skip


HOLDS = ["1111222233334444", "5555666677778888", "9999aaaabbbbcccc", "ddddeeeeffff0000"]
OTHER_SESSION = "3e7a9c1b5d2f8064"


def holds_chain():
    """Every kind M2a adds and every optional field it adds (docs/HOLD-SPEC.md, section 5): a
    hold allowed and forwarded, one denied, one expired, one abandoned by a later start, an
    unheld call with its allowed_by, and a refusal for too many holds."""
    started = {"session": SESSION, "polarizer_version": "0.1.0", "config_sha256": "7" * 64}

    def created(hold, tool, commit, cls, class_from, rule, reason):
        return (
            "hold.created",
            {
                "session": SESSION,
                "hold": hold,
                "tool": tool,
                "args_commit": commit,
                "class": cls,
                "class_from": class_from,
                "rule": rule,
                "reason": reason,
                "timeout_seconds": 300,
            },
        )

    def decided(hold, commit, decision, reason):
        return (
            "hold.decided",
            {
                "hold": hold,
                "args_commit": commit,
                "decision": decision,
                "actor": "person",
                "reason": reason,
            },
        )

    def refused(tool, reason, **hold):
        return ("call.refused", {"session": SESSION, "tool": tool, "reason": reason, **hold})

    sent = {"session": SESSION, "meta_dropped": [], "client_call_id": None}
    return build(
        [
            ("session.started", started),
            ("policy.loaded", {"session": SESSION, "holds": "on", "policy_sha256": "8" * 64,
                               "classified": 13, "workspace_roots": ["/home/me/code/proj"],
                               "hold_timeout_seconds": 300}),
            created(HOLDS[0], "fs__move_file", ARGS[0], "destructive", "config", "destructive",
                    "class destructive is held on every call"),
            decided(HOLDS[0], ARGS[0], "allow", None),
            ("call.sent", {**sent, "tool": "fs__move_file", "args_commit": ARGS[0],
                           "hold": HOLDS[0], "allowed_by": "hold"}),
            ("call.returned", {"call_seq": 5, "outcome": "ok", "latency_ms": 4,
                               "result_bytes": 70}),
            created(HOLDS[1], "fs__write_file", ARGS[1], None, None, "unclassified",
                    "fs__write_file has no class in polarizer.toml"),
            decided(HOLDS[1], ARGS[1], "deny", "not in this repo"),
            refused("fs__write_file", f"hold {HOLDS[1]} was denied", hold=HOLDS[1]),
            created(HOLDS[2], "web__post", ARGS[2], "egress", "annotations", "egress",
                    "class egress is held on every call (class from annotations)"),
            ("hold.expired", {"session": SESSION, "hold": HOLDS[2],
                              "reason": "timeout after 300 s"}),
            refused("web__post", f"hold {HOLDS[2]} expired: timeout after 300 s", hold=HOLDS[2]),
            created(HOLDS[3], "fs__edit_file", ARGS[3], "local-write", "config", "outside-roots",
                    'argument "path": /etc/hosts is outside every workspace root'),
            ("session.started", {**started, "session": OTHER_SESSION}),
            ("policy.loaded", {"session": OTHER_SESSION, "holds": "off",
                               "policy_sha256": "8" * 64, "classified": 13,
                               "workspace_roots": [], "hold_timeout_seconds": 300}),
            ("hold.abandoned", {"session": OTHER_SESSION, "hold": HOLDS[3], "held_by": SESSION}),
            ("call.sent", {**sent, "session": OTHER_SESSION, "tool": "fs__read_text_file",
                           "args_commit": ARGS[2], "allowed_by": "holds-off"}),
            refused("fs__delete", "too many held calls: 16 already wait in this session"),
        ]
    )  # fmt: skip


DRILL = "d1a2b3c4d5e6f708"


def drills_chain():
    """Every kind stage 8 adds (docs/MEASURE-SPEC.md, section 8), with each data field: a
    prediction-gate drill kept with --keep-predictions, one call caught and one a false flag,
    stopped with q, then a plain drill that never ended. Written to a drill ledger, which has no
    session.started."""
    started = {
        "polarizer_version": "0.1.0",
        "set": 1,
        "set_sha256": "e" * 64,
        "seed": "5eed" * 8,
        "seed_from": "random",
        "calls": 20,
    }
    shown = {"session": DRILL, "args_commit": "c" * 64, "seen_before": 0}
    return build(
        [
            ("drill.started", {"session": DRILL, **started, "condition": "prediction-gate",
                               "condition_from": "random", "excluded": [],
                               "keep_predictions": True}),
            ("drill.shown", {**shown, "n": 1, "scenario": "s042", "hold": "0123456789abcdef"}),
            ("drill.predicted", {"session": DRILL, "n": 1, "length": 21,
                                 "prediction": "it writes a git hook", "elapsed_ms": 3100}),
            ("drill.decided", {"session": DRILL, "n": 1, "scenario": "s042", "decision": "deny",
                               "elapsed_ms": 9400, "actor": "person"}),
            ("drill.revealed", {"session": DRILL, "n": 1, "scenario": "s042",
                                "answer": "planted", "shape": "changed-argument",
                                "outcome": "caught"}),
            ("drill.shown", {**shown, "n": 2, "scenario": "s107", "hold": "fedcba9876543210",
                             "seen_before": 1}),
            ("drill.predicted", {"session": DRILL, "n": 2, "length": 12, "prediction": None,
                                 "elapsed_ms": 1800}),
            ("drill.decided", {"session": DRILL, "n": 2, "scenario": "s107", "decision": "deny",
                               "elapsed_ms": 301250, "actor": "person"}),
            ("drill.revealed", {"session": DRILL, "n": 2, "scenario": "s107", "answer": "clean",
                                "shape": None, "outcome": "false-flag"}),
            ("drill.ended", {"session": DRILL, "how": "stopped", "answered": 2, "calls": 20}),
            ("drill.started", {"session": "d2a2b3c4d5e6f708", **started, "condition": "plain",
                               "condition_from": "flag", "excluded": ["s042", "s107"],
                               "keep_predictions": False}),
        ]
    )  # fmt: skip


def unicode_chain():
    values = {
        "controls": "".join(chr(c) for c in range(32)) + "\x7f",
        "separators": "\u2028\u2029\ufeff\xa0",
        "astral": "\U0001f600\U000e0049\U0010ffff",
        "private": "\ue000\ufffd",
        "quotes": 'a "quoted" \\ back\\slash /',
        "ints": [2**53 - 1, -(2**53 - 1), 0, -1],
        "nested": {"a": [[], {}, [{"b": None}]], "t": True, "f": False},
    }
    return build([("note.unicode", values), ("note.empty", {})])


# Mutations. Each takes the bytes of a valid fixture and returns broken bytes.


def edit_value(entries, index):
    out = copy(entries)
    out[index]["data"]["latency_ms"] = 1
    return text(out)


def delete_middle_line(entries, index):
    return text(entries[:index] + entries[index + 1 :])


def swap_lines(entries, index):
    out = list(entries)
    out[index], out[index + 1] = out[index + 1], out[index]
    return text(out)


def tear_last_line(data):
    last = lines_of(data)[-1] + b"\n"
    return data[: len(data) - len(last)] + last[: len(last) // 2]


def reorder_keys(entries, index):
    raw = lines_of(text(entries))
    e = entries[index]
    raw[index] = json.dumps({k: e[k] for k in reversed(list(e))}, separators=(",", ":")).encode()
    return b"\n".join(raw) + b"\n"


def change_data(entries, index, update, rehash=True):
    out = copy(entries)
    out[index]["data"].update(update)
    if rehash:
        relink(out, index)
    return text(out)


def insert_float(entries, index):
    return change_data(entries, index, {"ratio": 1.5})


def big_integer(entries, index):
    return change_data(entries, index, {"count": 2**53}, rehash=False)  # rfc8785 refuses 2^53


def non_ascii_key(entries, index):
    return change_data(entries, index, {"caf\xe9": 1})


def lone_surrogate(entries, index):
    raw = lines_of(text(entries))
    e = copy(entries)[index]
    e["data"]["s"] = "\ud800"
    raw[index] = json.dumps(e, sort_keys=True, separators=(",", ":")).encode()  # escaped
    return b"\n".join(raw) + b"\n"


DELETE = object()


def set_top_level(entries, index, key, value, rehash=True):
    out = copy(entries)
    if value is DELETE:
        del out[index][key]
    else:
        out[index][key] = value
    if rehash:
        relink(out, index)
    return text(out)


def extra_top_level_key(entries, index, rehash=True):
    return set_top_level(entries, index, "extra", "x", rehash)


def missing_key(entries, index):
    return set_top_level(entries, index, "ts", DELETE)


def wrong_type(entries, index):
    return set_top_level(entries, index, "kind", 7)


def v_not_1(entries, index):
    return set_top_level(entries, index, "v", 2)


def bad_seq(entries, index, rehash=True):
    return set_top_level(entries, index, "seq", entries[index]["seq"] + 10, rehash)


def bad_prev(entries, index):
    out = copy(entries)
    out[index]["prev"] = "9" * 64
    out[index]["hash"] = ref.entry_hash({k: v for k, v in out[index].items() if k != "hash"})
    relink(out, index + 1)
    return text(out)


def bad_hash(entries, index):
    out = copy(entries)
    out[index]["hash"] = "a" * 64
    return text(out)


def second_genesis(entries):
    out = copy(entries)
    out.append({"v": 1, "seq": len(out), "ts": ts(len(out)), "kind": "ledger.genesis",
                "data": {"chain_id": OTHER_CHAIN}, "prev": out[-1]["hash"]})  # fmt: skip
    relink(out, len(out) - 1)
    return text(out)


def delete_first_line(entries):
    return text(entries[1:])


def bad_chain_id(entries):
    return change_data(entries, 0, {"chain_id": "NOT-HEX"})


def oversize_line(entries, index):
    out = copy(entries)
    out[index]["data"]["pad"] = "x" * 16400
    relink(out, index)
    return text(out)


def replace_line(data, index, raw):
    lines = lines_of(data)
    lines[index] = raw
    return b"\n".join(lines) + b"\n"


def garbage_line(data, index):
    return replace_line(data, index, b'{"v":1,"seq":')


def bad_utf8(data, index):
    return replace_line(data, index, b'{"v":1,"note":"\xff"}')


def not_object(data, index):
    return replace_line(data, index, b"[1,2,3]")


def mix_v0_v1(entries, v0_data, index):
    lines = lines_of(text(entries))
    lines.insert(index, lines_of(v0_data)[0])
    return b"\n".join(lines) + b"\n"


def edit_v0_entry(v0_data, index):
    lines = lines_of(v0_data)
    e = json.loads(lines[index])
    e["reason"] = "edited after writing"
    lines[index] = json.dumps(e, sort_keys=True).encode()
    return b"\n".join(lines) + b"\n"


def v0_unsorted_line(v0_data, index):
    lines = lines_of(v0_data)
    e = json.loads(lines[index])
    lines[index] = json.dumps(dict(reversed(list(e.items())))).encode()
    return b"\n".join(lines) + b"\n"


def truncate(entries, keep):
    return text(entries[:keep])


def head_text(seq, hash_, chain_id=CHAIN):
    return head_bytes(seq, hash_, chain_id)


# The fixtures: name -> (ledger bytes, head bytes or None, expected).


def expect(status, line, seq, check=None):
    return {"status": status, "line": line, "seq": seq, "check": check}


def fixtures(v0_data):
    s = session_chain()
    n = len(s)  # 13 entries, seq 0 to 12
    u = unicode_chain()
    p = pins_chain()
    h = holds_chain()
    d = drills_chain()
    m = build([])
    v0_lines = len(lines_of(v0_data))
    rebuilt = s[10]  # ledger.head_rebuilt, a security entry behind the last one
    torn = tear_last_line(text(s))
    out = {
        # Valid.
        "valid/minimal": (text(m), head_of(m[0]), expect("intact", 1, 0)),
        "valid/session": (text(s), head_of(rebuilt), expect("intact", n, n - 1)),
        "valid/unicode": (text(u), head_of(u[0]), expect("intact", 3, 2)),
        "valid/head_at_last": (text(s), head_of(s[-1]), expect("intact", n, n - 1)),
        "valid/no_head": (text(s), None, expect("intact", n, n - 1)),
        "valid/pins": (text(p), head_of(p[10]), expect("intact", len(p), len(p) - 1)),
        "valid/holds": (text(h), head_of(h[15]), expect("intact", len(h), len(h) - 1)),
        # A drill ledger's ledger.head stays at its genesis: no drill kind is fsynced inline.
        "valid/drills": (text(d), head_of(d[0]), expect("intact", len(d), len(d) - 1)),
        # Tampered lines.
        "broken/edit_value": (edit_value(s, 6), None, expect("tampered", 7, 6, "hash")),
        "broken/delete_middle_line": (
            delete_middle_line(s, 4),
            None,
            expect("tampered", 5, 5, "seq"),
        ),
        "broken/swap_lines": (swap_lines(s, 3), None, expect("tampered", 4, 4, "seq")),
        "broken/bad_prev": (bad_prev(s, 8), None, expect("tampered", 9, 8, "prev")),
        "broken/bad_hash": (bad_hash(s, 2), None, expect("tampered", 3, 2, "hash")),
        "broken/bad_genesis_prev": (bad_prev(m, 0), None, expect("tampered", 1, 0, "prev")),
        # An approval whose hash was edited, and one whose edit was re-hashed but whose head
        # still names the original: the chain and ledger.head each catch it.
        "broken/pins_edit_approval": (
            change_data(p, 4, {"def_hash": "f" * 64}, rehash=False),
            None,
            expect("tampered", 5, 4, "hash"),
        ),
        "broken/pins_rehashed_rejection": (
            change_data(p, 10, {"def_hash": "a" * 64}),
            head_of(p[10]),
            expect("tampered", None, 10, "head"),
        ),
        # A deny edited into an allow, as is and re-hashed: the chain and ledger.head each
        # catch it (ledger.head names policy.loaded, the security entry after the deny).
        "broken/holds_edit_decision": (
            change_data(h, 8, {"decision": "allow"}, rehash=False),
            None,
            expect("tampered", 9, 8, "hash"),
        ),
        "broken/holds_rehashed_decision": (
            change_data(h, 8, {"decision": "allow"}),
            head_of(h[15]),
            expect("tampered", None, 15, "head"),
        ),
        "broken/bad_seq": (bad_seq(s, 5), None, expect("tampered", 6, 15, "seq")),
        # Invalid lines.
        "broken/insert_float": (insert_float(s, 6), None, expect("invalid", 7, 6, "structure")),
        "broken/big_integer": (big_integer(s, 6), None, expect("invalid", 7, 6, "structure")),
        "broken/non_ascii_key": (non_ascii_key(s, 9), None, expect("invalid", 10, 9, "structure")),
        "broken/lone_surrogate": (
            lone_surrogate(s, 9),
            None,
            expect("invalid", 10, 9, "structure"),
        ),
        "broken/extra_top_level_key": (
            extra_top_level_key(s, 2),
            None,
            expect("invalid", 3, 2, "structure"),
        ),
        "broken/missing_key": (missing_key(s, 2), None, expect("invalid", 3, 2, "structure")),
        "broken/wrong_type": (wrong_type(s, 2), None, expect("invalid", 3, 2, "structure")),
        "broken/v_not_1": (v_not_1(s, 2), None, expect("invalid", 3, 2, "structure")),
        "broken/second_genesis": (
            second_genesis(s),
            None,
            expect("invalid", n + 1, n, "structure"),
        ),
        "broken/delete_first_line": (
            delete_first_line(s),
            None,
            expect("invalid", 1, 1, "structure"),
        ),
        "broken/bad_chain_id": (bad_chain_id(s), None, expect("invalid", 1, 0, "structure")),
        "broken/oversize_line": (oversize_line(s, 3), None, expect("invalid", 4, 3, "structure")),
        "broken/garbage_line": (
            garbage_line(text(s), 4),
            None,
            expect("invalid", 5, None, "structure"),
        ),
        "broken/bad_utf8": (bad_utf8(text(s), 4), None, expect("invalid", 5, None, "structure")),
        "broken/not_object": (
            not_object(text(s), 4),
            None,
            expect("invalid", 5, None, "structure"),
        ),
        "broken/mix_v0_v1": (
            mix_v0_v1(s, v0_data, 3),
            None,
            expect("invalid", 4, None, "structure"),
        ),
        # Not canonical.
        "broken/reorder_keys": (
            reorder_keys(s, 4),
            None,
            expect("not canonical", 5, 4, "canonical"),
        ),
        # Two faults on one line pin the per-line order.
        "broken/extra_key_and_bad_hash": (
            extra_top_level_key(s, 6, rehash=False),
            None,
            expect("invalid", 7, 6, "structure"),
        ),
        "broken/bad_seq_and_bad_hash": (
            bad_seq(s, 6, rehash=False),
            None,
            expect("tampered", 7, 16, "seq"),
        ),
        # Torn tails.
        "broken/tear_last_line": (
            torn,
            head_of(rebuilt),
            expect("torn tail", n - 1, n - 2),
        ),
        "broken/torn_genesis": (
            tear_last_line(text(m)),
            None,
            expect("torn tail", 0, None),
        ),
        # Head problems, and the order of head problems against lines and the torn tail.
        "broken/truncated": (
            truncate(s, 8),
            head_of(rebuilt),
            expect("truncated", None, 10, "head"),
        ),
        "broken/torn_genesis_with_head": (
            tear_last_line(text(m)),
            head_of(m[0]),
            expect("truncated", None, 0, "head"),
        ),
        "broken/torn_tail_and_truncated": (
            tear_last_line(truncate(s, 9)),
            head_of(rebuilt),
            expect("truncated", None, 10, "head"),
        ),
        "broken/tampered_line_and_head_mismatch": (
            edit_value(s, 6),
            head_text(10, "b" * 64),
            expect("tampered", 7, 6, "hash"),
        ),
        "broken/head_hash_mismatch": (
            text(s),
            head_text(10, "b" * 64),
            expect("tampered", None, 10, "head"),
        ),
        "broken/head_wrong_chain": (
            text(s),
            head_of(rebuilt, OTHER_CHAIN),
            expect("invalid", None, 10, "head"),
        ),
        "broken/head_not_canonical": (
            text(s),
            b'{"chain_id": "%s", "hash": "%s", "seq": 10}\n'
            % (CHAIN.encode(), rebuilt["hash"].encode()),
            expect("invalid", None, None, "head"),
        ),
        "broken/head_no_newline": (
            text(s),
            head_of(rebuilt)[:-1],
            expect("invalid", None, None, "head"),
        ),
        "broken/head_extra_key": (
            text(s),
            ref.canonical({"chain_id": CHAIN, "hash": rebuilt["hash"], "seq": 10, "x": 1}) + b"\n",
            expect("invalid", None, None, "head"),
        ),
        # v0.
        "valid/v0-parallax": (None, None, expect("intact", v0_lines, None)),
        "broken/edit_v0_entry": (
            edit_v0_entry(v0_data, 1),
            None,
            expect("tampered", 2, None, "hash"),
        ),
        "broken/v0_unsorted_line": (
            v0_unsorted_line(v0_data, 2),
            None,
            expect("not canonical", 3, None, "canonical"),
        ),
        "broken/v0_torn": (
            tear_last_line(v0_data),
            None,
            expect("torn tail", v0_lines - 1, None),
        ),
        "broken/v0_with_head": (
            v0_data,
            head_text(0, "d" * 64),
            expect("invalid", None, 0, "head"),
        ),
        "broken/v0_then_v1": (
            v0_data + line(s[0]),
            None,
            expect("invalid", v0_lines + 1, None, "structure"),
        ),
    }
    return out


def write(out_dir):
    out_dir = Path(out_dir)
    v0_data = (ROOT / "conformance" / V0_FILE).read_bytes()
    expected = {}
    for name, (ledger, head, want) in fixtures(v0_data).items():
        expected[name] = {**want, "head": head is not None}
        if ledger is None:  # the v0 input, made by tools/make_v0_fixture.py
            continue
        path = out_dir / f"{name}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(ledger)
        if head is not None:
            path.with_suffix(".head").write_bytes(head)
    doc = {
        "about": "Expected verify result for each fixture. line and seq are null where they "
        "don't apply (head problems have no line; v0 has no seq). check names the failing "
        "check. head says whether <name>.head exists. Written by tools/make_fixtures.py.",
        "v0_source": {"repo": "parallax", "file": "parallax/ledger.py", "commit": V0_COMMIT},
        "fixtures": expected,
    }
    # Bytes, not text: text mode would write \r\n on Windows and break the byte-for-byte check.
    text = json.dumps(doc, indent=1, sort_keys=True) + "\n"
    (out_dir / "expected.json").write_bytes(text.encode("utf-8"))


def main(argv=None):
    parser = argparse.ArgumentParser(description="Write the conformance fixtures.")
    parser.add_argument("--out", default=str(ROOT / "conformance"))
    write(parser.parse_args(argv).out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
