"""Each call outcome: what the client gets, and exactly one call.sent and one call.returned
per call (docs/PROXY-SPEC.md, Calls)."""

import json

import anyio
import mcp_types as types
import pytest
from helpers import rig
from helpers.fakes import FakeUpstream
from mcp import MCPError

from polarizer.proxy import result_bytes


def test_one_sent_one_returned_per_call(tmp_path):
    fake = FakeUpstream()
    specs = [rig.spec("f", fake.server), rig.spec("p", rig.probe())]
    got = {}

    async def scenario():
        async with rig.proxied(tmp_path / "ledger", specs) as (client, _):
            got["ok"] = await client.call_tool("f__echo", {"a": 1})
            got["tool-error"] = await client.call_tool("f__fail", {})
            with pytest.raises(MCPError) as refused:
                await client.call_tool("f__refuse", {})
            got["protocol-error"] = refused.value
            got["transport-error (upstream -32000)"] = await client.call_tool("f__closed", {})
            got["unsupported"] = await client.call_tool("f__ask", {})
            with anyio.move_on_after(0.5):
                await client.call_tool("f__wait", {"seconds": 10})
            got["transport-error (crash)"] = await client.call_tool("p__crash", {})
            # The shielded call.returned for the cancel is written in the background; wait
            # for the upstream to see its own cancel.
            with anyio.fail_after(5):
                while "wait cancelled" not in fake.events:
                    await anyio.sleep(0.01)

    anyio.run(scenario)

    assert not got["ok"].is_error and got["ok"].content[0].text == '{"a": 1}'
    assert got["tool-error"].is_error and got["tool-error"].content[0].text == "it failed"
    assert (got["protocol-error"].code, got["protocol-error"].message) == (
        -32042,
        "upstream says no",
    )
    assert got["protocol-error"].data is None  # the upstream's data is dropped
    closed = got["transport-error (upstream -32000)"]
    assert closed.is_error
    assert (
        closed.content[0].text
        == "polarizer: upstream f failed: the upstream says connection closed"
    )
    crash = got["transport-error (crash)"]
    assert (
        crash.is_error
        and crash.content[0].text == "polarizer: upstream p failed: Connection closed"
    )
    unsupported = got["unsupported"]
    assert unsupported.is_error
    assert unsupported.content[0].text == (
        "polarizer: upstream f asked the client for elicitation; Polarizer 0.1 does not forward these"
    )

    entries = rig.entries(tmp_path / "ledger")
    sent = [e for e in entries if e["kind"] == "call.sent"]
    returned = [e for e in entries if e["kind"] == "call.returned"]
    assert len(sent) == len(returned) == 7
    by_call = {e["data"]["call_seq"]: e["data"] for e in returned}
    assert sorted(by_call) == [e["seq"] for e in sent]  # one each, none shared
    for e in returned:
        assert e["seq"] > e["data"]["call_seq"]
    outcome = {s["data"]["tool"] + str(i): by_call[s["seq"]] for i, s in enumerate(sent)}
    assert [d["outcome"] for d in outcome.values()] == [
        "ok",
        "tool-error",
        "protocol-error",
        "transport-error",
        "unsupported",
        "cancelled",
        "transport-error",
    ]
    for data in by_call.values():
        assert type(data["latency_ms"]) is int and data["latency_ms"] >= 0
        assert ("error" in data) == (data["outcome"] != "ok")
        assert ("code" in data) == (data["outcome"] == "protocol-error")
        assert set(data) <= {"call_seq", "outcome", "latency_ms", "result_bytes", "error", "code"}
    ok, tool_error, protocol, closed_d, unsupported_d, cancelled, crash_d = outcome.values()

    def own(result):
        """A result Polarizer made, as it handed it to the SDK: before the SDK stamps its own
        serverInfo into _meta on the 2026-07-28 wire."""
        return result.model_copy(update={"meta": None})

    assert ok["result_bytes"] == result_bytes(got["ok"])
    assert tool_error["result_bytes"] == result_bytes(got["tool-error"])
    assert tool_error["error"] == "upstream f returned a tool error"
    assert protocol == {
        "call_seq": protocol["call_seq"],
        "outcome": "protocol-error",
        "latency_ms": protocol["latency_ms"],
        "result_bytes": 0,
        "error": "upstream says no",
        "code": -32042,
    }
    assert closed_d["error"] == closed.content[0].text
    assert closed_d["result_bytes"] == result_bytes(own(closed))
    assert unsupported_d["error"] == unsupported.content[0].text
    assert unsupported_d["result_bytes"] == result_bytes(own(unsupported))
    assert cancelled["result_bytes"] == 0 and cancelled["error"] == "the client cancelled the call"
    assert 400 <= cancelled["latency_ms"] < 2000
    assert crash_d["error"] == crash.content[0].text
    assert crash_d["result_bytes"] == result_bytes(own(crash))
    # Results never enter the ledger.
    raw = (tmp_path / "ledger" / "ledger.jsonl").read_text(encoding="utf-8")
    assert "it failed" not in raw and '{\\"a\\": 1}' not in raw


def test_result_bytes_definition():
    result = types.CallToolResult(content=[types.TextContent(text="caf" + chr(0xE9))])
    # {"content":[{"type":"text","text":"cafe"}],"isError":false,"resultType":"complete"} with
    # e-acute as two UTF-8 bytes; computed by hand from the model's own dump.
    dumped = result.model_dump(by_alias=True, mode="json", exclude_none=True)
    expected = len(json.dumps(dumped, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))
    assert result_bytes(result) == expected
    assert expected == len(json.dumps(dumped, separators=(",", ":"))) - len("\\u00e9") + 2


def test_older_era_upstream_requests(tmp_path):
    """An upstream asking the client for input with a real request is refused before
    Polarizer sees it, and the upstream's own error is recorded as protocol-error, in both
    upstream eras. Neither era's request reaches the client."""
    modern = FakeUpstream()
    legacy = FakeUpstream()
    specs = [rig.spec("m", modern.server), rig.spec("l", legacy.server, mode="legacy")]
    errors = []

    async def scenario():
        async with rig.proxied(tmp_path / "ledger", specs) as (client, gw):
            assert gw.upstreams["l"].protocol_version == "2025-11-25"
            for name in ("m__elicit", "l__elicit"):
                with pytest.raises(MCPError) as e:
                    await client.call_tool(name, {})
                errors.append((e.value.code, e.value.message))

    anyio.run(scenario)
    assert [code for code, _ in errors] == [-32600, -32600]
    assert "elicitation/create" in errors[0][1]
    assert errors[1][1] == "Elicitation not supported"
    returned = [e["data"] for e in rig.kinds(tmp_path / "ledger", "call.returned")]
    assert [(d["outcome"], d["code"], d["result_bytes"]) for d in returned] == [
        ("protocol-error", -32600, 0),
        ("protocol-error", -32600, 0),
    ]


def test_stopped_ledger_refuses_calls(tmp_path, capfd):
    """If the ledger changes under the writer (here, a line that doesn't chain), the writer
    stops, and a call it can't record is refused without reaching the upstream."""
    fake = FakeUpstream()
    ledger_dir = tmp_path / "ledger"

    async def scenario():
        async with rig.proxied(ledger_dir, [rig.spec("f", fake.server)]) as (client, _):
            with open(ledger_dir / "ledger.jsonl", "ab") as f:
                f.write(b'{"not": "a chained entry"}\n')
            return await client.call_tool("f__echo", {"a": 1})

    result = anyio.run(scenario)
    assert result.is_error
    assert (
        result.content[0].text
        == "polarizer: f__echo was not called: the ledger could not record it"
    )
    assert fake.calls == []
    assert "polarizer: stopped writing the ledger:" in capfd.readouterr().err
