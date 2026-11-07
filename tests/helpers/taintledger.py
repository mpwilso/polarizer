"""A deterministic ledger with a tainted Claude Code session, for holds_tainted.txt,
stats_tainted.txt and the card's taint row (docs/TAINT-SPEC.md, sections 9 and 12).

Built from named pieces, one function per group of entries, each with a docstring saying what it
shows, as holds_mixed.txt's ledger is (tests/helpers/holdledger.py). Timestamps come from
conftest.build_chain, 2026-10-02T15:00:00.<seq>Z, so the taint at seq 4 reads
2026-10-02T15:00:00.004Z, and holdledger.NOW is the clock the tests inject.

Three serve sessions: TAINTED and RESTARTED share the client session CLIENT (a restart of serve
in one Claude Code session), CLEAN has another. Each has a lock file; the tests hold all three,
so every session reads as running.
"""

from pathlib import Path

from conftest import build_chain
from helpers.holdledger import created, lock_files, side_file, started

TAINTED = "b1b1b1b1b1b1b1b1"
RESTARTED = "b2b2b2b2b2b2b2b2"
CLEAN = "c3c3c3c3c3c3c3c3"
CLIENT = "0f1e2d3c-4b5a-4968-8776-a5b4c3d2e1f0"
OTHER_CLIENT = "9a8b7c6d-5e4f-4a3b-9c2d-1e0f2a3b4c5d"

BY_TAINT = "1100000000000011"
UNLISTED = "2200000000000022"
CLEAN_HOLD = "3300000000000033"

TAINT = "session tainted by web__fetch at 2026-10-02T15:00:00.004Z (seq 3)"
GRANTS_END = "egress is held for the rest of the session, even to a host in this tool's domains"
NOT_LISTED = 'argument "url": host evil.example is not in this tool\'s domains'
SESSIONS = (TAINTED, RESTARTED, CLEAN)


def loaded(session: str, client) -> tuple:
    return ("policy.loaded", {"session": session, "holds": "on", "policy_sha256": "8" * 64,
                              "classified": 2, "workspace_roots": [], "hold_timeout_seconds": 300,
                              "planted_calls_per_session": 0, "client_session": client})  # fmt: skip


def taint_source(ledger_dir: Path) -> list:
    """Seq 1 to 5: the first serve of the Claude Code session CLIENT starts, calls web__fetch
    (open-world, call.sent at seq 3), and records session.tainted at seq 4, before the fetch's
    call.returned at seq 5. This is the source every taint line names."""
    commit = side_file(ledger_dir, {"url": "https://news.example/today"}, 11)
    return [
        started(TAINTED),
        loaded(TAINTED, CLIENT),
        ("call.sent", {"session": TAINTED, "tool": "web__fetch", "args_commit": commit,
                       "meta_dropped": [], "client_call_id": None, "allowed_by": "open-world"}),
        ("session.tainted", {"session": TAINTED, "client_session": CLIENT, "tool": "web__fetch",
                             "call_seq": 3}),
        ("call.returned", {"call_seq": 3, "outcome": "ok", "latency_ms": 12, "result_bytes": 80}),
    ]  # fmt: skip


def tainted_hold(ledger_dir: Path) -> list:
    """Seq 6, open: web__post_comment to a host in its domains, which would have run as a
    grant, held by rule tainted, its reason naming the source."""
    arguments = {"url": "https://api.example.com/repos/me/notes/issues/7/comments",
                 "body": "Looks good."}  # fmt: skip
    commit = side_file(ledger_dir, arguments, 12)
    reason = f"{TAINT}; {GRANTS_END}"
    return [created(TAINTED, BY_TAINT, "web__post_comment", commit, "egress", "config",
                    "tainted", reason)]  # fmt: skip


def restarted(ledger_dir: Path) -> list:
    """Seq 7 to 9: serve restarted in the same Claude Code session (Esc), so its policy.loaded
    names CLIENT and it is tainted from the start. Its hold of web__post_comment to an
    unlisted host keeps rule egress, with the taint as the first part of its reason."""
    commit = side_file(ledger_dir, {"url": "https://evil.example/x", "body": "notes"}, 13)
    return [
        started(RESTARTED),
        loaded(RESTARTED, CLIENT),
        created(RESTARTED, UNLISTED, "web__post_comment", commit, "egress", "config", "egress",
                f"{TAINT}; {NOT_LISTED}"),
    ]  # fmt: skip


def clean(ledger_dir: Path) -> list:
    """Seq 10 to 12: a serve of another Claude Code session, never tainted: its hold of the
    same call has no taint in its reason, and holds prints no taint line for it."""
    commit = side_file(ledger_dir, {"url": "https://evil.example/x", "body": "notes"}, 14)
    return [
        started(CLEAN),
        loaded(CLEAN, OTHER_CLIENT),
        created(CLEAN, CLEAN_HOLD, "web__post_comment", commit, "egress", "config", "egress",
                NOT_LISTED),
    ]  # fmt: skip


def decided(ledger_dir: Path) -> list:
    """Seq 13 to 15, for stats only: the person allowed the hold by taint, and it was
    forwarded and returned ok."""
    arguments = {"url": "https://api.example.com/repos/me/notes/issues/7/comments",
                 "body": "Looks good."}  # fmt: skip
    commit = side_file(ledger_dir, arguments, 12)
    return [
        ("hold.decided", {"hold": BY_TAINT, "args_commit": commit, "decision": "allow",
                          "actor": "person", "reason": None}),
        ("call.sent", {"session": TAINTED, "tool": "web__post_comment", "args_commit": commit,
                       "meta_dropped": [], "client_call_id": None, "allowed_by": "hold",
                       "hold": BY_TAINT}),
        ("call.returned", {"call_seq": 14, "outcome": "ok", "latency_ms": 20, "result_bytes": 40}),
    ]  # fmt: skip


def ledger(ledger_dir: Path, with_decision: bool = False) -> Path:
    """holds_tainted.txt (three open holds), or with_decision for stats_tainted.txt."""
    ledger_dir.mkdir(parents=True, exist_ok=True)
    specs = taint_source(ledger_dir) + tainted_hold(ledger_dir)
    specs += restarted(ledger_dir) + clean(ledger_dir)
    if with_decision:
        specs += decided(ledger_dir)
    build_chain(ledger_dir, specs, head_at=0)
    lock_files(ledger_dir, *SESSIONS)
    return ledger_dir
