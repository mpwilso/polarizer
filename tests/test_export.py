"""polarizer export --ocsf (docs/EXPORT-SPEC.md, section 3): golden files for four conformance
fixtures, the spec's mapping table against the code's, every key of every exported kind used
by the fixtures and landing where the table says, no unexported value anywhere, every attribute
name one of OCSF 1.9.0's, the refusals, and export writing nothing. Set
POLARIZER_UPDATE_GOLDEN=1 to rewrite the golden files, then read the diff."""

import json
import os
import re
from pathlib import Path

import pytest
from conftest import ROOT, build_chain, install_fixture, mask_paths

from polarizer import cli, export
from polarizer.ledger import LOCK
from polarizer.lock import LedgerLock

GOLDEN = Path(__file__).parent / "golden"
UPDATE = os.environ.get("POLARIZER_UPDATE_GOLDEN") == "1"
FIXTURES = ["valid/session", "valid/holds", "valid/planted", "valid/taint"]
SPEC = (ROOT / "docs" / "EXPORT-SPEC.md").read_text(encoding="utf-8")

# OCSF 1.9.0 attribute names for API Activity (6003) with the Security Control profile, and
# for the objects the export uses, as schema.ocsf.io listed them on Nov 7, 2026
# (docs/verified-facts.md, Stretch export). unmapped takes any key.
OCSF_NAMES = {
    "": {
        "action", "action_id", "activity_id", "activity_name", "actor", "api", "category_name",
        "category_uid", "class_name", "class_uid", "disposition", "disposition_id", "duration",
        "metadata", "policy", "severity", "severity_id", "src_endpoint", "status", "status_code",
        "status_detail", "status_id", "time", "type_name", "type_uid", "unmapped",
    },
    "metadata": {
        "correlation_uid", "event_code", "log_version", "original_time", "product", "profiles",
        "sequence", "uid", "version",
    },
    "metadata.product": {"name", "version"},
    "actor": {"session", "user", "application"},
    "actor.session": {"uid"},
    "actor.user": {"name"},
    "actor.application": {"name", "version"},
    "api": {"operation", "service", "request", "response"},
    "api.service": {"name"},
    "api.request": {"uid"},
    "api.response": {"code"},
    "policy": {"name"},
    "src_endpoint": {"svc_name"},
}  # fmt: skip


def run_export(capsys, *argv):
    code = cli.main(["export", *argv])
    out, err = capsys.readouterr()
    return code, out, err


def golden(name: str, text: str) -> None:
    path = GOLDEN / name
    if UPDATE:
        path.write_text(text, encoding="utf-8", newline="\n")
    assert text == path.read_text(encoding="utf-8")


def exported(tmp_path, capsys, monkeypatch, fixture):
    monkeypatch.setattr(export, "VERSION", "0.0.0-golden")
    directory = install_fixture(fixture, tmp_path / fixture.replace("/", "_"))
    code, out, err = run_export(capsys, "--ocsf", "--ledger-dir", str(directory))
    assert code == 0, err
    return out, err


def entries_of(fixture):
    path = ROOT / "conformance" / f"{fixture}.jsonl"
    return [json.loads(line) for line in path.read_bytes().splitlines()]


@pytest.mark.parametrize("fixture", FIXTURES)
def test_golden(tmp_path, capsys, monkeypatch, fixture):
    out, err = exported(tmp_path, capsys, monkeypatch, fixture)
    golden(f"export_ocsf_{fixture.split('/')[1]}.jsonl", out)
    entries = entries_of(fixture)
    events = [json.loads(line) for line in out.splitlines()]
    n = sum(e["kind"] in export.KINDS for e in entries)
    assert len(events) == n
    assert err == (
        f"polarizer: exported {n} events from {len(entries)} entries (OCSF 1.9.0, API Activity); "
        f"{len(entries) - n} entries of other kinds left out\n"
    )
    for line in out.splitlines():
        event = json.loads(line)
        assert line == json.dumps(event, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def spec_table() -> dict:
    """The spec's "Each kind's data" table: {(kind, key): set of OCSF names}, empty when the
    key is not exported."""
    section = SPEC.split("### Each kind's data")[1].split("### ")[0]
    rows = {}
    for line in section.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) != 3 or not cells[0].startswith("`"):
            continue
        kind, key = cells[0].strip("`"), cells[1].strip("`")
        names = (
            set()
            if cells[2].startswith("not exported")
            else set(re.findall(r"`([^`]+)`", cells[2]))
        )
        rows[(kind, key)] = names
    return rows


def test_the_spec_table_is_the_code():
    code = {
        (k, key): set(names) for k, keys in export.MAPPING.items() for key, names in keys.items()
    }
    assert spec_table() == code
    assert set(export.MAPPING) == set(export.KINDS)


def test_fixtures_use_every_key():
    seen = {kind: set() for kind in export.KINDS}
    for fixture in FIXTURES:
        for e in entries_of(fixture):
            if e["kind"] in seen:
                seen[e["kind"]].update(e["data"])
    for kind in export.KINDS:
        assert seen[kind] == set(export.MAPPING[kind]), kind


def get(event, path):
    value = event
    for part in path.split("."):
        if not isinstance(value, dict) or part not in value:
            return KeyError
        value = value[part]
    return value


def names_in(event, prefix=""):
    for key, value in event.items():
        path = f"{prefix}.{key}" if prefix else key
        yield prefix, key
        if isinstance(value, dict) and path != "unmapped":
            yield from names_in(value, path)


@pytest.mark.parametrize("fixture", FIXTURES)
def test_every_value_lands_where_the_table_says(tmp_path, capsys, monkeypatch, fixture):
    out, _ = exported(tmp_path, capsys, monkeypatch, fixture)
    events = iter(json.loads(line) for line in out.splitlines())
    for entry in entries_of(fixture):
        if entry["kind"] not in export.KINDS:
            continue
        event = next(events)
        assert event["metadata"]["uid"] == entry["hash"]
        assert event["metadata"]["sequence"] == entry["seq"]
        assert event["metadata"]["event_code"] == entry["kind"] == event["activity_name"]
        assert event["metadata"]["original_time"] == entry["ts"]
        assert event["metadata"]["log_version"] == "1"
        assert event["unmapped"]["prev"] == entry["prev"]
        text = json.dumps(event, ensure_ascii=False)
        for key, value in entry["data"].items():
            names = export.MAPPING[entry["kind"]][key]
            if not names:
                if isinstance(value, str) and value:
                    assert value not in text, (entry["kind"], key)
                continue
            if value is None:
                continue
            landed = [get(event, name) for name in names]
            assert any(v is not KeyError for v in landed), (entry["kind"], key)
            direct = {
                "actor.session.uid", "api.operation", "api.request.uid", "unmapped.meta_dropped",
                "unmapped.client_call_id", "metadata.correlation_uid", "policy.name",
                "unmapped.call_seq", "duration", "unmapped.result_bytes", "api.response.code",
                "unmapped.class", "unmapped.class_from", "unmapped.timeout_seconds",
                "unmapped.plant_commit", "actor.user.name", "unmapped.terminal",
                "status_detail", "unmapped.held_by", "status_code",
            }  # fmt: skip
            for name, got in zip(names, landed, strict=True):
                if name in direct:
                    assert got == value, (entry["kind"], key, name)
        for parent, name in names_in(event):
            assert name in OCSF_NAMES[parent], (parent, name)


def test_values_of_the_fixed_fields(tmp_path, capsys, monkeypatch):
    out, _ = exported(tmp_path, capsys, monkeypatch, "valid/holds")
    events = [json.loads(line) for line in out.splitlines()]
    first = events[0]
    assert (first["class_uid"], first["class_name"]) == (6003, "API Activity")
    assert (first["category_uid"], first["category_name"]) == (6, "Application Activity")
    assert (first["type_uid"], first["type_name"]) == (600399, "API Activity: Other")
    assert (first["activity_id"], first["severity_id"], first["severity"]) == (
        99,
        1,
        "Informational",
    )
    assert first["metadata"]["version"] == "1.9.0"
    assert first["metadata"]["product"] == {"name": "Polarizer", "version": "0.0.0-golden"}
    assert first["metadata"]["profiles"] == ["security_control"]
    assert first["src_endpoint"] == {"svc_name": "mcp"}
    by_kind = {}
    for event in events:
        by_kind.setdefault(event["activity_name"], []).append(event)
    decided = by_kind["hold.decided"]
    assert [(e["action_id"], e["disposition_id"]) for e in decided] == [(1, 8), (2, 25)]
    assert [e["disposition"] for e in decided] == ["Approved", "Rejected"]
    created = by_kind["hold.created"][0]
    assert (created["action_id"], created["action"], created["disposition_id"]) == (99, "Held", 14)
    sent_held, sent_plain = by_kind["call.sent"]
    assert sent_held["disposition_id"] == 8 and sent_plain["disposition_id"] == 1
    # call.returned joins its call.sent: the tool, the service, the session and the commit.
    (returned,) = by_kind["call.returned"]
    assert returned["api"]["operation"] == "fs__move_file"
    assert returned["api"]["service"] == {"name": "fs"}
    assert returned["api"]["request"]["uid"] == sent_held["api"]["request"]["uid"]
    assert returned["metadata"]["correlation_uid"] == sent_held["metadata"]["correlation_uid"]
    assert (returned["status_id"], returned["status"], returned["status_code"]) == (
        1,
        "Success",
        "ok",
    )
    # Endings join their hold.created.
    expired = by_kind["hold.expired"][0]
    assert expired["api"]["operation"] == "web__post"
    assert (expired["action_id"], expired["disposition_id"]) == (2, 6)
    refused = by_kind["call.refused"]
    assert [(e["action_id"], e["disposition_id"]) for e in refused] == [(2, 2)] * len(refused)
    assert (
        refused[0]["api"]["request"]["uid"] == by_kind["hold.created"][1]["api"]["request"]["uid"]
    )
    assert "request" not in refused[-1]["api"]  # too many holds: no side file was written


def test_session_client_names_the_application(tmp_path, capsys, monkeypatch):
    out, _ = exported(tmp_path, capsys, monkeypatch, "valid/session")
    events = [json.loads(line) for line in out.splitlines()]
    assert events[0]["actor"]["application"] == {"name": "claude-code", "version": "2.1.287"}
    statuses = [(e["status_id"], e["status"]) for e in events if "status_id" in e]
    assert statuses == [(1, "Success"), (2, "Failure"), (99, "Cancelled")]
    protocol = [e for e in events if e.get("status_code") == "protocol-error"][0]
    assert protocol["api"]["response"] == {"code": -32042}
    assert "upstream says no" not in json.dumps(events)


def test_odd_values(tmp_path, capsys):
    """A string code goes to unmapped, an unparseable ts makes time 0, an unknown seq in
    call_seq joins nothing, and a name without a prefix has no service."""
    d = tmp_path / "ledger"
    build_chain(
        d,
        [
            ("call.returned", {"call_seq": 99, "outcome": "protocol-error", "latency_ms": 1,
                               "result_bytes": 0, "error": "x", "code": "123456789012345678901"}),
            ("call.refused", {"session": "0" * 16, "tool": "plain", "reason": "no"}),
        ],
    )  # fmt: skip
    lines = (d / "ledger.jsonl").read_bytes().splitlines()
    code, out, _ = run_export(capsys, "--ocsf", "--ledger-dir", str(d))
    assert code == 0
    returned, refused = (json.loads(line) for line in out.splitlines())
    assert returned["unmapped"]["code"] == "123456789012345678901"
    assert "response" not in returned["api"]
    assert returned["api"]["operation"] == "unknown"
    assert "service" not in refused["api"]
    assert returned["time"] == 1790953200001
    assert len(lines) == 3
    assert export.epoch_ms("2026-10-02T15:00:00.001Z") == 1790953200001
    assert export.epoch_ms("2026-13-02T15:00:00.001Z") == 0
    assert export.epoch_ms("yesterday") == 0


def test_refusals(tmp_path, capsys):
    d = tmp_path / "ledger"
    code, out, err = run_export(capsys, "--ledger-dir", str(d))
    assert (code, out, err) == (2, "", "polarizer: export needs --ocsf (the only format so far)\n")
    code, out, err = run_export(capsys, "--ocsf", "--ledger-dir", str(d))
    assert (code, out, err) == (2, "", f"polarizer: no ledger at {d}\n")
    code, out, err = run_export(capsys, "--ocsf")
    assert code == 2 and out == ""
    assert err == (
        "polarizer: export needs exactly one of --config <absolute path> or --ledger-dir "
        "<absolute path>\n"
    )
    broken = install_fixture("broken/edit_value", tmp_path / "broken")
    code, out, err = run_export(capsys, "--ocsf", "--ledger-dir", str(broken))
    assert (code, out) == (1, "")
    assert err == (
        "polarizer: tampered: line 7 (seq 6): hash does not match the entry; export needs an "
        "intact ledger\n"
    )
    v0 = install_fixture("valid/v0-parallax", tmp_path / "v0")
    code, out, err = run_export(capsys, "--ocsf", "--ledger-dir", str(v0))
    assert (code, out) == (3, "")
    assert mask_paths(err, dir=v0) == (
        "polarizer: ledger at <dir> is v0 (Parallax's format); export reads only v1\n"
    )


def test_locked(tmp_path, capsys):
    d = install_fixture("valid/holds", tmp_path / "ledger")
    lock = LedgerLock.create(d / LOCK)
    assert lock.acquire(0)
    try:
        code, out, err = run_export(capsys, "--ocsf", "--ledger-dir", str(d))
    finally:
        lock.close()
    assert (code, out) == (7, "")
    assert err.startswith("polarizer: locked: ledger is locked by another process")


def test_export_writes_nothing(tmp_path, capsys, monkeypatch):
    d = install_fixture("valid/taint", tmp_path / "ledger")
    LedgerLock.create(d / LOCK).close()

    def listing():
        return {
            str(p.relative_to(d)): (p.stat().st_size, p.stat().st_mtime_ns)
            for p in [d, *sorted(d.rglob("*"))]
        }

    before = listing()
    exported(tmp_path, capsys, monkeypatch, "valid/taint")
    code, _, _ = run_export(capsys, "--ocsf", "--ledger-dir", str(d))
    assert code == 0
    assert listing() == before
