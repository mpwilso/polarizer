"""Ledgers with planted and real holds of planting sessions, for stage 10's golden files
(docs/MEASURE-SPEC.md, section 11): allow and deny printing the reveal, and `polarizer stats`'
planted calls section. Built from named pieces, one function per hold, each with a docstring
saying what it shows, as holdledger's and statsledger's pieces are.

Salts are fixed (bytes([n]) * 32), so every plant_commit and reveal is the same on every run,
and every reveal checks unless a piece says otherwise.
"""

from pathlib import Path

from conftest import build_chain
from helpers import holdledger as hl
from helpers import statsledger as sl

from polarizer.holds import plant_commit

PLANTED = "7c2e94d0a1b85f36"
REAL = "5b1d0e2f3a4c6879"
ROOT = "/home/river/code/garden-planner"
WHY = (
    f"The agent's fs__write_file call named {ROOT}/notes.md; this planted call named "
    f"{ROOT}/.git/hooks/post-checkout, where git runs code."
)


def salt(n: int) -> bytes:
    return bytes([n]) * 32


def policy_loaded(session: str, planted: int) -> tuple:
    return ("policy.loaded", {"session": session, "holds": "on", "policy_sha256": "8" * 64,
                              "classified": 14, "workspace_roots": [ROOT],
                              "hold_timeout_seconds": 300,
                              "planted_calls_per_session": planted})  # fmt: skip


def revealed(session, hold, label, n, why=None, derived_from=None) -> tuple:
    return ("hold.revealed", {"session": session, "hold": hold, "label": label,
                              "salt": salt(n).hex(), "why": why,
                              "derived_from": derived_from})  # fmt: skip


def planted_hold(ledger_dir: Path) -> list:
    """Open, in the running session: a planted write to .git/hooks/post-checkout, made from the
    agent's write to notes.md. Its reveal is already in the ledger, so allow and deny print it
    at once; in a live session serve writes it right after the decision."""
    path = f"{ROOT}/.git/hooks/post-checkout"
    commit = hl.side_file(ledger_dir, {"path": path, "content": "# notes\n"}, 61)
    reason = f'argument "path": {path} matches .git/hooks/**'
    created = hl.created(hl.RUNNING, PLANTED, "fs__write_file", commit, "local-write", "config",
                         "write-pattern", reason)  # fmt: skip
    created[1]["plant_commit"] = plant_commit(salt(61), "planted")
    return [created, revealed(hl.RUNNING, PLANTED, "planted", 61, WHY, 4)]


def real_hold(ledger_dir: Path, with_reveal: bool = True) -> list:
    """Open, in the running session: a real write outside every root, with plant_commit as every
    hold of a planting session has, and its reveal as real (unless `with_reveal` is false)."""
    path = "/home/river/notes/todo.md"
    commit = hl.side_file(ledger_dir, {"path": path, "content": "milk"}, 62)
    reason = f'argument "path": {path} is outside every workspace root'
    created = hl.created(hl.RUNNING, REAL, "fs__write_file", commit, "local-write", "config",
                         "outside-roots", reason)  # fmt: skip
    created[1]["plant_commit"] = plant_commit(salt(62), "real")
    return [created, *([revealed(hl.RUNNING, REAL, "real", 62)] if with_reveal else [])]


def decide_ledger(ledger_dir: Path, with_reveal: bool = True) -> Path:
    """allow_planted.txt, deny_planted.txt and allow_real_planting_on.txt: one planting session,
    running, with a planted and a real open hold."""
    ledger_dir.mkdir(parents=True, exist_ok=True)
    specs = [hl.started(hl.RUNNING), policy_loaded(hl.RUNNING, 1)]
    specs += planted_hold(ledger_dir)
    specs += real_hold(ledger_dir, with_reveal)
    build_chain(ledger_dir, specs, head_at=0)
    hl.lock_files(ledger_dir, hl.RUNNING)
    return ledger_dir


# stats_planted.txt ------------------------------------------------------------------------

S1, S2, S3, S4 = "1111111111111111", "2222222222222222", "3333333333333333", "4444444444444444"


def _commit(created_spec: tuple, n: int, label: str) -> tuple:
    at, kind, data = created_spec
    return at, kind, {**data, "plant_commit": plant_commit(salt(n), label)}


def _reveal(session, hold, label, n, at, why=None, bad=False) -> tuple:
    data = {"session": session, "hold": hold, "label": label,
            "salt": (salt(n + 100) if bad else salt(n)).hex(), "why": why,
            "derived_from": None}  # fmt: skip
    return sl.ts(at), "hold.revealed", data


def s1_holds() -> list:
    """S1, planting off: two real holds, both allowed, 10 s and 20 s."""
    return [sl.created(S1, "1000000000000001", "fs__write_file", "outside-roots", sl.OUTSIDE,
                       "10:00:00.000"),
            sl.decided("1000000000000001", "allow", "10:00:10.000"),
            sl.created(S1, "1000000000000002", "fs__write_file", "outside-roots", sl.OUTSIDE,
                       "10:01:00.000"),
            sl.decided("1000000000000002", "allow", "10:01:20.000")]  # fmt: skip


def s2_holds() -> list:
    """S2, planting on (1): a real hold revealed real and denied after 30 s, and a planted hold
    denied, so caught."""
    real = sl.created(S2, "2000000000000001", "fs__move_file", "destructive", sl.DESTRUCTIVE,
                      "11:00:00.000", cls="destructive")  # fmt: skip
    planted = sl.created(S2, "2000000000000002", "fs__write_file", "write-pattern", sl.HOOKS,
                         "11:00:05.000")  # fmt: skip
    return [_commit(real, 1, "real"),
            sl.decided("2000000000000001", "deny", "11:00:30.000"),
            _reveal(S2, "2000000000000001", "real", 1, "11:00:30.010"),
            _commit(planted, 2, "planted"),
            sl.decided("2000000000000002", "deny", "11:00:45.000"),
            _reveal(S2, "2000000000000002", "planted", 2, "11:00:45.010", why="a reason")]  # fmt: skip


def s3_holds() -> list:
    """S3, planting on (1): a planted hold allowed, so missed, and a real hold still open, not
    revealed yet."""
    planted = sl.created(S3, "3000000000000001", "fs__write_file", "write-pattern", sl.HOOKS,
                         "12:00:00.000")  # fmt: skip
    real = sl.created(S3, "3000000000000002", "fs__write_file", "outside-roots", sl.OUTSIDE,
                      "12:01:00.000")  # fmt: skip
    return [_commit(planted, 3, "planted"),
            sl.decided("3000000000000001", "allow", "12:00:09.000"),
            _reveal(S3, "3000000000000001", "planted", 3, "12:00:09.010", why="a reason"),
            _commit(real, 4, "real")]  # fmt: skip


def stats_ledger(ledger_dir: Path, bad_reveal: bool = False) -> Path:
    """stats_planted.txt: four sessions, two with planting on. With `bad_reveal`, S2's planted
    hold's reveal names another salt, so it doesn't check."""
    loaded = [(sl.ts(at), *policy_loaded(s, n)) for s, n, at in
              ((S1, 0, "09:59:59.100"), (S2, 1, "10:59:59.100"), (S3, 1, "11:59:59.100"),
               (S4, 0, "12:29:59.100"))]  # fmt: skip
    entries = [sl.started(S1, "09:59:59.000"), loaded[0], *s1_holds(),
               sl.started(S2, "10:59:59.000"), loaded[1], *s2_holds(),
               sl.started(S3, "11:59:59.000"), loaded[2], *s3_holds(),
               sl.started(S4, "12:29:59.000"), loaded[3]]  # fmt: skip
    if bad_reveal:
        entries = [
            _reveal(S2, "2000000000000002", "planted", 2, "11:00:45.010", "a reason", True)
            if e[1] == "hold.revealed" and e[2]["hold"] == "2000000000000002"
            else e
            for e in entries
        ]
    return sl.build(ledger_dir, entries)
