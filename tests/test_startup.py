"""Startup: upstreams connect in parallel, each within its own timeout, with no retry
(docs/PROXY-SPEC.md, Startup)."""

import sys
import time

import anyio
from helpers import rig
from mcp import Client, StdioServerParameters


def _inbound(log):
    """The probe's log lines after its own start line: what it read, and its call spans."""
    return [line for line in log.read_text(encoding="utf-8").splitlines() if " start " not in line]


def test_parallel_connect_with_timeout(tmp_path):
    """Three probes and one that never answers (timeout 3 s). The three read nothing until a
    gate file exists, and the test creates it only after all four processes have started, so
    startup can finish only if the upstreams connect in parallel. The hung one times out while
    the others serve. Events, not elapsed time, show both."""
    logs = {p: tmp_path / f"{p}.log" for p in ("a", "b", "c", "hung")}
    gate = tmp_path / "gate"
    specs = [
        rig.spec(p, rig.probe({"PROBE_GATE": str(gate), "PROBE_LOG": str(logs[p])}), timeout=20)
        for p in ("a", "b", "c")
    ]
    specs.append(
        rig.spec(
            "hung", rig.probe({"PROBE_DELAY": "60", "PROBE_LOG": str(logs["hung"])}), timeout=3
        )
    )
    seen = {}

    async def open_gate():
        # A generous deadline: starting four Python processes takes well under a second.
        with anyio.fail_after(30):
            while not all(log.exists() for log in logs.values()):
                await anyio.sleep(0.01)
        seen["inbound before the gate"] = {p: _inbound(log) for p, log in logs.items()}
        gate.touch()

    async def scenario():
        async with anyio.create_task_group() as tasks:
            tasks.start_soon(open_gate)
            start = time.monotonic()
            async with rig.gateway(tmp_path / "ledger", specs) as gw:
                startup = time.monotonic() - start
                async with Client(gw.server) as client:
                    names = [t.name for t in (await client.list_tools()).tools]
                    ok = await client.call_tool("b__wait", {"seconds": 0})
                    hung = await client.call_tool("hung__wait", {"seconds": 0})
        return startup, names, ok, hung

    startup, names, ok, hung = anyio.run(scenario)
    # All four started before any of them had read a message: they were launched together.
    assert seen["inbound before the gate"] == {p: [] for p in logs}
    # A lower bound only: startup waited out the hung upstream's 3 s timeout.
    assert startup >= 2.9, startup
    assert {n.split("__")[0] for n in names} == {"a", "b", "c"}
    assert not ok.is_error and hung.is_error
    # The hung probe never read a message: the others served without it.
    assert _inbound(logs["hung"]) == []
    connected = {
        e["data"]["prefix"]: e["data"] for e in rig.kinds(tmp_path / "ledger", "upstream.connected")
    }
    assert connected["hung"] == {"prefix": "hung", "error": "timeout after 3 s"}
    for prefix in ("a", "b", "c"):
        assert connected[prefix]["protocol_version"] == "2025-11-25"
    # Entries come in config order, after session.started.
    kinds = [(e["kind"], e["data"].get("prefix")) for e in rig.entries(tmp_path / "ledger")][1:6]
    assert kinds == [
        ("session.started", None),
        ("upstream.connected", "a"),
        ("upstream.connected", "b"),
        ("upstream.connected", "c"),
        ("upstream.connected", "hung"),
    ]
    # No retry: each probe process was started exactly once.
    for log in logs.values():
        starts = [line for line in log.read_text().splitlines() if " start " in line]
        assert len(starts) == 1, log.read_text()


def test_failed_upstream_is_recorded(tmp_path):
    specs = [
        rig.spec("gone", StdioServerParameters(command=str(tmp_path / "no-such-command"))),
        rig.spec("exits", StdioServerParameters(command=sys.executable, args=["-c", "pass"])),
    ]

    async def scenario():
        async with rig.gateway(tmp_path / "ledger", specs):
            pass

    anyio.run(scenario)
    connected = {
        e["data"]["prefix"]: e["data"] for e in rig.kinds(tmp_path / "ledger", "upstream.connected")
    }
    for prefix in ("gone", "exits"):
        assert set(connected[prefix]) == {"prefix", "error"}
        assert connected[prefix]["error"] and "\n" not in connected[prefix]["error"]
