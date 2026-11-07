"""`polarizer card`: a local approval page for held calls (docs/CARD-SPEC.md).

A separate process, since Esc in Claude Code ends `serve`. It reads the ledger, side files,
session locks and pins as `polarizer holds` does, serves one page on 127.0.0.1, and records
decisions through decisions.HoldDecider, exactly as `polarizer allow` and `deny` do, with one
field added: a monotonic `elapsed_ms` from the card first showing the hold to the click.

Every request passes section 3's checks before anything is read or written: the Host header,
then the token (the page), the cookie (everything else), the page's nonce (the state and
decisions), and for a decision the Origin and the JSON content type. Secrets are compared with
hmac.compare_digest. The server writes no request log: the first request line carries the
token.

Every line the page shows is built here, from ledger text through printable() and the holds
renderer; the page's script only places it with textContent.

The review view (docs/SCAN-SPEC.md, section 6): GET /review lists the tool definitions waiting
for a decision, as `polarizer pending` does, with the scanner's flags and a diff against the
definition the tool's latest decision names; POST /approve approves one of them, or the group
of unflagged ones, through decisions.Decider, exactly as `polarizer approve` does. The same
checks apply as to /state and /decide.
"""

import difflib
import hmac
import html
import json
import os
import secrets
import signal
import socket
import socketserver
import sys
import threading
import time
from collections import deque
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

from polarizer import defhash, holds, lock, paths, pins, sidefiles, stats
from polarizer.decisions import (
    PLANTED_LINES,
    Decider,
    HoldDecider,
    Refusal,
    fold_reason,
    reveal_lines,
)
from polarizer.ledger import LEDGER, LOCKED_LINE, Locked, read_files, verify_bytes
from polarizer.text import printable
from polarizer.writer import LedgerError

POLL_MS = 1500  # the page asks for the state this often (card.js keeps its own copy)
BACKGROUND_SECONDS = 120
ENDED_SHOWN = 20
NONCES_KEPT = 16
BODY_LIMIT = 16384  # bytes of a decision's body
DISCARD_LIMIT = 1 << 20  # a larger body is read and dropped up to this, then the link closed
PATH_LINES = 32
DECISIONS = ("allow", "deny")
BODY_KEYS = {"hold", "decision", "reason"}

CSP = "default-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
HEADERS = (
    ("Content-Security-Policy", CSP),
    ("X-Content-Type-Options", "nosniff"),
    ("Referrer-Policy", "no-referrer"),
    ("Cache-Control", "no-store"),
    ("X-Frame-Options", "DENY"),
)
ASSETS = {
    "/card.js": ("card.js", "text/javascript; charset=utf-8"),
    "/card.css": ("card.css", "text/css; charset=utf-8"),
}

BACKGROUND_NOTE = (
    "This call has waited over 2 minutes: probably moved to the background by Claude Code; "
    "the agent may have moved on."
)
ENDED_NOTE = "The session that held this call has ended; no process will act on a decision."
UNKNOWN_NOTE = "Polarizer cannot tell whether the session that held this call is running."
NO_CONFIG = "not shown: start the card with --config to see the paths"
NO_PATH_ARGS = "this tool has no path arguments in polarizer.toml, so the card cannot say"
PATHS_NOTE = "Resolved by the card when it first showed this hold."
NO_APPROVAL = "no approval for this tool in this ledger"
NOT_A_DECISION = "polarizer: the request is not a decision"
NOT_AN_APPROVAL = "polarizer: the request is not an approval"
TOO_LARGE = "polarizer: the request is too large"

WRITE_DANGER = (
    "Deny, unless you asked for exactly this: the path matches {}, a place Polarizer always "
    "holds, because a write there can run code or change who has access."
)
READ_DANGER = (
    "Deny, unless you asked for exactly this: the path matches {}, a place Polarizer always "
    "holds, because it can hold secrets."
)
FILES_DANGER = (
    "Deny: the call would touch Polarizer's own ledger or config, which decide and record what "
    "the agent may do."
)
UNCHECKED_DANGER = (
    "Deny, unless you know why: Polarizer could not check the path this call names ({}), so "
    "it cannot tell where the call would write or read."
)
TAINT_DANGER = (
    "Deny, unless you asked for exactly this: the agent read content from outside this machine "
    "earlier in this session, and this call would send data out. Text it read can ask it to send "
    "what it knows."
)
NO_RECOMMENDATION = (
    "No recommendation: the rule that held this call does not mark it as dangerous by itself. "
    "Read the call and decide."
)
FAIL_CLOSED = ("path-missing", "path-not-string", "path-unresolvable")
CLIENT_CANCEL = "the client cancelled the call"
SHUTDOWN = "polarizer shut down while the call was held"


# What a card shows (section 4) --------------------------------------------------------------


def recommend(rule: str, reason: str) -> tuple[str | None, str]:
    """("deny", text) or (None, text), from the hold's rule and reason only (section 4).

    Deny for a built-in pattern (read from the reason as `stats` reads it; a destructive call's
    paths are judged as a write's, an egress call's as a read's), for polarizer-files, for the
    fail-closed path rules, and for an egress call held by taint (docs/TAINT-SPEC.md, section 9).
    Anything else gets no recommendation. Nothing acts on it."""
    if rule in ("write-pattern", "destructive", "read-pattern", "egress"):
        writes = rule in ("write-pattern", "destructive")
        pattern = stats.pattern_of(reason)
        as_rule = "write-pattern" if writes else "read-pattern"
        if pattern is not None and stats.is_builtin(as_rule, pattern):
            return "deny", (WRITE_DANGER if writes else READ_DANGER).format(printable(pattern))
    if rule == "polarizer-files":
        return "deny", FILES_DANGER
    if rule == "tainted":
        return "deny", TAINT_DANGER
    if rule in FAIL_CLOSED:
        return "deny", UNCHECKED_DANGER.format(rule)
    return None, NO_RECOMMENDATION


def tool_line(hold: holds.Hold) -> str:
    tool = printable(hold.tool)
    if hold.cls is None:
        return f"{tool}, no class: every call of it is held"
    if hold.class_from == "annotations":
        return f"{tool}, class {printable(hold.cls)} (from annotations)"
    return f"{tool}, class {printable(hold.cls)}"


def definition_line(pinned: pins.PinState, tool: str) -> str:
    """The pins of the exposed name's tool, split at its first "__" (a prefix never holds
    one)."""
    prefix, sep, name = tool.partition("__")
    if not sep:
        return NO_APPROVAL
    found = pinned.get(prefix, name)
    if found.decision == "approved":
        if found.drifted:
            return (
                f"changed since it was approved at seq {found.decided_seq}: serve hides the "
                "tool now, so an allow will be refused"
            )
        return f"unchanged since it was approved at seq {found.decided_seq}"
    if found.decision == "rejected":
        return f"rejected at seq {found.decided_seq}: an allow will be refused"
    return NO_APPROVAL


def history_line(hold: holds.Hold, earlier: list[holds.Hold]) -> str:
    """`earlier` is every hold of this tool and rule; those before this one are counted."""
    rule = printable(hold.rule)
    before = [h for h in earlier if h.seq < hold.seq]
    if not before:
        return f"none: this is the first hold of this tool by {rule}"
    allowed = sum(1 for h in before if _decided(h, "allow"))
    denied = sum(1 for h in before if _decided(h, "deny"))
    return f"earlier holds of this tool by {rule}: {allowed} allowed, {denied} denied"


def _decided(hold: holds.Hold, decision: str) -> bool:
    ending = hold.ending
    return ending is not None and ending.kind == "hold.decided" and ending.decision == decision


def age_seconds(ts: str, now: datetime) -> int | None:
    """now minus a ledger ts in whole seconds, as `holds` measures a hold's age; None when ts
    doesn't parse."""
    try:
        then = datetime.strptime(ts, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=UTC)
    except (TypeError, ValueError):
        return None
    return max(int((now - then).total_seconds()), 0)


def touched(policy, tool: str, arguments) -> list[str]:
    """What the call would touch: each path of the tool's path_args, resolved now with serve's
    resolver and bound (section 4, Paths)."""
    prefix, sep, name = tool.partition("__")
    rule = policy.rule_for(prefix, name) if sep else None
    if rule is None or not rule.path_args:
        return [NO_PATH_ARGS]
    lines: list[str] = []
    more, resolved_any = 0, False
    for arg in rule.path_args:
        label = f'"{printable(arg)}"'
        if not isinstance(arguments, dict) or arg not in arguments:
            pending = [(None, f"{label}: missing")]
        else:
            value = arguments[arg]
            if isinstance(value, str):
                pending = [(value, None)]
            elif isinstance(value, list) and all(isinstance(v, str) for v in value):
                pending = [(v, None) for v in value]
            else:
                pending = [(None, f"{label}: not a string or a list of strings")]
        for one, line in pending:
            if len(lines) >= PATH_LINES:
                more += 1
                continue
            if one is not None:
                try:
                    where = paths.resolve_bounded(
                        one, policy.resolve_bound, policy.resolver, policy.stuck
                    )
                except paths.Unresolvable as e:
                    line = f"{label}: {printable(one)} cannot be resolved: {e}"
                else:
                    resolved_any = True
                    given = "" if where == one else f" (given as {printable(one)})"
                    line = f"{label}: {printable(where)}{given}"
            lines.append(line)
    if more:
        lines.append(f"and {more} more")
    if resolved_any:
        lines.append(PATHS_NOTE)
    return lines


def ending_text(hold: holds.Hold) -> str:
    """An ended hold's line in the ended list (section 7)."""
    ending = hold.ending
    q = ending.seq
    if ending.kind == "hold.decided":
        if ending.decision == "allow":
            return f"allowed at seq {q}"
        text = f"denied at seq {q}"
        return text + f": {printable(ending.reason)}" if ending.reason else text
    if ending.kind == "hold.expired":
        reason = ending.reason or ""
        if reason == CLIENT_CANCEL:
            return f"cancelled at seq {q}: {CLIENT_CANCEL}"
        if reason == SHUTDOWN:
            return f"ended at seq {q}: {SHUTDOWN}"
        return f"expired at seq {q}: {printable(reason)}"
    return f"abandoned at seq {q}: its session ended before the call was decided"


def _same(given, secret: str) -> bool:
    """A secret check in constant time. Text that isn't ASCII can't be a secret, and
    compare_digest refuses it, so it is turned away first."""
    return isinstance(given, str) and given.isascii() and hmac.compare_digest(given, secret)


def _asset(name: str) -> bytes:
    return resources.files("polarizer").joinpath("card_page", name).read_bytes()


# The card ---------------------------------------------------------------------------------


class Card:
    """What one `polarizer card` process knows: its secrets, the nonces it issued, the last
    read of the ledger, and when it first showed each hold.

    `policy` is the config's policy for showing paths (None with --ledger-dir). `terminal`
    False records terminal false on each decision, as allow --allow-no-terminal does.
    `monotonic`, `now` and `ops` are the clocks and the writer's file layer; tests replace
    them."""

    def __init__(
        self,
        ledger_dir: Path,
        policy=None,
        *,
        terminal: bool = True,
        monotonic=time.monotonic,
        now=None,
        ops=None,
    ):
        self.ledger_dir = Path(ledger_dir)
        self.policy = policy
        self.terminal = terminal
        self._monotonic = monotonic
        self._now = now or (lambda: datetime.now(UTC))
        self._ops = ops
        self.token = secrets.token_hex(32)
        self.cookie = secrets.token_hex(32)
        self.port = 0
        self._nonces: deque[str] = deque(maxlen=NONCES_KEPT)
        self._lock = threading.Lock()  # the read, the shown times, the nonces
        self._deciding = threading.Lock()  # one decision at a time
        self._key = None  # (size, mtime_ns) of the ledger at the last read
        self._read = None  # (status, problem, holds fold, pins fold) of the last read
        self._first_seq: int | None = None  # the ledger's last seq at the first intact read
        self._shown: dict[str, float] = {}  # hold id -> monotonic time first sent to a page
        self._watched: set[str] = set()  # holds seen open at some read
        self._paths: dict[str, list[str]] = {}  # hold id -> its path lines, resolved once
        self._shown_defs: set[tuple[str, str, str]] = set()  # (prefix, tool, hash) on a review
        self._shown_groups: set[str] = set()  # group ids a review answer showed

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/?t={self.token}"

    @property
    def cookie_name(self) -> str:
        return f"polarizer_card_{self.port}"

    # The page and its nonce

    def page(self) -> bytes:
        """index.html with a fresh nonce and the ledger directory filled in."""
        nonce = secrets.token_hex(32)
        with self._lock:
            self._nonces.append(nonce)
        where = html.escape(printable(str(self.ledger_dir)), quote=True)
        text = _asset("index.html").decode("ascii")
        return text.replace("{{NONCE}}", nonce).replace("{{LEDGER}}", where).encode("ascii")

    def nonce_ok(self, given) -> bool:
        with self._lock:
            issued = list(self._nonces)
        ok = False
        for nonce in issued:  # every one is compared, so the time doesn't say which matched
            ok = _same(given, nonce) or ok
        return ok

    # The state (section 5)

    def _fold(self):
        """The ledger as `holds` reads it, again only when its size or mtime changed. A locked
        or unreadable ledger is not remembered, so the next request tries again."""
        path = self.ledger_dir / LEDGER
        try:
            stat = os.stat(path)
            key = (stat.st_size, stat.st_mtime_ns)
        except OSError:
            key = None
        if key is not None and key == self._key and self._read is not None:
            return self._read
        try:
            data, head = read_files(self.ledger_dir)
        except Locked:
            return "locked", [LOCKED_LINE], None, None
        except OSError as e:
            return (
                "unreadable",
                [f"polarizer: cannot read {e.filename or path}: {e.strerror}"],
                None,
                None,
            )
        if not data:
            return "no ledger", [f"no ledger at {self.ledger_dir}"], None, None
        held, pinned = holds.HoldState(), pins.PinState()
        result = verify_bytes(data, head, holds.listener(held.apply, pinned.apply))
        if result.status != "intact":
            read = (result.status, result.output_lines(), None, None)
        else:
            read = ("intact", [], held, pinned)
            if self._first_seq is None:
                self._first_seq = result.state.last_seq
        self._key, self._read = key, read
        return read

    def state(self) -> dict:
        """GET /state's answer. Every open hold it lists counts as shown from now on."""
        with self._lock:
            status, problem, held, pinned = self._fold()
            if status != "intact":
                return {"status": status, "problem": list(problem), "open": [], "ended": []}
            now = self._now()
            shown_at = self._monotonic()
            every = held.snapshot()
            planted = {h for h, hold in every.items() if _planted(hold, held)}
            by_pair: dict[tuple[str, str], list[holds.Hold]] = {}
            for hold in every.values():
                if hold.hold not in planted:  # a revealed planted hold is no one's history
                    by_pair.setdefault((hold.tool, hold.rule), []).append(hold)
            cards = []
            for hold in held.open_holds():
                self._watched.add(hold.hold)
                earlier = by_pair.get((hold.tool, hold.rule), [])
                cards.append(self._card(hold, held, pinned, earlier, now))
                self._shown.setdefault(hold.hold, shown_at)
            first = self._first_seq if self._first_seq is not None else -1
            ended = [
                h
                for h in every.values()
                if h.ending is not None and (h.hold in self._watched or h.seq > first)
            ]
            ended.sort(key=lambda h: h.ending.seq, reverse=True)
            return {
                "status": "intact",
                "problem": [],
                "open": cards,
                "ended": [_ended(h, held, h.hold in planted) for h in ended[:ENDED_SHOWN]],
            }

    def _card(self, hold, held, pinned, earlier, now) -> dict:
        state = holds.session_state(self.ledger_dir, hold.session)
        block = holds.block(hold, held, self.ledger_dir, now, state)
        try:
            arguments, _ = sidefiles.read_args(self.ledger_dir, hold.args_commit)
            problem = None
        except sidefiles.ArgsProblem as e:
            arguments, problem = None, e
        started = held.session_started(hold.session)
        started_text = printable(started) if started is not None else "unknown"
        waiting = f"about {holds.age(hold.ts, now)}; times out after {hold.timeout_seconds} s"
        rows = [
            ["Tool", [tool_line(hold)]],
            ["Why it was held", [f"{printable(hold.rule)}: {printable(hold.reason)}"]],
            ["What it would touch", self._touched(hold, arguments, problem)],
            ["Its definition", [definition_line(pinned, hold.tool)]],
            ["Earlier holds", [history_line(hold, earlier)]],
            ["Waiting", [waiting]],
            [
                "Session",
                [f"{printable(hold.session)} started {started_text}, {holds.STATE_TEXT[state]}"],
            ],
        ]
        found = held.taint_of(hold.session)
        if found is not None:  # docs/TAINT-SPEC.md, section 9
            line = (
                f"tainted by {printable(found.tool)} at {printable(found.ts)} "
                f"(seq {found.call_seq}): every egress call of this session is held"
            )
            rows.append(["Session taint", [line]])
        notes = []
        age = age_seconds(hold.ts, now)
        if age is not None and age >= BACKGROUND_SECONDS:
            notes.append(BACKGROUND_NOTE)
        if problem is not None:
            notes.append(
                f"This hold cannot be allowed: side file args/{printable(hold.args_commit)}.bin "
                f"{printable(str(problem))}."
            )
        if state == lock.ENDED:
            notes.append(ENDED_NOTE)
        elif state == lock.UNKNOWN:
            notes.append(UNKNOWN_NOTE)
        decision, text = recommend(hold.rule, hold.reason)
        return {
            "hold": printable(hold.hold),
            "tool": printable(hold.tool),
            "rows": rows,
            "recommendation": {"decision": decision, "text": text},
            "notes": notes,
            "block": block,
            "can_allow": problem is None,
        }

    def _touched(self, hold, arguments, problem) -> list[str]:
        if self.policy is None:
            return [NO_CONFIG]
        prefix, sep, name = hold.tool.partition("__")
        rule = self.policy.rule_for(prefix, name) if sep else None
        if rule is None or not rule.path_args:
            return [NO_PATH_ARGS]
        if problem is not None:
            return [f"not shown: the side file {printable(problem.problem)}"]
        if hold.hold not in self._paths:
            self._paths[hold.hold] = touched(self.policy, hold.tool, arguments)
        return self._paths[hold.hold]

    # Deciding (section 6)

    def decide(self, raw: bytes) -> tuple[int, dict]:
        """POST /decide's body to (HTTP status, answer)."""
        try:
            body = json.loads(raw)
        except (ValueError, RecursionError):
            return 400, {"ok": False, "error": NOT_A_DECISION}
        if not _is_decision(body):
            return 400, {"ok": False, "error": NOT_A_DECISION}
        hold_id, decision, reason = body["hold"], body["decision"], body.get("reason")
        clicked = self._monotonic()
        with self._lock:
            shown = self._shown.get(hold_id)
        if shown is None:
            line = f"polarizer: this card has not shown hold {hold_id}; reload the page"
            return 409, {"ok": False, "error": line}
        elapsed_ms = max(int((clicked - shown) * 1000), 0)
        folded = (fold_reason(reason) or None) if reason is not None else None
        out: list[str] = []
        err: list[str] = []
        reveal: list[str] = []
        with self._deciding:
            try:
                decider = HoldDecider.open(self.ledger_dir, ops=self._ops)
            except Refusal as e:
                return 409, {"ok": False, "error": e.line}
            except OSError as e:
                where = e.filename or self.ledger_dir
                return 409, {"ok": False, "error": f"polarizer: cannot open {where}: {e.strerror}"}
            decider.terminal = self.terminal
            try:
                decider.decide(
                    hold_id,
                    decision,
                    folded,
                    out.append,
                    err.append,
                    self._now(),
                    elapsed_ms,
                    reveal_out=reveal.append,
                )
            except (Refusal, LedgerError) as e:
                return 409, {"ok": False, "error": e.line}
            except OSError as e:
                line = f"polarizer: could not record the decision: {e.strerror or e}"
                return 409, {"ok": False, "error": line}
            finally:
                decider.close()
        return 200, {"ok": True, "lines": out, "warnings": err, "reveal": reveal[1:]}

    # The review view (docs/SCAN-SPEC.md, section 6)

    def review(self) -> dict:
        """GET /review's answer. Every definition and group id it lists counts as shown."""
        with self._lock:
            status, problem, _, pinned = self._fold()
            if status != "intact":
                return {
                    "status": status,
                    "problem": list(problem),
                    "definitions": [],
                    "group": None,
                }
            found = pins.blocks(pinned, self.ledger_dir)
            members, group_id = pins.group(found)
            shown = []
            for block in found:
                if block.kind == "unservable":
                    continue
                shown.append(self._definition(block, pinned))
                self._shown_defs.add((block.upstream, block.tool, block.def_hash))
            group = None
            if group_id is not None:
                self._shown_groups.add(group_id)
                names = [printable(f"{b.upstream}__{b.tool}") for b in members]
                group = {"id": group_id, "count": len(members), "names": names}
            return {"status": "intact", "problem": [], "definitions": shown, "group": group}

    def _definition(self, block: pins.Block, pinned: pins.PinState) -> dict:
        if block.copy is not None:
            rendered = defhash.render(block.copy).split("\n")
        else:
            where = printable(f"{defhash.copy_name(block.def_hash)} {block.copy_problem}")
            rendered = [f"stored copy {where}; it cannot be approved"]
        return {
            "upstream": printable(block.upstream),
            "tool": printable(block.tool),
            "def_hash": printable(block.def_hash),
            "name": printable(f"{block.upstream}__{block.tool}"),
            "header": pins.header(block),
            "flags": [f.line for f in block.flags],
            "diff": diff_lines(block, self.ledger_dir) if block.copy is not None else [],
            "definition": rendered,
            "in_group": block.groupable,
            "can_approve": block.copy is not None,
        }

    def approve(self, raw: bytes) -> tuple[int, dict]:
        """POST /approve's body to (HTTP status, answer)."""
        try:
            body = json.loads(raw)
        except (ValueError, RecursionError):
            return 400, {"ok": False, "error": NOT_AN_APPROVAL}
        if not _is_approval(body):
            return 400, {"ok": False, "error": NOT_AN_APPROVAL}
        group_id = body.get("group")
        with self._lock:
            if group_id is not None:
                known = group_id in self._shown_groups
                what = f"group {group_id}"
            else:
                key = (body["upstream"], body["tool"], body["def_hash"])
                known = key in self._shown_defs
                what = f"{body['upstream']}__{body['tool']} {body['def_hash']}"
        if not known:
            line = f"polarizer: this card has not shown {printable(what)}; reload the page"
            return 409, {"ok": False, "error": line}
        out: list[str] = []
        err: list[str] = []
        with self._deciding:
            try:
                decider = Decider.open(self.ledger_dir, ops=self._ops)
            except Refusal as e:
                return 409, {"ok": False, "error": e.line}
            except OSError as e:
                where = e.filename or self.ledger_dir
                return 409, {"ok": False, "error": f"polarizer: cannot open {where}: {e.strerror}"}
            decider.terminal = self.terminal
            try:
                if group_id is not None:
                    code = decider.approve_group(group_id, None, out.append, err.append)
                    if code != 0:
                        return 409, {"ok": False, "lines": out, "error": " ".join(err)}
                else:
                    decider.approve_one(
                        body["upstream"], body["tool"], body["def_hash"], out.append
                    )
            except (Refusal, LedgerError) as e:
                return 409, {"ok": False, "error": e.line}
            except OSError as e:
                line = f"polarizer: could not record the decision: {e.strerror or e}"
                return 409, {"ok": False, "error": line}
            finally:
                decider.close()
        return 200, {"ok": True, "lines": out}

    def wait_for_decisions(self, timeout: float) -> None:
        """Let a decision in progress finish before the process exits."""
        if self._deciding.acquire(timeout=timeout):
            self._deciding.release()


def _planted(hold: holds.Hold, held: holds.HoldState) -> bool:
    """A hold whose reveal checks and says planted (stage 10). Until then it is any hold."""
    reveal = held.revealed(hold.hold)
    return reveal is not None and reveal.label == "planted" and holds.reveal_checks(hold, reveal)


def _ended(hold: holds.Hold, held: holds.HoldState, planted: bool) -> dict:
    item = {"hold": printable(hold.hold), "tool": printable(hold.tool), "text": ending_text(hold)}
    if planted:
        reveal = held.revealed(hold.hold)
        ending = hold.ending
        if ending.kind == "hold.decided":
            item["reveal"] = reveal_lines(reveal, ending.decision)[1:]
        else:
            item["reveal"] = [*PLANTED_LINES, f"why: {printable(reveal.why or '')}"]
    return item


def diff_lines(block: pins.Block, ledger_dir: Path) -> list[str]:
    """A unified diff of the definition the tool's latest decision names against the waiting
    one, both rendered as `pending` renders them (docs/SCAN-SPEC.md, section 6). Empty for a
    tool with no decision."""
    if block.kind == "changed":
        label, other = "approved", block.approved
    elif block.rejected is not None:
        label, other = "rejected", block.rejected
    else:
        return []
    try:
        before = defhash.read_copy(ledger_dir, other)
    except defhash.CopyProblem as e:
        return [f"no diff: stored copy {printable(defhash.copy_name(other))} {printable(str(e))}"]
    return list(
        difflib.unified_diff(
            defhash.render(before).split("\n"),
            defhash.render(block.copy).split("\n"),
            f"{label} {printable(other)}",
            f"waiting {printable(block.def_hash)}",
            n=3,
            lineterm="",
        )
    )


def _is_approval(body) -> bool:
    if not isinstance(body, dict):
        return False
    if set(body) == {"group"}:
        return isinstance(body["group"], str) and defhash.is_hash(body["group"])
    if set(body) != {"upstream", "tool", "def_hash"}:
        return False
    return all(isinstance(body[k], str) and 0 < len(body[k]) <= 256 for k in body) and (
        defhash.is_hash(body["def_hash"])
    )


def _is_decision(body) -> bool:
    if not isinstance(body, dict) or not set(body) <= BODY_KEYS:
        return False
    hold_id, decision = body.get("hold"), body.get("decision")
    if not isinstance(hold_id, str) or not holds.HOLD_ID.fullmatch(hold_id):
        return False
    if decision not in DECISIONS:
        return False
    reason = body.get("reason")
    if reason is not None and (not isinstance(reason, str) or decision == "allow"):
        return False
    return True


# The server (section 3) -------------------------------------------------------------------


def _handler(card: Card):
    hosts = (f"127.0.0.1:{card.port}", f"localhost:{card.port}")
    origins = tuple(f"http://{host}" for host in hosts)

    class Handler(BaseHTTPRequestHandler):
        server_version = "polarizer-card"
        sys_version = ""

        def log_message(self, format, *args):
            """No request log: the first request line carries the token."""

        def _send(self, status: int, body: bytes, kind="text/plain; charset=utf-8", extra=()):
            self.send_response(status)
            for key, value in HEADERS:
                self.send_header(key, value)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            for key, value in extra:
                self.send_header(key, value)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _json(self, status: int, answer: dict) -> None:
            body = json.dumps(answer, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
            self._send(status, body.encode("ascii"), "application/json")

        def _forbid(self) -> None:
            self._send(403, b"forbidden")

        def _host_ok(self) -> bool:
            given = self.headers.get_all("Host") or []
            return len(given) == 1 and given[0] in hosts

        def _cookie_ok(self) -> bool:
            for header in self.headers.get_all("Cookie") or []:
                for part in header.split(";"):
                    key, sep, value = part.strip().partition("=")
                    if sep and key == card.cookie_name and _same(value, card.cookie):
                        return True
            return False

        def _nonce_ok(self) -> bool:
            given = self.headers.get_all("X-Polarizer-Nonce") or []
            return len(given) == 1 and card.nonce_ok(given[0])

        def do_GET(self):
            if not self._host_ok():
                return self._forbid()
            parts = urlsplit(self.path)
            if parts.path == "/":
                fields = parse_qsl(parts.query, keep_blank_values=True)
                if len(fields) == 1 and fields[0][0] == "t" and _same(fields[0][1], card.token):
                    cookie = f"{card.cookie_name}={card.cookie}; Path=/; HttpOnly; SameSite=Strict"
                    page = card.page()
                    return self._send(
                        200, page, "text/html; charset=utf-8", [("Set-Cookie", cookie)]
                    )
                return self._forbid()
            if not self._cookie_ok():
                return self._forbid()
            if parts.path in ASSETS:
                name, kind = ASSETS[parts.path]
                return self._send(200, _asset(name), kind)
            if parts.path == "/state":
                if not self._nonce_ok():
                    return self._forbid()
                return self._json(200, card.state())
            if parts.path == "/review":
                if not self._nonce_ok():
                    return self._forbid()
                return self._json(200, card.review())
            if parts.path in ("/decide", "/approve"):
                return self._forbid()  # a decision is a POST
            return self._send(404, b"not found")

        def do_POST(self):
            where = urlsplit(self.path).path
            if not self._host_ok() or where not in ("/decide", "/approve"):
                return self._forbid()
            if not self._cookie_ok() or not self._nonce_ok():
                return self._forbid()
            origin = self.headers.get_all("Origin") or []
            if len(origin) != 1 or origin[0] not in origins:
                return self._forbid()
            kind = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            if kind != "application/json":
                return self._forbid()
            malformed = NOT_A_DECISION if where == "/decide" else NOT_AN_APPROVAL
            try:
                length = int(self.headers.get("Content-Length") or "")
            except ValueError:
                return self._json(400, {"ok": False, "error": malformed})
            if length < 0:
                return self._json(400, {"ok": False, "error": malformed})
            if length > BODY_LIMIT:
                self.rfile.read(min(length, DISCARD_LIMIT))
                self.close_connection = True
                return self._json(413, {"ok": False, "error": TOO_LARGE})
            handle = card.decide if where == "/decide" else card.approve
            status, answer = handle(self.rfile.read(length))
            return self._json(status, answer)

        def _refuse(self):
            self._forbid()

        do_HEAD = do_PUT = do_DELETE = do_PATCH = do_OPTIONS = do_TRACE = do_CONNECT = _refuse

    return Handler


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False  # SO_REUSEADDR on Windows would let another program bind too

    def server_bind(self):
        """Bind without SO_REUSEADDR, and exclusively on Windows; skip HTTPServer's reverse
        lookup of the host name, which can stall."""
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        socketserver.TCPServer.server_bind(self)
        self.server_name, self.server_port = self.server_address[:2]


def make_server(card: Card, port: int = 0) -> _Server:
    """A server for `card` on 127.0.0.1:port (0: a free port the operating system chooses).
    The card learns its port before the handler is built, since every check names it."""
    server = _Server(("127.0.0.1", port), BaseHTTPRequestHandler, bind_and_activate=False)
    try:
        server.server_bind()
        server.server_activate()
    except BaseException:
        server.server_close()
        raise
    card.port = server.server_address[1]
    server.RequestHandlerClass = _handler(card)
    return server


def _interrupt(signum, frame):
    raise KeyboardInterrupt


def run(ledger_dir: Path, policy, port: int, terminal: bool) -> int:
    """`polarizer card` after its usage, terminal and location checks (section 2, Start)."""
    path = Path(ledger_dir) / LEDGER
    if not path.is_file() or path.stat().st_size == 0:
        print(f"polarizer: no ledger at {ledger_dir}", file=sys.stderr)
        return 2
    card = Card(ledger_dir, policy, terminal=terminal)
    try:
        server = make_server(card, port)
    except OSError as e:
        print(f"polarizer: cannot listen on 127.0.0.1:{port}: {e.strerror or e}", file=sys.stderr)
        return 2
    previous = None
    if sys.platform != "win32" and threading.current_thread() is threading.main_thread():
        previous = signal.signal(signal.SIGTERM, _interrupt)
    print(f"open {card.url}", flush=True)
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        card.wait_for_decisions(10.0)
        if previous is not None:
            signal.signal(signal.SIGTERM, previous)
    return 0
