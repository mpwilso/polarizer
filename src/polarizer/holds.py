"""Holds: a fold over the verified ledger, and what `polarizer holds` prints
(docs/HOLD-SPEC.md, sections 5, 7 and 8).

What a hold is, its arguments and how it ended are all in the ledger and the side file; every
process reads them from there. The fold is fed entries in seq order, from the verification pass
and then from every entry the process appends or adopts, so it can run on the writer thread
and is guarded by a lock.

Each hold ends once: the first hold.decided, hold.expired or hold.abandoned for it, in seq
order. A hold.decided whose args_commit differs from the hold's is not an ending; it is ignored
(serve says so on stderr). Anything after the ending changes nothing.
"""

import json
import re
import threading
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path

from polarizer import lock, sidefiles
from polarizer.text import printable

HOLD_ID = re.compile("[0-9a-f]{16}")
SESSION_ID = re.compile("[0-9a-f]{16}")
ENDINGS = ("hold.decided", "hold.expired", "hold.abandoned")
SESSIONS = "sessions"
DECISIONS = ("allow", "deny")

# The session states `holds` prints (section 8).
STATE_TEXT = {
    lock.RUNNING: "running",
    lock.ENDED: "ended; no process will act on a decision",
    lock.UNKNOWN: "state unknown",
}
ENDED_WARNING = (
    "polarizer: warning: the session that held this call has ended; "
    "no process will act on this decision"
)
UNKNOWN_WARNING = (
    "polarizer: warning: cannot tell whether the session that held this call is running"
)


@dataclass(frozen=True)
class Ending:
    kind: str  # hold.decided, hold.expired or hold.abandoned
    seq: int
    decision: str | None = None  # hold.decided: "allow" or "deny"
    reason: str | None = None  # hold.expired's reason, or a deny's reason


@dataclass(frozen=True)
class Hold:
    hold: str
    seq: int
    ts: str
    session: str
    tool: str
    args_commit: str
    cls: str | None
    class_from: str | None
    rule: str
    reason: str
    timeout_seconds: int
    ending: Ending | None = None


def _texts(data: dict, *keys) -> bool:
    return all(isinstance(data.get(k), str) for k in keys)


@dataclass
class HoldState:
    """The fold, keyed by hold id."""

    _holds: dict = field(default_factory=dict)  # id -> Hold
    _sessions: dict = field(default_factory=dict)  # session -> session.started ts
    _mismatched: list = field(default_factory=list)  # (seq, hold id): decisions ignored
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def apply(self, entry: dict) -> None:
        """Fold one verified entry. Kinds and malformed data it doesn't know are ignored."""
        kind, data, seq = entry.get("kind"), entry.get("data"), entry.get("seq")
        if not isinstance(data, dict):
            return
        with self._lock:
            if kind == "session.started":
                if isinstance(data.get("session"), str):
                    self._sessions.setdefault(data["session"], entry.get("ts"))
            elif kind == "hold.created":
                self._created(entry, data)
            elif kind in ENDINGS:
                self._ended(kind, seq, data)

    def _created(self, entry: dict, data: dict) -> None:
        if not _texts(data, "hold", "session", "tool", "args_commit", "rule", "reason"):
            return
        if type(data.get("timeout_seconds")) is not int or data["hold"] in self._holds:
            return
        cls, class_from = data.get("class"), data.get("class_from")
        self._holds[data["hold"]] = Hold(
            hold=data["hold"],
            seq=entry["seq"],
            ts=entry["ts"],
            session=data["session"],
            tool=data["tool"],
            args_commit=data["args_commit"],
            cls=cls if isinstance(cls, str) else None,
            class_from=class_from if isinstance(class_from, str) else None,
            rule=data["rule"],
            reason=data["reason"],
            timeout_seconds=data["timeout_seconds"],
        )

    def _ended(self, kind: str, seq: int, data: dict) -> None:
        held = self._holds.get(data.get("hold"))
        if held is None or held.ending is not None:
            return
        if kind == "hold.decided":
            if data.get("decision") not in DECISIONS:
                return
            if data.get("args_commit") != held.args_commit:
                self._mismatched.append((seq, held.hold))
                return
            reason = data.get("reason")
            reason = reason if isinstance(reason, str) else None
            ending = Ending(kind, seq, data["decision"], reason)
        elif kind == "hold.expired":
            reason = data.get("reason")
            ending = Ending(kind, seq, None, reason if isinstance(reason, str) else None)
        else:
            ending = Ending(kind, seq)
        self._holds[held.hold] = replace(held, ending=ending)

    def get(self, hold_id: str) -> Hold | None:
        with self._lock:
            return self._holds.get(hold_id)

    def open_holds(self) -> list[Hold]:
        """Every hold with no ending, in seq order."""
        with self._lock:
            found = [h for h in self._holds.values() if h.ending is None]
        return sorted(found, key=lambda h: h.seq)

    def session_started(self, session: str) -> str | None:
        with self._lock:
            return self._sessions.get(session)

    def mismatched(self) -> list[tuple[int, str]]:
        """(seq, hold id) of every hold.decided ignored for its args_commit, in seq order."""
        with self._lock:
            return list(self._mismatched)

    def snapshot(self) -> dict:
        with self._lock:
            return dict(self._holds)


def listener(*folds):
    """One on_entry for the writer that feeds every fold in turn."""

    def apply(entry: dict) -> None:
        for fold in folds:
            fold(entry)

    return apply


# Session liveness (section 7) --------------------------------------------------------------


def session_state(ledger_dir: Path, session: str) -> str:
    """lock.RUNNING, lock.ENDED or lock.UNKNOWN for a session, from its lock file. A session
    id that isn't 16 lowercase hex characters (a hand-edited ledger) is never a path: unknown."""
    if not isinstance(session, str) or not SESSION_ID.fullmatch(session):
        return lock.UNKNOWN
    return lock.probe(Path(ledger_dir) / SESSIONS / f"{session}.lock")


def warning(state: str) -> str | None:
    """allow's and deny's stderr warning for the hold's session state, if any."""
    return {lock.ENDED: ENDED_WARNING, lock.UNKNOWN: UNKNOWN_WARNING}.get(state)


# What `holds` prints (section 8) -----------------------------------------------------------


def age(ts: str, now: datetime) -> str:
    """now minus a ledger ts, as <m>m<ss>s under an hour and <h>h<mm>m from then on. "?"
    when ts doesn't parse; a negative age (a clock step) is 0m00s."""
    try:
        then = datetime.strptime(ts, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=UTC)
    except (TypeError, ValueError):
        return "?"
    seconds = max(int((now - then).total_seconds()), 0)
    if seconds < 3600:
        return f"{seconds // 60}m{seconds % 60:02d}s"
    return f"{seconds // 3600}h{seconds % 3600 // 60:02d}m"


def render_arguments(arguments) -> str:
    """The side file's JSON as `holds` prints it: indent 2, keys in the order stored, every
    character outside printable ASCII escaped, DEL included."""
    text = json.dumps(arguments, indent=2, ensure_ascii=True)
    return text.replace(chr(0x7F), chr(0x5C) + "u007f")


def class_text(hold: Hold) -> str:
    if hold.cls is None:
        return "unclassified"
    if hold.class_from == "annotations":
        return f"{hold.cls} (from annotations)"
    return hold.cls


def _head(hold: Hold, started: str | None, state: str, now: datetime) -> list[str]:
    """The block's first four lines, every string from the ledger through printable()."""
    return [
        f"hold {printable(hold.hold)} {printable(hold.tool)} {printable(class_text(hold))}",
        f"held by {printable(hold.rule)}: {printable(hold.reason)}",
        f"waiting about {age(hold.ts, now)}; times out after {hold.timeout_seconds} s",
        f"session {printable(hold.session)} started "
        f"{printable(started) if started is not None else 'unknown'}, {STATE_TEXT[state]}",
    ]


def render_block(
    hold: Hold, started: str | None, state: str, now: datetime, arguments, size: int
) -> list[str]:
    """One hold's block from its arguments and their size in bytes (the side file's size minus
    its salt), with no file read: `holds` calls it after reading and checking the side file,
    and a drill calls it with a scenario's arguments (docs/MEASURE-SPEC.md, section 4)."""
    lines = _head(hold, started, state, now)
    lines.append(f"args_commit {printable(hold.args_commit)}, {size} bytes of arguments")
    lines.extend(render_arguments(arguments).split("\n"))
    return lines


def block(hold: Hold, folded: HoldState, ledger_dir: Path, now: datetime, state: str) -> list[str]:
    """One hold's block, reading and checking its side file."""
    started = folded.session_started(hold.session)
    try:
        arguments, size = sidefiles.read_args(ledger_dir, hold.args_commit)
    except sidefiles.ArgsProblem as e:
        commit = printable(hold.args_commit)
        size_text = f", {e.size} bytes of arguments" if e.size is not None else ""
        return [
            *_head(hold, started, state, now),
            f"args_commit {commit}{size_text}",
            f"arguments: side file args/{commit}.bin {printable(str(e))}; "
            "this hold cannot be allowed",
        ]
    return render_block(hold, started, state, now, arguments, size)


def listing(folded: HoldState, ledger_dir: Path, now: datetime, states=None) -> list[str]:
    """`holds`' stdout for an intact ledger: every open hold, in seq order. `states` maps a
    session to the state already probed for it (holds --wait); others are probed here."""
    open_ = folded.open_holds()
    if not open_:
        return ["holds: nothing is held"]
    lines = [f"holds: {len(open_)} open"]
    for hold in open_:
        state = (states or {}).get(hold.session) or session_state(ledger_dir, hold.session)
        lines.append("")
        lines.extend(block(hold, folded, ledger_dir, now, state))
    return lines
