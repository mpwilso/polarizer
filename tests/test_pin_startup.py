"""Startup with pins: it fails closed on any ledger that isn't intact, exposes nothing once the
writer stops, and primes a ledger with stdin closed (docs/PIN-SPEC.md, sections 4 and 8)."""

import sys

import anyio
import pytest
from conftest import build_chain, install_fixture
from helpers import rig
from helpers.fakes import FakeUpstream

from polarizer import cli
from polarizer.ledger import verify_bytes

BROKEN = {
    "tampered line": ("broken/edit_value", 1),
    "tampered head": ("broken/head_hash_mismatch", 1),
    "invalid line": ("broken/insert_float", 3),
    "invalid head": ("broken/head_wrong_chain", 3),
    "not canonical": ("broken/reorder_keys", 4),
    "torn tail": ("broken/tear_last_line", 5),
    "truncated": ("broken/truncated", 6),
    "v0": ("valid/v0-parallax", 3),
}


def probe_config(tmp_path, ledger_dir, log):
    toml = (
        f"[upstream.p]\ncommand = {rig.toml_str(sys.executable)}\n"
        f"args = [{rig.toml_str(rig.PROBE)}]\nenv = {{ PROBE_LOG = {rig.toml_str(log)} }}\n"
    )
    return rig.serve_config(tmp_path, toml, ledger_dir)


@pytest.mark.parametrize("case", sorted(BROKEN))
def test_startup_fails_closed_on_each_status(case, tmp_path, capsys, fake_home):
    name, code = BROKEN[case]
    ledger_dir = install_fixture(name, tmp_path / "ledger")
    log = tmp_path / "probe.log"
    cfg = probe_config(tmp_path, ledger_dir, log)
    assert cli.main(["serve", "--config", str(cfg)]) == code
    out, err = capsys.readouterr()
    assert out == "" and err.startswith("polarizer: ") and err.count("\n") == 1
    assert not log.exists()  # no upstream was started


def test_head_ahead_of_file(tmp_path, capsys, fake_home):
    """A ledger.head past the last entry exits 6 and exposes nothing, even when the lost tail
    held the tool.rejected that revoked an approval."""
    ledger_dir = tmp_path / "ledger"
    tool = {"upstream": "p", "tool": "wait", "def_hash": "a" * 64, "actor": "person"}
    entries = build_chain(
        ledger_dir,
        [
            ("tool.seen", {"session": "0" * 16, **{k: tool[k] for k in tool if k != "actor"}}),
            ("tool.approved", {**tool, "group": None}),
            ("tool.rejected", {**tool, "reason": "revoked"}),
        ],
        head_at=3,
    )
    data = (ledger_dir / "ledger.jsonl").read_bytes()
    last = data.rstrip(b"\n").rfind(b"\n") + 1
    (ledger_dir / "ledger.jsonl").write_bytes(data[:last])  # the rejection is lost
    assert entries[3]["kind"] == "tool.rejected"
    log = tmp_path / "probe.log"
    cfg = probe_config(tmp_path, ledger_dir, log)
    assert cli.main(["serve", "--config", str(cfg)]) == 6
    assert capsys.readouterr().err == (
        "polarizer: truncated: ledger ends at seq 2 but ledger.head records seq 3; "
        "run polarizer verify\n"
    )
    assert not log.exists()


def test_writer_stopped_exposes_nothing(tmp_path):
    """A line another process appended that doesn't chain stops the writer: the exposed list
    is empty, clients are told, and calls are refused without reaching the upstream."""
    fake = FakeUpstream(names=["echo", "wait"])
    ledger_dir = tmp_path / "ledger"

    async def scenario():
        async with rig.proxied(ledger_dir, [rig.spec("f", fake.server)]) as (client, _):
            before = [t.name for t in (await client.list_tools()).tools]
            async with client.listen(tools_list_changed=True) as subscription:
                with open(ledger_dir / "ledger.jsonl", "ab") as f:
                    f.write(b'{"not": "a chained entry"}\n')
                after = (await client.list_tools()).tools
                with anyio.fail_after(2):
                    event = await subscription.__anext__()
            result = await client.call_tool("f__echo", {"a": 1})
            return before, after, event, result

    before, after, event, result = anyio.run(scenario)
    assert before == ["f__echo", "f__wait"]
    assert after == []
    assert type(event).__name__ == "ToolsListChanged"
    assert result.is_error and result.content[0].text == (
        "polarizer: f__echo was not called: the ledger could not record it"
    )
    assert fake.calls == []


def test_prime_with_closed_stdin(tmp_path):
    """serve with stdin closed, in front of an upstream slow to connect, connects every
    upstream, records tool.seen and stores a copy for every tool, and exits 0: end of input
    doesn't cancel startup."""
    ledger_dir = tmp_path / "ledger"
    logs = {p: tmp_path / f"{p}.log" for p in ("fast", "slow")}
    toml = ""
    for prefix, delay in (("fast", "0"), ("slow", "2")):
        toml += (
            f"[upstream.{prefix}]\ncommand = {rig.toml_str(sys.executable)}\n"
            f"args = [{rig.toml_str(rig.PROBE)}]\n"
            f'env = {{ PROBE_LOG = {rig.toml_str(logs[prefix])}, PROBE_DELAY = "{delay}" }}\n'
            "connect_timeout_seconds = 15\n\n"
        )
    cfg = rig.serve_config(tmp_path, toml, ledger_dir)
    done = rig.run_serve(cfg)
    assert done.returncode == 0, done.stderr
    assert b"polarizer: 12 tools wait for approval; run polarizer pending" in done.stderr
    connected = {
        e["data"]["prefix"]: e["data"] for e in rig.kinds(ledger_dir, "upstream.connected")
    }
    assert connected["fast"]["tools"] == connected["slow"]["tools"] == 6
    seen = rig.kinds(ledger_dir, "tool.seen")
    assert sorted((e["data"]["upstream"], e["data"]["tool"]) for e in seen) == sorted(
        (p, t)
        for p in ("fast", "slow")
        for t in ("wait", "crash", "env", "fail", "rich", "invalid")
    )
    for entry in seen:
        assert (ledger_dir / "defs" / f"{entry['data']['def_hash']}.json").is_file()
    data = (ledger_dir / "ledger.jsonl").read_bytes()
    assert verify_bytes(data, (ledger_dir / "ledger.head").read_bytes()).status == "intact"
    assert data.endswith(b"\n")
    # The slow probe read its messages only after its delay, and still got listed.
    assert '"tools/list"' in logs["slow"].read_text(encoding="utf-8")
