"""Ledgers for the card's tests (docs/CARD-SPEC.md, section 11, Fixtures).

The state fixture is holds_mixed's ledger (tests/helpers/holdledger.py) with pieces added, one
function per piece, each with a docstring saying what it shows on the card, as holdledger's
pieces are. Timestamps come from conftest.build_chain, and holdledger.NOW is the clock the
tests inject, so every open hold is more than 120 s old and shows the background note.
"""

from pathlib import Path

import mcp_types as types
from conftest import build_chain
from helpers import holdledger as hl
from helpers import scanledger as sl

from polarizer import defhash

EARLIER = "b00000000000000b"  # an earlier allowed hold of fs__write_file by write-pattern
WRITE_HASH = "c" * 64
WEB_APPROVED = "d" * 64
WEB_LIVE = "e" * 64
LONG_HOLD = "c00000000000000c"  # the open hold of long_lines
# A 2,000-character description: sentences, then a 300-character run with no space in it,
# which only overflow-wrap can break.
LONG_DESCRIPTION = ("Read the glossary entry for the word and return it. " * 40)[:1700] + "A" * 300


def approvals() -> list:
    """The pins behind the Its definition row: fs__write_file approved and unchanged, so its
    card reads "unchanged since it was approved at seq 3"; web__post_comment approved, then a
    drift, so its card reads "changed since it was approved"; probe__wait has no decision."""
    return [
        ("tool.approved", {"upstream": "fs", "tool": "write_file", "def_hash": WRITE_HASH,
                           "actor": "person", "group": None}),
        ("tool.approved", {"upstream": "web", "tool": "post_comment",
                           "def_hash": WEB_APPROVED, "actor": "person", "group": None}),
        ("tool.drift", {"session": hl.RUNNING, "upstream": "web", "tool": "post_comment",
                        "approved_hash": WEB_APPROVED, "live_hash": WEB_LIVE}),
    ]  # fmt: skip


def earlier_allowed_hold(ledger_dir: Path) -> list:
    """Ended before the card starts, so not listed: an fs__write_file hold by write-pattern that
    the person allowed. It is the history of the open write-pattern hold: 1 allowed, 0
    denied."""
    path = "/home/me/proj/.git/hooks/post-merge"
    commit = hl.side_file(ledger_dir, {"path": path, "content": "echo hi"}, 11)
    reason = f'argument "path": {path} matches .git/hooks/**'
    return [
        hl.created(hl.RUNNING, EARLIER, "fs__write_file", commit, "local-write", "config",
                   "write-pattern", reason),
        ("hold.decided", {"hold": EARLIER, "args_commit": commit, "decision": "allow",
                          "actor": "person", "reason": None}),
    ]  # fmt: skip


def mixed(ledger_dir: Path) -> Path:
    """card_state_mixed.json: holds_mixed's three open holds (a write-pattern hold with odd
    characters, an unclassified hold, and a class from annotations in an ended session), its
    denied and expired holds, and the pieces above."""
    ledger_dir.mkdir(parents=True, exist_ok=True)
    specs = [hl.started(hl.RUNNING), hl.started(hl.ENDED)]
    specs += approvals()
    specs += earlier_allowed_hold(ledger_dir)
    specs += hl.write_pattern_hold(ledger_dir)
    specs += hl.denied_hold(ledger_dir)
    specs += hl.unclassified_hold(ledger_dir)
    specs += hl.expired_hold(ledger_dir)
    specs += hl.annotations_hold(ledger_dir)
    build_chain(ledger_dir, specs, head_at=0)
    hl.lock_files(ledger_dir, hl.RUNNING, hl.ENDED)
    return ledger_dir


def one_hold(ledger_dir: Path, tool: str, rule: str, reason: str, arguments, hold: str,
             cls="local-write", session=hl.RUNNING, salt=21) -> Path:  # fmt: skip
    """A ledger with one open hold of a running session, for the row and path tests."""
    ledger_dir.mkdir(parents=True, exist_ok=True)
    commit = hl.side_file(ledger_dir, arguments, salt)
    entry = hl.created(session, hold, tool, commit, cls, "config", rule, reason)
    build_chain(ledger_dir, [hl.started(session), entry], head_at=0)
    hl.lock_files(ledger_dir, session)
    return ledger_dir


def long_lines(ledger_dir: Path) -> Path:
    """A card whose boxes hold lines far wider than the page (docs/CARD-SPEC.md, section 9,
    Layout, and deviation 16): one open hold of fs__write_file in the running session, with a
    long path under .git/hooks and a long content argument, so its exact call and its reason
    run past the box; and two definitions waiting for approval: scanledger's poisoned
    lab/lookup, whose description line holds the <IMPORTANT> instruction, and lab/notes, whose
    description is LONG_DESCRIPTION, 2,000 characters."""
    ledger_dir.mkdir(parents=True, exist_ok=True)
    path = "/home/me/proj/.git/hooks/" + "post-checkout-with-a-very-long-name-" * 6 + "end.sh"
    arguments = {"path": path, "content": "echo " + "every word of this hook " * 25}
    commit = hl.side_file(ledger_dir, arguments, 31)
    reason = f'argument "path": {path} matches .git/hooks/**'
    notes = types.Tool(name="notes", description=LONG_DESCRIPTION, input_schema={"type": "object"})
    seen = []
    for tool in (sl.TOOLS["lookup"], notes):
        def_hash, canon = defhash.definition(tool, sl.META_ALLOW)
        defhash.write_copy(ledger_dir, def_hash, canon)
        seen.append(("tool.seen", {"session": hl.RUNNING, "upstream": "lab", "tool": tool.name,
                                   "def_hash": def_hash}))  # fmt: skip
    specs = [
        hl.started(hl.RUNNING),
        hl.created(hl.RUNNING, LONG_HOLD, "fs__write_file", commit, "local-write", "config",
                   "write-pattern", reason),
        *seen,
    ]  # fmt: skip
    build_chain(ledger_dir, specs, head_at=len(specs))
    hl.lock_files(ledger_dir, hl.RUNNING)
    return ledger_dir
