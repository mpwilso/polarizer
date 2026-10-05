"""The fixture drill ledger behind drill_report.txt and drill_export.json (docs/MEASURE-SPEC.md,
section 9): seven drills built from named pieces, one function per drill, each with a docstring
saying what it holds, so a reviewer can read where each number in the golden files comes from.

Totals, as section 9's example prints them: plain, 3 drills, 17 planted calls with 13 caught and
42 clean with 3 false flags, median 12.4 s; prediction gate, 4 drills, 17 planted with 15 caught
and 40 clean with 2 false flags, median 16.1 s; 116 answered calls, 9 of them seen before; by
shape (planted, caught): changed argument 7, 6; different tool 6, 6; extra effect 8, 6;
misleading summary 7, 5; look-alike 6, 5. One plain answer took over 300 s.

WITH_GUIDED, behind drill_report_guided.txt, is the same ledger with a guided first drill before
the seven and a guided drill chosen with the flag after them: guided, 2 drills, 13 planted calls
with 12 caught and 17 clean with 2 false flags; by shape (planted, caught) changed argument 3, 3;
different tool 3, 3; extra effect 3, 2; misleading summary 2, 2; look-alike 2, 2; median 9.0 s.
"""

from pathlib import Path

from polarizer.canon import ZERO_HASH, make_entry
from polarizer.ledger import head_bytes

CHAIN_ID = "d0d0d0d0d0d0d0d0d0d0d0d0d0d0d0d0"
SEED = "5eed" * 8
PREDICTION = "it emails the team the release notes"
SHAPES = ("changed-argument", "different-tool", "extra-effect", "misleading-summary",
          "look-alike")  # fmt: skip


def _calls(planted, missed, clean, flags, shapes, times, seen=0):
    """[(answer, shape, outcome, elapsed_ms, seen_before)], planted calls first. `shapes` lists
    the planted calls' shapes in order; the first `missed` of them were missed."""
    found = []
    for i in range(planted):
        outcome = "missed" if i < missed else "caught"
        found.append(("planted", shapes[i], outcome))
    for i in range(clean):
        found.append(("clean", None, "false-flag" if i < flags else "right"))
    return [(*call, times[i], 1 if i < seen else 0) for i, call in enumerate(found)]


def _times(*runs):
    """[(count, ms), ...] -> a flat list of times."""
    return [ms for count, ms in runs for _ in range(count)]


def drill_1():
    """2026-10-06, plain, 20 of 20, finished: 6 planted, 5 caught (one changed argument
    missed); 14 clean, 1 false flag; median 12.4 s."""
    shapes = ["changed-argument", "different-tool", "extra-effect", "misleading-summary",
              "look-alike", "changed-argument"]  # fmt: skip
    calls = _calls(6, 1, 14, 1, shapes, _times((9, 9000), (1, 12400), (10, 16000)))
    return "2026-10-06", "plain", 20, "finished", calls, False


def drill_2():
    """2026-10-08, plain, 20 of 20, finished: 6 planted, 4 caught (an extra effect and a
    misleading summary missed); 14 clean, 1 false flag; one clean call allowed after 301 s, so
    over 300 s; median 12.4 s; 3 calls seen in an earlier drill."""
    shapes = ["extra-effect", "misleading-summary", "different-tool", "look-alike",
              "changed-argument", "extra-effect"]  # fmt: skip
    times = _times((9, 9000), (1, 12400), (9, 16000), (1, 301000))
    calls = _calls(6, 2, 14, 1, shapes, times, seen=3)
    return "2026-10-08", "plain", 20, "finished", calls, False


def drill_3():
    """2026-10-10, plain, 19 of 20, stopped: 5 planted, 4 caught (a look-alike missed); 14
    clean, 1 false flag; median 12.4 s; 2 calls seen before."""
    shapes = ["look-alike", "changed-argument", "different-tool", "extra-effect",
              "misleading-summary"]  # fmt: skip
    calls = _calls(5, 1, 14, 1, shapes, _times((9, 9000), (1, 12400), (9, 16000)), seen=2)
    return "2026-10-10", "plain", 20, "stopped", calls, False


def drill_4():
    """2026-10-13, prediction gate, 20 of 20, finished: 6 planted, 5 caught (an extra effect
    missed); 14 clean, 1 false flag; median 16.1 s."""
    shapes = ["extra-effect", "changed-argument", "different-tool", "misleading-summary",
              "look-alike", "extra-effect"]  # fmt: skip
    calls = _calls(6, 1, 14, 1, shapes, _times((9, 14000), (1, 16100), (10, 18000)))
    return "2026-10-13", "prediction-gate", 20, "finished", calls, False


def drill_5():
    """2026-10-15, prediction gate, 10 of 10, finished: 3 planted, all caught; 7 clean, 1 false
    flag; median 16.1 s; 2 calls seen before."""
    shapes = ["changed-argument", "different-tool", "extra-effect"]
    calls = _calls(3, 0, 7, 1, shapes, _times((4, 14000), (1, 16100), (5, 18000)), seen=2)
    return "2026-10-15", "prediction-gate", 10, "finished", calls, False


def drill_6():
    """2026-10-17, prediction gate, 10 of 10, finished: 3 planted, all caught; 7 clean, no
    false flag; median 16.1 s; 1 call seen before."""
    shapes = ["misleading-summary", "changed-argument", "look-alike"]
    calls = _calls(3, 0, 7, 0, shapes, _times((4, 14000), (1, 16100), (5, 18000)), seen=1)
    return "2026-10-17", "prediction-gate", 10, "finished", calls, False


def drill_7():
    """2026-10-20, prediction gate, 17 of 20, stopped: 5 planted, 4 caught (a misleading
    summary missed); 12 clean, no false flag; median 15.0 s; 1 call seen before. Started with
    --keep-predictions, so its predictions' text is in the ledger (never in the export)."""
    shapes = ["misleading-summary", "different-tool", "extra-effect", "look-alike",
              "misleading-summary"]  # fmt: skip
    calls = _calls(5, 1, 12, 0, shapes, _times((8, 13000), (1, 15000), (8, 17000)), seen=1)
    return "2026-10-20", "prediction-gate", 20, "stopped", calls, True


def guided_first():
    """2026-10-05, guided, the person's first drill (condition_from first-drill), 10 of 10,
    finished: 5 planted, all caught; 5 clean, 1 false flag; median 7.0 s."""
    calls = _calls(5, 0, 5, 1, list(SHAPES), _times((5, 7000), (1, 9000), (4, 11000)))
    return "2026-10-05", "guided", 10, "finished", calls, False


def guided_flag():
    """2026-10-24, guided, chosen with --condition guided, 20 of 20, finished: 8 planted, 7
    caught (an extra effect missed); 12 clean, 1 false flag; median 10.0 s."""
    shapes = ["extra-effect", "changed-argument", "different-tool", "misleading-summary",
              "look-alike", "changed-argument", "different-tool", "extra-effect"]  # fmt: skip
    calls = _calls(8, 1, 12, 1, shapes, _times((9, 8000), (1, 10000), (10, 12000)))
    return "2026-10-24", "guided", 20, "finished", calls, False


DRILLS = [drill_1, drill_2, drill_3, drill_4, drill_5, drill_6, drill_7]
WITH_GUIDED = [guided_first, *DRILLS, guided_flag]
FROM = {guided_first: "first-drill", guided_flag: "flag"}


def session_of(i: int) -> str:
    return f"{i + 1:x}" * 16


def specs(drills=DRILLS, version=1):
    """[(ts, kind, data)] for the drills, in order."""
    out = []
    for i, piece in enumerate(drills):
        date, condition, calls, how, answered, keep = piece()
        session, ts = session_of(i), f"{date}T09:30:00.000Z"
        out.append((ts, "drill.started", {
            "session": session, "polarizer_version": "0.1.0", "set": version,
            "set_sha256": "e" * 64, "seed": SEED, "seed_from": "random", "condition": condition,
            "condition_from": FROM.get(piece, "random"), "calls": calls, "excluded": [],
            "keep_predictions": keep,
        }))  # fmt: skip
        for n, (answer, shape, outcome, ms, seen) in enumerate(answered, 1):
            scenario = f"s{100 + n:03d}"
            out.append((ts, "drill.shown", {"session": session, "n": n, "scenario": scenario,
                                            "hold": f"{n:016x}", "args_commit": f"{n:064x}",
                                            "seen_before": seen}))  # fmt: skip
            if condition == "prediction-gate":
                out.append((ts, "drill.predicted", {
                    "session": session, "n": n, "length": len(PREDICTION),
                    "prediction": PREDICTION if keep else None, "elapsed_ms": 2500,
                }))  # fmt: skip
            decision = "deny" if outcome in ("caught", "false-flag") else "allow"
            out.append((ts, "drill.decided", {"session": session, "n": n, "scenario": scenario,
                                              "decision": decision, "elapsed_ms": ms,
                                              "actor": "person"}))  # fmt: skip
            out.append((ts, "drill.revealed", {"session": session, "n": n, "scenario": scenario,
                                               "answer": answer, "shape": shape,
                                               "outcome": outcome}))  # fmt: skip
        if how is not None:
            out.append((ts, "drill.ended", {"session": session, "how": how,
                                            "answered": len(answered), "calls": calls}))  # fmt: skip
    return out


def write(ledger_dir: Path, drills=DRILLS, version=1) -> Path:
    """A drill ledger holding the drills, with ledger.head at its genesis, as a drill leaves it."""
    ledger_dir = Path(ledger_dir)
    ledger_dir.mkdir(parents=True, exist_ok=True)
    lines, prev, entries = [], ZERO_HASH, []
    all_specs = [("2026-10-06T09:00:00.000Z", "ledger.genesis", {"chain_id": CHAIN_ID})]
    all_specs += specs(drills, version)
    for seq, (ts, kind, data) in enumerate(all_specs):
        entry, line = make_entry(seq, ts, kind, data, prev)
        entries.append(entry)
        lines.append(line)
        prev = entry["hash"]
    (ledger_dir / "ledger.jsonl").write_bytes(b"".join(lines))
    (ledger_dir / "ledger.head").write_bytes(head_bytes(CHAIN_ID, entries[0]["hash"], 0))
    return ledger_dir
