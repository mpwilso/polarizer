"""Startup: upstreams connect in parallel, each within its own timeout, with no retry
(docs/PROXY-SPEC.md, Startup)."""

import sys
import time

import anyio
from helpers import rig
from mcp import Client, StdioServerParameters


def test_parallel_connect_with_timeout(tmp_path):
    """Three probes each take 1.5 s to start and one never answers (timeout 3 s). In
    sequence that would be at least 7.5 s; in parallel it is about 3 s."""
    logs = {p: tmp_path / f"{p}.log" for p in ("a", "b", "c", "hung")}
    specs = [
        rig.spec(p, rig.probe({"PROBE_DELAY": "1.5", "PROBE_LOG": str(logs[p])}), timeout=5)
        for p in ("a", "b", "c")
    ]
    specs.append(
        rig.spec(
            "hung", rig.probe({"PROBE_DELAY": "60", "PROBE_LOG": str(logs["hung"])}), timeout=3
        )
    )

    async def scenario():
        start = time.monotonic()
        async with rig.gateway(tmp_path / "ledger", specs) as gw:
            startup = time.monotonic() - start
            await anyio.sleep(0.5)
            async with Client(gw.server) as client:
                names = [t.name for t in (await client.list_tools()).tools]
                ok = await client.call_tool("b__wait", {"seconds": 0})
                hung = await client.call_tool("hung__wait", {"seconds": 0})
            return startup, names, ok, hung

    startup, names, ok, hung = anyio.run(scenario)
    assert 2.9 <= startup < 5, startup
    assert {n.split("__")[0] for n in names} == {"a", "b", "c"}
    assert not ok.is_error and hung.is_error
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
