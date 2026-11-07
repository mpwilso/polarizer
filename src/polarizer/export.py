"""polarizer export --ocsf (docs/EXPORT-SPEC.md, section 3).

One OCSF 1.9.0 API Activity event (class 6003, with the Security Control profile) per ledger
entry of the seven call kinds, in seq order. MAPPING is the spec's table of each kind's data:
every key is mapped to OCSF names, or to none when it is not exported. Free text that can quote
an argument (a hold's or a refusal's reason, an upstream's error, a person's deny reason) is
never exported, and no side file is read: a call's arguments appear only as their commitment,
args_commit.

Fields an entry doesn't hold are joined from earlier ones: a call.returned takes its tool,
session, hold and args_commit from its call.sent, a hold's endings from its hold.created, and
every event of a session its client from that session's session.client.
"""

import calendar
import datetime
import json
import re
from dataclasses import dataclass

from polarizer import __version__

OCSF_VERSION = "1.9.0"
VERSION = __version__  # metadata.product.version; tests fix it for the golden files
KINDS = (
    "call.sent",
    "call.returned",
    "call.refused",
    "hold.created",
    "hold.decided",
    "hold.expired",
    "hold.abandoned",
)
MAPPING = {
    "call.sent": {
        "session": ("actor.session.uid",),
        "tool": ("api.operation", "api.service.name"),
        "args_commit": ("api.request.uid",),
        "meta_dropped": ("unmapped.meta_dropped",),
        "client_call_id": ("unmapped.client_call_id",),
        "hold": ("metadata.correlation_uid",),
        "allowed_by": ("policy.name",),
    },
    "call.returned": {
        "call_seq": ("unmapped.call_seq",),
        "outcome": ("status_id", "status", "status_code"),
        "latency_ms": ("duration",),
        "result_bytes": ("unmapped.result_bytes",),
        "error": (),
        "code": ("api.response.code", "unmapped.code"),
    },
    "call.refused": {
        "session": ("actor.session.uid",),
        "tool": ("api.operation", "api.service.name"),
        "reason": (),
        "hold": ("metadata.correlation_uid",),
        "rule": ("policy.name",),
    },
    "hold.created": {
        "session": ("actor.session.uid",),
        "hold": ("metadata.correlation_uid",),
        "tool": ("api.operation", "api.service.name"),
        "args_commit": ("api.request.uid",),
        "class": ("unmapped.class",),
        "class_from": ("unmapped.class_from",),
        "rule": ("policy.name",),
        "reason": (),
        "timeout_seconds": ("unmapped.timeout_seconds",),
        "plant_commit": ("unmapped.plant_commit",),
    },
    "hold.decided": {
        "hold": ("metadata.correlation_uid",),
        "args_commit": ("api.request.uid",),
        "decision": ("action_id", "action", "disposition_id", "disposition"),
        "actor": ("actor.user.name",),
        "reason": (),
        "terminal": ("unmapped.terminal",),
        "elapsed_ms": ("duration",),
    },
    "hold.expired": {
        "session": ("actor.session.uid",),
        "hold": ("metadata.correlation_uid",),
        "reason": ("status_detail",),
    },
    "hold.abandoned": {
        "session": ("actor.session.uid",),
        "hold": ("metadata.correlation_uid",),
        "held_by": ("unmapped.held_by",),
    },
}

ALLOWED = (1, "Allowed")
DENIED = (2, "Denied")
HELD = (99, "Held")
DISPOSITIONS = {
    "allowed": (1, "Allowed"),
    "approved": (8, "Approved"),
    "blocked": (2, "Blocked"),
    "delayed": (14, "Delayed"),
    "dropped": (6, "Dropped"),
    "rejected": (25, "Rejected"),
}
OUTCOME_STATUS = {"ok": (1, "Success"), "cancelled": (99, "Cancelled")}
FAILURE = (2, "Failure")
EXPIRED_REASONS = re.compile(
    r"timeout after [0-9]+ s|the client cancelled the call|"
    r"polarizer shut down while the call was held"
)
_TS = re.compile(r"([0-9]{4})-([0-9]{2})-([0-9]{2})T([0-9]{2}):([0-9]{2}):([0-9]{2})\.([0-9]{3})Z")


def epoch_ms(ts) -> int:
    """`ts` as milliseconds since the Unix epoch, or 0 when it isn't RFC 3339 with
    milliseconds and Z (the ledger does not check it)."""
    match = _TS.fullmatch(ts) if isinstance(ts, str) else None
    if match is None:
        return 0
    year, month, day, hour, minute, second, ms = (int(g) for g in match.groups())
    if not (1 <= month <= 12 and 1 <= day <= 31 and hour < 24 and minute < 60 and second < 61):
        return 0
    try:
        datetime.date(year, month, day)
    except ValueError:
        return 0
    return calendar.timegm((year, month, day, hour, minute, second)) * 1000 + ms


def _str(value) -> str | None:
    return value if isinstance(value, str) else None


def _int(value) -> int | None:
    return value if type(value) is int else None


def _put(event: dict, path: str, value) -> None:
    """Set a dotted path, unless value is None."""
    if value is None:
        return
    *parents, last = path.split(".")
    for part in parents:
        event = event.setdefault(part, {})
    event[last] = value


@dataclass
class _Call:
    """What later entries join from a call.sent or a hold.created."""

    tool: str | None
    session: str | None
    args_commit: str | None
    hold: str | None
    approved: bool = False  # a call.sent allowed from a hold


class Exporter:
    """Folds verified entries in seq order; event() gives each exported entry's event."""

    def __init__(self):
        self.chain_id = None
        self.clients: dict[str, dict] = {}  # serve session -> actor.application
        self.sent: dict[int, _Call] = {}  # call.sent seq -> the call
        self.holds: dict[str, _Call] = {}  # hold id -> the held call
        self.exported = 0
        self.entries = 0

    def event(self, entry: dict) -> dict | None:
        self.entries += 1
        kind, data = entry["kind"], entry["data"]
        if kind == "ledger.genesis":
            self.chain_id = _str(data.get("chain_id"))
        elif kind == "session.client":
            session, name = _str(data.get("session")), _str(data.get("client_name"))
            if session is not None and name is not None and session not in self.clients:
                app = {"name": name}
                _put(app, "version", _str(data.get("client_version")))
                self.clients[session] = app
        if kind not in KINDS:
            return None
        self.exported += 1
        event = self._base(entry)
        getattr(self, "_" + kind.replace(".", "_"))(event, data, entry)
        return event

    def _base(self, entry: dict) -> dict:
        kind = entry["kind"]
        return {
            "activity_id": 99,
            "activity_name": kind,
            "category_name": "Application Activity",
            "category_uid": 6,
            "class_name": "API Activity",
            "class_uid": 6003,
            "metadata": {
                "event_code": kind,
                "log_version": str(entry["v"]),
                "original_time": entry["ts"],
                "product": {"name": "Polarizer", "version": VERSION},
                "profiles": ["security_control"],
                "sequence": entry["seq"],
                "uid": entry["hash"],
                "version": OCSF_VERSION,
            },
            "severity": "Informational",
            "severity_id": 1,
            "src_endpoint": {"svc_name": "mcp"},
            "time": epoch_ms(entry["ts"]),
            "type_name": "API Activity: Other",
            "type_uid": 600399,
            "unmapped": {"chain_id": self.chain_id, "prev": entry["prev"]},
        }

    # Shared parts ------------------------------------------------------------------------

    def _call(self, event: dict, call: _Call | None, session: bool = True) -> None:
        """api and the correlation of one call, and with `session` its actor.session and
        actor.application."""
        tool = call.tool if call is not None else None
        _put(event, "api.operation", tool if tool is not None else "unknown")
        if tool is not None and "__" in tool:
            _put(event, "api.service.name", tool.split("__", 1)[0])
        if call is None:
            return
        _put(event, "api.request.uid", call.args_commit)
        _put(event, "metadata.correlation_uid", call.hold)
        if session:
            self._session(event, call.session)

    def _session(self, event: dict, session: str | None) -> None:
        _put(event, "actor.session.uid", session)
        if session in self.clients:
            _put(event, "actor.application", dict(self.clients[session]))

    @staticmethod
    def _decision(event: dict, action, disposition: str) -> None:
        event["action_id"], event["action"] = action
        event["disposition_id"], event["disposition"] = DISPOSITIONS[disposition]

    # One method per kind -------------------------------------------------------------------

    def _call_sent(self, event, data, entry):
        hold = _str(data.get("hold"))
        call = _Call(
            _str(data.get("tool")),
            _str(data.get("session")),
            _str(data.get("args_commit")),
            hold,
            approved=hold is not None,
        )
        self.sent[entry["seq"]] = call
        self._call(event, call)
        meta = data.get("meta_dropped")
        if isinstance(meta, list) and all(isinstance(k, str) for k in meta):
            _put(event, "unmapped.meta_dropped", list(meta))
        _put(event, "unmapped.client_call_id", _str(data.get("client_call_id")))
        _put(event, "policy.name", _str(data.get("allowed_by")))
        self._decision(event, ALLOWED, "approved" if call.approved else "allowed")

    def _call_returned(self, event, data, entry):
        call_seq = _int(data.get("call_seq"))
        call = self.sent.get(call_seq) if call_seq is not None else None
        self._call(event, call)
        _put(event, "unmapped.call_seq", call_seq)
        outcome = _str(data.get("outcome"))
        if outcome is not None:
            event["status_id"], event["status"] = OUTCOME_STATUS.get(outcome, FAILURE)
            event["status_code"] = outcome
        _put(event, "duration", _int(data.get("latency_ms")))
        _put(event, "unmapped.result_bytes", _int(data.get("result_bytes")))
        code = data.get("code")
        if _int(code) is not None:
            _put(event, "api.response.code", code)
        else:
            _put(event, "unmapped.code", _str(code))
        approved = call is not None and call.approved
        self._decision(event, ALLOWED, "approved" if approved else "allowed")

    def _call_refused(self, event, data, entry):
        hold = _str(data.get("hold"))
        held = self.holds.get(hold) if hold is not None else None
        tool = _str(data.get("tool"))
        session = _str(data.get("session"))
        commit = held.args_commit if held is not None else None
        self._call(event, _Call(tool, session, commit, hold))
        _put(event, "policy.name", _str(data.get("rule")))
        self._decision(event, DENIED, "blocked")

    def _hold_created(self, event, data, entry):
        hold = _str(data.get("hold"))
        call = _Call(
            _str(data.get("tool")), _str(data.get("session")), _str(data.get("args_commit")), hold
        )
        if hold is not None and hold not in self.holds:
            self.holds[hold] = call
        self._call(event, call)
        _put(event, "unmapped.class", _str(data.get("class")))
        _put(event, "unmapped.class_from", _str(data.get("class_from")))
        _put(event, "policy.name", _str(data.get("rule")))
        _put(event, "unmapped.timeout_seconds", _int(data.get("timeout_seconds")))
        _put(event, "unmapped.plant_commit", _str(data.get("plant_commit")))
        self._decision(event, HELD, "delayed")

    def _ending(self, event, data) -> None:
        """A hold's ending: the held call's tool, commitment and hold id, from its
        hold.created. The actor is the ending's own."""
        hold = _str(data.get("hold"))
        held = self.holds.get(hold) if hold is not None else None
        self._call(event, held if held is not None else _Call(None, None, None, hold), False)

    def _hold_decided(self, event, data, entry):
        self._ending(event, data)
        _put(event, "api.request.uid", _str(data.get("args_commit")))
        _put(event, "actor.user.name", _str(data.get("actor")))
        allow = data.get("decision") == "allow"
        self._decision(event, ALLOWED if allow else DENIED, "approved" if allow else "rejected")
        terminal = data.get("terminal")
        _put(event, "unmapped.terminal", terminal if isinstance(terminal, bool) else None)
        _put(event, "duration", _int(data.get("elapsed_ms")))

    def _hold_expired(self, event, data, entry):
        self._ending(event, data)
        self._session(event, _str(data.get("session")))
        reason = _str(data.get("reason"))
        if reason is not None and EXPIRED_REASONS.fullmatch(reason):
            event["status_detail"] = reason
        self._decision(event, DENIED, "dropped")

    def _hold_abandoned(self, event, data, entry):
        self._ending(event, data)
        self._session(event, _str(data.get("session")))
        _put(event, "unmapped.held_by", _str(data.get("held_by")))
        self._decision(event, DENIED, "dropped")


def line(event: dict) -> str:
    """One event as written: compact JSON, keys sorted, UTF-8."""
    return json.dumps(event, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def summary(exporter: Exporter) -> str:
    left = exporter.entries - exporter.exported
    return (
        f"polarizer: exported {exporter.exported} events from {exporter.entries} entries "
        f"(OCSF {OCSF_VERSION}, API Activity); {left} entries of other kinds left out"
    )
