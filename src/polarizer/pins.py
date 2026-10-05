"""Pin state: a fold over the verified ledger, the states a tool can be in, and what
`pending` lists (docs/PIN-SPEC.md, sections 3, 4 and 7).

Nothing that decides exposure lives only in memory: every process builds the same state from
the ledger alone. The fold is fed entries in seq order, from the verification pass and then from
every entry the process appends or adopts. Entries can arrive on the writer thread, so the state
is guarded by a lock.
"""

import hashlib
import threading
from dataclasses import dataclass, field
from pathlib import Path

import rfc8785

from polarizer import defhash
from polarizer.text import printable

CAP = 16  # distinct live hashes recorded per tool between decisions
CAP_PROBLEM = f"more than {CAP} definitions since the last decision"
GROUP_PREFIX = b"POLARIZER-GROUP/1\n"
DECISIONS = ("tool.approved", "tool.rejected")
NOT_IN_GROUP = "not in a group, because this tool has a decision; approve it by name"
SEVERAL = "more than one definition waits for this tool; approve one by name"
BLOCK_KINDS = ("new", "changed", "unservable")

# state -> (the reason after `upstream <p> tool "<t>" ` in call.refused, the client's <why>)
REASONS = {
    "pending": ("is pending approval", "waiting for approval"),
    "rejected": ("was rejected", "rejected"),
    "changed": ("changed after approval", "its definition changed after approval"),
    "unservable": ("cannot be served: {problem}", "it cannot be served"),
}


@dataclass
class ToolPins:
    """What the ledger says about one (upstream, tool)."""

    decision: str | None = None  # "approved" or "rejected"
    decided_hash: str | None = None
    decided_seq: int | None = None
    drifted: bool = False  # a tool.drift follows the latest decision
    observed: dict = field(default_factory=dict)  # hash -> seq of its first record since then
    capped: bool = False  # the cap entry was recorded since the latest decision
    unservable: list = field(default_factory=list)  # (def_hash or None, problem, seq) since then
    seen_ever: set = field(default_factory=set)  # every hash a tool.seen or tool.drift named
    approved_ever: set = field(default_factory=set)

    def copy(self) -> "ToolPins":
        return ToolPins(
            self.decision,
            self.decided_hash,
            self.decided_seq,
            self.drifted,
            dict(self.observed),
            self.capped,
            list(self.unservable),
            set(self.seen_ever),
            set(self.approved_ever),
        )


def _text(data: dict, key: str) -> str | None:
    value = data.get(key)
    return value if isinstance(value, str) else None


class PinState:
    """The fold, keyed by (upstream prefix, upstream tool name)."""

    def __init__(self):
        self._lock = threading.Lock()
        self._tools: dict[tuple[str, str], ToolPins] = {}

    def apply(self, entry: dict) -> None:
        """Fold one verified entry. Kinds and malformed data it doesn't know are ignored."""
        kind = entry.get("kind")
        data = entry.get("data")
        if not isinstance(data, dict) or not isinstance(kind, str) or not kind.startswith("tool."):
            return
        upstream, tool = _text(data, "upstream"), _text(data, "tool")
        if upstream is None or tool is None:
            return
        seq = entry.get("seq")
        with self._lock:
            pins = self._tools.setdefault((upstream, tool), ToolPins())
            if kind in DECISIONS:
                def_hash = _text(data, "def_hash")
                if def_hash is None:
                    return
                approved = kind == "tool.approved"
                pins.decision = "approved" if approved else "rejected"
                pins.decided_hash, pins.decided_seq = def_hash, seq
                pins.drifted, pins.capped = False, False
                pins.observed, pins.unservable = {}, []
                if approved:
                    pins.approved_ever.add(def_hash)
            elif kind == "tool.seen":
                def_hash = _text(data, "def_hash")
                if def_hash is not None:
                    pins.observed.setdefault(def_hash, seq)
                    pins.seen_ever.add(def_hash)
            elif kind == "tool.drift":
                live = _text(data, "live_hash")
                pins.drifted = True
                if live is not None:
                    pins.observed.setdefault(live, seq)
                    pins.seen_ever.add(live)
            elif kind == "tool.unservable":
                problem = _text(data, "problem")
                if problem is None:
                    return
                if problem == CAP_PROBLEM:
                    pins.capped = True
                pins.unservable.append((_text(data, "def_hash"), problem, seq))

    def get(self, upstream: str, tool: str) -> ToolPins:
        """A copy of one tool's pins (empty if the ledger never named it)."""
        with self._lock:
            pins = self._tools.get((upstream, tool))
            return pins.copy() if pins else ToolPins()

    def keys(self) -> list[tuple[str, str]]:
        with self._lock:
            return sorted(self._tools)

    def snapshot(self) -> dict:
        """Every tool's pins, copied: for comparing two folds."""
        with self._lock:
            return {key: pins.copy() for key, pins in self._tools.items()}


def state(pins: ToolPins, live: str) -> tuple[str, str | None]:
    """(state, problem) for a tool whose current listing hashes to `live`, before the stored
    copy check: approved, pending, rejected, changed, or unservable (over the cap)."""
    beyond_cap = pins.capped and live not in pins.observed
    if pins.decision == "approved":
        if live == pins.decided_hash and not pins.drifted:
            return "approved", None
        if beyond_cap and live != pins.decided_hash:
            return "unservable", CAP_PROBLEM
        return "changed", None
    if pins.decision == "rejected" and live == pins.decided_hash:
        return "rejected", None
    if beyond_cap:
        return "unservable", CAP_PROBLEM
    return "pending", None


def what_to_record(pins: ToolPins, live: str) -> str | None:
    """Which observation a successful listing of `live` needs (section 3): "seen", "drift",
    "cap", or None when the decision covers it or it was recorded since."""
    if pins.decision is not None and live == pins.decided_hash:
        return None
    if live in pins.observed:
        return None
    if len(pins.observed) >= CAP:
        return None if pins.capped else "cap"
    return "drift" if pins.decision == "approved" else "seen"


# What `pending` lists ---------------------------------------------------------------------


@dataclass
class Block:
    kind: str  # "new", "changed" or "unservable"
    upstream: str
    tool: str
    def_hash: str | None
    seq: int
    rejected: str | None = None  # new after a rejection: the rejected hash
    approved: str | None = None  # changed: the approved hash
    problem: str | None = None  # unservable: the problem as printed
    copy: dict | None = None  # the stored copy, when it passed its check
    copy_problem: str | None = None  # why the stored copy failed its check
    decided: bool = False  # the tool has a decision, so it is never grouped
    several: bool = False  # no decision, and more than one definition waits: never grouped

    @property
    def groupable(self) -> bool:
        return (
            self.kind == "new" and not self.decided and not self.several and self.copy is not None
        )


def blocks(pins: PinState, ledger_dir: Path, upstream: str | None = None) -> list[Block]:
    """Every block `pending` prints, sorted by prefix, tool name, then the seq of its record."""
    found = []
    for key in pins.keys():
        if upstream is not None and key[0] != upstream:
            continue
        tp = pins.get(*key)
        waiting = [h for h in tp.observed if h != tp.decided_hash]  # the decision covers the rest
        for def_hash, seq in tp.observed.items():
            if def_hash not in waiting:
                continue
            if tp.decision == "approved":
                block = Block("changed", *key, def_hash, seq, approved=tp.decided_hash)
            else:
                block = Block("new", *key, def_hash, seq, rejected=tp.decided_hash)
            block.decided = tp.decision is not None
            block.several = not block.decided and len(waiting) > 1
            try:
                block.copy = defhash.read_copy(ledger_dir, def_hash)
            except defhash.CopyProblem as e:
                block.copy_problem = str(e)
            found.append(block)
        shown = set()
        for def_hash, problem, seq in tp.unservable:
            if problem.startswith("stored copy") and def_hash is not None:
                try:
                    defhash.read_copy(ledger_dir, def_hash)
                    continue  # the copy passes now
                except defhash.CopyProblem as e:
                    problem = f"stored copy {defhash.copy_name(def_hash)} {e}"
            if (def_hash, problem) in shown:
                continue
            shown.add((def_hash, problem))
            found.append(Block("unservable", *key, def_hash, seq, problem=problem))
    found.sort(key=lambda b: (b.upstream, b.tool, b.seq))
    return found


def group(found: list[Block]) -> tuple[list[Block], str | None]:
    """The blocks a group approves, in listing order, and the group id (None if empty)."""
    members = [b for b in found if b.groupable]
    if not members:
        return [], None
    triples = sorted([b.upstream, b.tool, b.def_hash] for b in members)
    return members, hashlib.sha256(GROUP_PREFIX + rfc8785.dumps(triples)).hexdigest()


def render(found: list[Block], class_line=None) -> list[str]:
    """`pending`'s stdout for an intact ledger, line by line. Every name, hash and problem
    taken from the ledger goes through printable(). `class_line(prefix, tool, copy)`, given
    with --config, adds each new or changed block's class line after its header lines, when
    its stored copy passes (docs/HOLD-SPEC.md, section 8)."""
    if not found:
        return ["pending: nothing waits for a decision"]
    new, changed, unservable = (sum(b.kind == k for b in found) for k in BLOCK_KINDS)
    lines = [f"pending: {new} new, {changed} changed, {unservable} unservable"]
    for b in found:
        lines.append("")
        name = printable(f"{b.upstream}__{b.tool}")
        def_hash = printable(b.def_hash or "-")
        if b.kind == "unservable":
            lines.append(f"unservable {name} {def_hash}: {printable(b.problem)}")
            continue
        if b.kind == "changed":
            lines.append(f"changed {name} {def_hash}, approved {printable(b.approved)}")
        elif b.rejected is not None:
            lines.append(f"new {name} {def_hash}, after rejecting {printable(b.rejected)}")
            lines.append(NOT_IN_GROUP)
        else:
            lines.append(f"new {name} {def_hash}")
            if b.several:
                lines.append(SEVERAL)
        if b.copy is not None and class_line is not None:
            lines.append(class_line(b.upstream, b.tool, b.copy))
        if b.copy is not None:
            lines.extend(defhash.render(b.copy).split("\n"))
        else:
            where = printable(f"{defhash.copy_name(b.def_hash)} {b.copy_problem}")
            lines.append(f"stored copy {where}; it cannot be approved")
    members, group_id = group(found)
    if members:
        lines.append("")
        lines.append(
            f"group {group_id} covers the {len(members)} new definitions above "
            "for tools with no decision yet and one definition waiting"
        )
    return lines
