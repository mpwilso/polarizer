"""Deterministic serve ledgers with holds for `polarizer stats` and its golden files
(docs/MEASURE-SPEC.md, section 10).

stats_mixed.txt's ledger is built from named pieces: one function per hold, each returning the
entries for that hold with a docstring saying what it shows, as holds_mixed.txt's helper does.
Timestamps are given per entry, since time to decision is their difference; the chain is
written with canon.make_entry, as drillledger does. No side files: stats never reads them.

`build` writes any list of (ts, kind, data) as a ledger, for the other fixtures: the approval
rate, the suggestion threshold and the trend windows.
"""

from pathlib import Path

from polarizer.canon import ZERO_HASH, make_entry
from polarizer.ledger import head_bytes

CHAIN_ID = "5a5a5a5a5a5a5a5a5a5a5a5a5a5a5a5a"
A1 = "a1a1a1a1a1a1a1a1"  # started 09:59:59; five holds
B2 = "b2b2b2b2b2b2b2b2"  # started 10:59:59; three holds, one abandoned by c3
C3 = "c3c3c3c3c3c3c3c3"  # started 11:59:59; one open hold
D4 = "d4d4d4d4d4d4d4d4"  # started 12:30:00; no holds
DAY = "2026-10-06T"
OUTSIDE = 'argument "path": /home/me/notes/todo.md is outside every workspace root'
DESTRUCTIVE = "class destructive is held on every call"
EGRESS = "class egress is held on every call"
HOOKS = 'argument "path": /home/me/proj/.git/hooks/pre-commit matches .git/hooks/**'


def ts(clock: str) -> str:
    """`10:00:10.400` on the fixture's day, as a ledger ts."""
    return f"{DAY}{clock}Z"


def started(session: str, at: str):
    return (ts(at), "session.started", {"session": session, "polarizer_version": "0.1.0",
                                        "config_sha256": "7" * 64})  # fmt: skip


def created(session, hold, tool, rule, reason, at, cls="local-write", commit=None):
    commit = commit if commit is not None else hold[-1] * 64
    return (ts(at), "hold.created", {"session": session, "hold": hold, "tool": tool,
                                     "args_commit": commit, "class": cls, "class_from": "config",
                                     "rule": rule, "reason": reason,
                                     "timeout_seconds": 300})  # fmt: skip


def decided(hold, decision, at, reason=None, elapsed_ms=None, commit=None):
    data = {"hold": hold, "args_commit": commit if commit is not None else hold[-1] * 64,
            "decision": decision, "actor": "person", "reason": reason}  # fmt: skip
    if elapsed_ms is not None:
        data["elapsed_ms"] = elapsed_ms
    return (ts(at), "hold.decided", data)


def expired(session, hold, at, reason="timeout after 300 s"):
    return (ts(at), "hold.expired", {"session": session, "hold": hold, "reason": reason})


def abandoned(session, hold, held_by, at):
    return (ts(at), "hold.abandoned", {"session": session, "hold": hold, "held_by": held_by})


def refused(session, hold, tool, reason, at):
    return (ts(at), "call.refused", {"session": session, "tool": tool, "reason": reason,
                                     "hold": hold})  # fmt: skip


# The pieces of stats_mixed.txt ------------------------------------------------------------


def hold_1() -> list:
    """a1: fs__write_file outside-roots, allowed after 10.4 s: 10 s."""
    return [created(A1, "0000000000000001", "fs__write_file", "outside-roots", OUTSIDE,
                    "10:00:00.000"),
            decided("0000000000000001", "allow", "10:00:10.400")]  # fmt: skip


def hold_2() -> list:
    """a1: fs__write_file outside-roots, allowed after 19.6 s: 20 s."""
    return [created(A1, "0000000000000002", "fs__write_file", "outside-roots", OUTSIDE,
                    "10:01:00.000"),
            decided("0000000000000002", "allow", "10:01:19.600")]  # fmt: skip


def hold_3() -> list:
    """a1: fs__move_file destructive, denied after 30 s, with its call.refused."""
    return [created(A1, "0000000000000003", "fs__move_file", "destructive", DESTRUCTIVE,
                    "10:02:00.000", cls="destructive"),
            decided("0000000000000003", "deny", "10:02:30.000", reason="not this one"),
            refused(A1, "0000000000000003", "fs__move_file",
                    "hold 0000000000000003 was denied", "10:02:30.100")]  # fmt: skip


def hold_4() -> list:
    """a1: fs__write_file outside-roots, allowed after 40.5 s: 41, since a half rounds up."""
    return [created(A1, "0000000000000004", "fs__write_file", "outside-roots", OUTSIDE,
                    "10:03:00.000"),
            decided("0000000000000004", "allow", "10:03:40.500")]  # fmt: skip


def hold_5() -> list:
    """a1: web__post_comment egress, expired at the 300 s timeout, with its call.refused."""
    return [created(A1, "0000000000000005", "web__post_comment", "egress", EGRESS,
                    "10:04:00.000", cls="egress"),
            expired(A1, "0000000000000005", "10:09:00.000"),
            refused(A1, "0000000000000005", "web__post_comment",
                    "hold 0000000000000005 expired: timeout after 300 s",
                    "10:09:00.100")]  # fmt: skip


def hold_6() -> list:
    """b2: fs__write_file outside-roots, allowed after 4.9 s: 5 s."""
    return [created(B2, "0000000000000006", "fs__write_file", "outside-roots", OUTSIDE,
                    "11:00:00.900"),
            decided("0000000000000006", "allow", "11:00:05.800")]  # fmt: skip


def hold_7() -> list:
    """b2: fs__move_file destructive, allowed 0.4 s before it was created, as the ledger
    records it when the wall clock steps back: 0 s."""
    return [created(B2, "0000000000000007", "fs__move_file", "destructive", DESTRUCTIVE,
                    "11:01:00.900", cls="destructive"),
            decided("0000000000000007", "allow", "11:01:00.500")]  # fmt: skip


def hold_8() -> list:
    """b2: fs__edit_file write-pattern (.git/hooks/**), left open when b2's process died;
    c3's start writes hold.abandoned for it."""
    return [created(B2, "0000000000000008", "fs__edit_file", "write-pattern", HOOKS,
                    "11:02:00.000")]  # fmt: skip


def hold_8_abandoned() -> list:
    return [abandoned(C3, "0000000000000008", B2, "11:59:59.500")]


def hold_9() -> list:
    """c3: fs__write_file outside-roots, still open."""
    return [created(C3, "0000000000000009", "fs__write_file", "outside-roots", OUTSIDE,
                    "12:00:00.000")]  # fmt: skip


def mixed_specs() -> list:
    """[(ts, kind, data)] for stats_mixed.txt: four sessions, nine holds."""
    specs = [started(A1, "09:59:59.000")]
    for piece in (hold_1, hold_2, hold_3, hold_4, hold_5):
        specs += piece()
    specs += [started(B2, "10:59:59.000")]
    for piece in (hold_6, hold_7, hold_8):
        specs += piece()
    specs += [started(C3, "11:59:59.000")]
    specs += hold_8_abandoned()
    specs += hold_9()
    specs += [started(D4, "12:30:00.000")]
    return specs


def build(ledger_dir: Path, specs: list) -> Path:
    """A serve ledger holding the specs, in order, with ledger.head at its genesis."""
    ledger_dir = Path(ledger_dir)
    ledger_dir.mkdir(parents=True, exist_ok=True)
    lines, prev, entries = [], ZERO_HASH, []
    all_specs = [(ts("09:59:58.000"), "ledger.genesis", {"chain_id": CHAIN_ID}), *specs]
    for seq, (when, kind, data) in enumerate(all_specs):
        entry, line = make_entry(seq, when, kind, data, prev)
        entries.append(entry)
        lines.append(line)
        prev = entry["hash"]
    (ledger_dir / "ledger.jsonl").write_bytes(b"".join(lines))
    (ledger_dir / "ledger.head").write_bytes(head_bytes(CHAIN_ID, entries[0]["hash"], 0))
    return ledger_dir


def mixed(ledger_dir: Path) -> Path:
    """stats_mixed.txt."""
    return build(ledger_dir, mixed_specs())


def nothing(ledger_dir: Path) -> Path:
    """stats_nothing.txt: two sessions and no hold at all."""
    return build(ledger_dir, [started(A1, "09:59:59.000"), started(D4, "12:30:00.000")])


# Generated ledgers for the thresholds -----------------------------------------------------


def run_of(n: int, tool: str, rule: str, reason: str, decisions, seconds, start_seq=1,
           session=A1, cls="local-write"):  # fmt: skip
    """n holds of one tool and rule, created a minute apart from 10:00 and each decided
    `seconds` later; `decisions` and `seconds` are one value or one per hold."""
    specs = []
    for i in range(n):
        hold = f"{start_seq + i:016x}"
        decision = decisions if isinstance(decisions, str) else decisions[i]
        wait = seconds if isinstance(seconds, int) else seconds[i]
        minute = i
        created_at = f"{10 + minute // 60:02d}:{minute % 60:02d}:00.000"
        decided_at = f"{10 + minute // 60:02d}:{minute % 60:02d}:{wait:02d}.000"
        specs.append(created(session, hold, tool, rule, reason, created_at, cls=cls))
        specs.append(decided(hold, decision, decided_at))
    return specs


def allowed_run(ledger_dir: Path, n: int, tool="fs__write_file", rule="outside-roots",
                reason=OUTSIDE, denied=0, cls="local-write") -> Path:  # fmt: skip
    """n decided holds of one tool and rule, the last `denied` of them denied."""
    decisions = ["allow"] * (n - denied) + ["deny"] * denied
    return build(ledger_dir, [started(A1, "09:59:59.000"),
                              *run_of(n, tool, rule, reason, decisions, 10, cls=cls)])  # fmt: skip


def two_windows(ledger_dir: Path, earlier_seconds: int, earlier_allowed: int,
                recent_seconds: int, recent_allowed: int, extra: int = 0) -> Path:  # fmt: skip
    """`extra` then 40 then 40 decided holds, in seq order: the earlier window at
    earlier_seconds each with earlier_allowed of 40 allowed, then the recent window likewise.
    The extra holds, before both windows, take the earlier window's values."""
    specs = [started(A1, "09:59:59.000")]
    n = extra + 40
    decisions = ["allow"] * (n - (40 - earlier_allowed)) + ["deny"] * (40 - earlier_allowed)
    specs += run_of(n, "fs__write_file", "outside-roots", OUTSIDE, decisions, earlier_seconds)
    decisions = ["allow"] * recent_allowed + ["deny"] * (40 - recent_allowed)
    specs += run_of(40, "fs__write_file", "outside-roots", OUTSIDE, decisions, recent_seconds,
                    start_seq=n + 1, session=B2)  # fmt: skip
    specs.insert(1 + 2 * n, started(B2, "10:59:59.000"))
    return build(ledger_dir, specs)
