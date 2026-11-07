"""Session taint: what it is, and which Claude Code session a serve belongs to
(docs/TAINT-SPEC.md, section 7).

A call to an open-world tool taints its session when its answer comes back, and serve records
session.tainted before the client sees it. The fold (holds.HoldState) keeps the first one for
each session key: the client session, Claude Code's CLAUDE_CODE_SESSION_ID read once at start,
or, without one, the serve session itself. So a serve restarted in the same Claude Code session
(Esc ends the old one) is tainted from the start, and a new Claude Code session starts clean.
"""

import re
from dataclasses import dataclass

ENV = "CLAUDE_CODE_SESSION_ID"
CLIENT_SESSION = re.compile(r"[A-Za-z0-9._:-]{1,128}")
NOT_SET = f"polarizer: warning: {ENV} is not set; taint lasts only while this process runs"
NOT_READ = (
    f"polarizer: warning: {ENV} is not a session id Polarizer reads; taint lasts only while "
    "this process runs"
)


@dataclass(frozen=True)
class Taint:
    """The first session.tainted of a session key: the tainting call's exposed tool name, the
    entry's ts, the seq of the call's call.sent, and the entry's own seq (None for a taint this
    process holds only in memory, because it could not be recorded)."""

    tool: str
    ts: str
    call_seq: int
    seq: int | None = None
    session: str | None = None
    client_session: str | None = None

    @property
    def text(self) -> str:
        """`session tainted by <tool> at <ts> (seq <n>)`, as reasons and `holds` show it. The
        tool and ts come from the ledger; callers that print it pass it through printable()."""
        return f"session tainted by {self.tool} at {self.ts} (seq {self.call_seq})"


def client_session(environ) -> tuple[str | None, str | None]:
    """(the client session, or None; the start-up warning when there is none)."""
    value = environ.get(ENV)
    if value is None:
        return None, NOT_SET
    if not CLIENT_SESSION.fullmatch(value):
        return None, NOT_READ
    return value, None


def key(session: str, client: str | None) -> tuple[str, str]:
    """The key a taint is folded under: the client session when there is one, else the serve
    session alone."""
    return ("client", client) if client is not None else ("serve", session)
