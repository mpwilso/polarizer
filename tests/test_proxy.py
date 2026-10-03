"""The proxy's calls: prefixes, routing, exact arguments, the _meta filter, progress,
concurrency, isolation, unknown tools and session.client (docs/PROXY-SPEC.md)."""

import json
import sys
import time
from pathlib import Path

import anyio
import pytest
from helpers import rig
from helpers.fakes import FakeUpstream
from mcp import Client, StdioServerParameters

from polarizer import config
from polarizer.sidefiles import ARGS


def test_prefix_rules(tmp_path):
    # The config accepts exactly the prefixes the spec allows.
    accepted = ["a", "A9", "a-b", "a_b", "-a", "a-", "x" * 32]
    rejected = ["", "a__b", "_a", "a_", "a.b", "a b", "x" * 33, "a___b"]
    for prefix in accepted + rejected:
        path = tmp_path / "polarizer.toml"
        path.write_text(f'[upstream.{json.dumps(prefix)}]\ncommand = "x"\n', encoding="utf-8")
        if prefix in accepted:
            assert config.load(path).upstreams[0].prefix == prefix
        else:
            with pytest.raises(config.ConfigError, match=r"upstream prefix .* must be 1 to 32"):
                config.load(path)

    # Exposed names are <prefix>__<tool>; a name that would break ^[A-Za-z0-9._-]{1,128}$ is
    # left out and recorded under skipped_tools.
    fits = "t" * (128 - len("srv__"))
    too_long = fits + "u"
    fake = FakeUpstream(names=["echo", "a.b-c_d", fits, too_long, "has space", "caf" + chr(0xE9)])

    async def scenario():
        async with rig.proxied(tmp_path / "ledger", [rig.spec("srv", fake.server)]) as (client, _):
            names = [t.name for t in (await client.list_tools()).tools]
            assert names == ["srv__echo", "srv__a.b-c_d", f"srv__{fits}"]
            result = await client.call_tool(f"srv__{fits}", {"k": 1})
            assert not result.is_error
            assert fake.calls[-1][0] == fits

    anyio.run(scenario)
    (connected,) = rig.kinds(tmp_path / "ledger", "upstream.connected")
    assert connected["data"] == {
        "prefix": "srv",
        "protocol_version": "2026-07-28",
        "tools": 3,
        "skipped_tools": [too_long, "has space", "caf" + chr(0xE9)],
    }


def test_double_underscore_tool_name(tmp_path):
    fake = FakeUpstream(names=["get__thing", "a__b__c", "__lead", "trail__"])

    async def scenario():
        async with rig.proxied(tmp_path / "ledger", [rig.spec("up", fake.server)]) as (client, _):
            names = [t.name for t in (await client.list_tools()).tools]
            assert names == ["up__get__thing", "up__a__b__c", "up____lead", "up__trail__"]
            for name in names:
                result = await client.call_tool(name, {"n": name})
                assert not result.is_error, result

    anyio.run(scenario)
    assert [c[0] for c in fake.calls] == ["get__thing", "a__b__c", "__lead", "trail__"]
    sent = rig.kinds(tmp_path / "ledger", "call.sent")
    assert [e["data"]["tool"] for e in sent] == [
        "up__get__thing",
        "up__a__b__c",
        "up____lead",
        "up__trail__",
    ]


def test_exact_arguments(tmp_path):
    arguments = {
        "float": 0.1,
        "big": 2**63,
        "nested": {"list": [1, 2.5, None, True, "x"], "empty": {}},
        "text": "caf" + chr(0xE9) + " " + chr(0x1F600) + " line\nbreak",
        "neg": -0.0,
    }
    fake = FakeUpstream()

    async def scenario():
        async with rig.proxied(tmp_path / "ledger", [rig.spec("f", fake.server)]) as (client, _):
            await client.call_tool("f__echo", arguments)
            await client.call_tool("f__echo", None)

    anyio.run(scenario)
    assert fake.calls[0][1] == arguments
    assert json.dumps(fake.calls[0][1]) == json.dumps(arguments)
    assert fake.calls[1][1] is None
    sent = rig.kinds(tmp_path / "ledger", "call.sent")
    for entry, given in zip(sent, [arguments, None], strict=True):
        blob = (tmp_path / "ledger" / ARGS / f"{entry['data']['args_commit']}.bin").read_bytes()
        stored = json.dumps(given, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        assert blob[32:] == stored


def test_meta_filter(tmp_path):
    meta = {
        "traceparent": "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01",
        "tracestate": "vendor=value",
        "claudecode/toolUseId": "toolu_01ABC",
        "x-secret": "do not forward",
        "io.modelcontextprotocol/custom": "reserved",
        "progressToken": 7,
    }
    fake = FakeUpstream()

    async def scenario():
        async with rig.proxied(tmp_path / "ledger", [rig.spec("f", fake.server)]) as (client, _):
            await client.call_tool("f__echo", {}, meta=meta)
            await client.call_tool("f__echo", {})

    anyio.run(scenario)
    seen = fake.calls[0][2]
    unreserved = {k: v for k, v in seen.items() if not k.startswith("io.modelcontextprotocol/")}
    assert unreserved == {"traceparent": meta["traceparent"], "tracestate": meta["tracestate"]}
    first, second = (e["data"] for e in rig.kinds(tmp_path / "ledger", "call.sent"))
    assert first["meta_dropped"] == ["claudecode/toolUseId", "x-secret"]
    assert first["client_call_id"] == "toolu_01ABC"
    assert first["tool"] == "f__echo"
    assert second["meta_dropped"] == []
    assert second["client_call_id"] is None


def test_meta_filter_over_stdio(tmp_path):
    """Over stdio the SDK shows the progress token as "progress_token"; it is relayed, never
    forwarded or recorded as dropped. The probe sees only its own SDK-made token."""
    log = tmp_path / "probe.log"
    toml = f"[upstream.p]\ncommand = {rig.toml_str(sys.executable)}\nargs = [{rig.toml_str(rig.PROBE)}]\nenv = {{ PROBE_LOG = {rig.toml_str(log)} }}\n"
    cfg = rig.serve_config(tmp_path, toml, tmp_path / "ledger")
    updates = []

    async def progress(p, total, message):
        updates.append((p, total))

    async def scenario():
        async with Client(rig.serve_params(cfg)) as client:
            meta = {
                "traceparent": "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01",
                "x-other": 1,
            }
            await client.call_tool("p__wait", {"seconds": 1}, progress_callback=progress, meta=meta)

    anyio.run(scenario)
    (sent,) = rig.kinds(tmp_path / "ledger", "call.sent")
    assert sent["data"]["meta_dropped"] == ["x-other"]
    calls = [
        json.loads(line.split(" ", 1)[1])
        for line in log.read_text().splitlines()
        if '"tools/call"' in line
    ]
    assert len(calls) == 1
    assert set(calls[0]["params"]["_meta"]) == {"progressToken", "traceparent"}
    assert updates == [(1, 1)]


def test_progress_relay(tmp_path):
    for mode in ("auto", "legacy"):
        fake = FakeUpstream()
        updates = []

        async def progress(p, total, message, updates=updates):
            updates.append((p, total, message))

        async def scenario(mode=mode, fake=fake, updates=updates):
            async with rig.proxied(
                tmp_path / f"ledger-{mode}", [rig.spec("f", fake.server)], mode
            ) as (client, _):
                result = await client.call_tool(
                    "f__wait", {"seconds": 2}, progress_callback=progress
                )
                assert result.content[0].text == "waited 2 s"
                # No progress was asked for: report_progress is a no-op, and the call works.
                await client.call_tool("f__wait", {"seconds": 1})

        anyio.run(scenario)
        assert updates == [(1, 2, "step 1"), (2, 2, "step 2")], mode


def test_progress_relay_stdio_upstream(tmp_path):
    updates = []

    async def progress(p, total, message):
        updates.append((p, total))

    async def scenario():
        async with rig.proxied(tmp_path / "ledger", [rig.spec("p", rig.probe())]) as (client, _):
            await client.call_tool("p__wait", {"seconds": 2}, progress_callback=progress)

    anyio.run(scenario)
    assert updates == [(1, 2), (2, 2)]


@pytest.mark.parametrize("upstream", ["memory", "stdio"])
def test_two_concurrent_calls(tmp_path, upstream):
    target = FakeUpstream().server if upstream == "memory" else rig.probe()
    results = []

    async def scenario():
        async with rig.proxied(tmp_path / "ledger", [rig.spec("u", target)]) as (client, _):

            async def one():
                results.append(await client.call_tool("u__wait", {"seconds": 1}))

            start = time.monotonic()
            async with anyio.create_task_group() as tasks:
                tasks.start_soon(one)
                tasks.start_soon(one)
            return time.monotonic() - start

    elapsed = anyio.run(scenario)
    assert len(results) == 2 and not any(r.is_error for r in results)
    assert 0.95 <= elapsed < 1.8, elapsed
    returned = rig.kinds(tmp_path / "ledger", "call.returned")
    assert len(returned) == 2
    assert all(900 <= e["data"]["latency_ms"] < 1800 for e in returned)


def test_upstream_isolation(tmp_path):
    """A missing command, a hung handshake and a crash mid-call each stay in their own
    upstream; the gateway and the other upstreams keep serving."""
    fake = FakeUpstream()
    specs = [
        rig.spec("good", fake.server),
        rig.spec("missing", StdioServerParameters(command=str(tmp_path / "no-such-command"))),
        rig.spec("hung", rig.probe({"PROBE_DELAY": "30"}), timeout=1),
        rig.spec("crashy", rig.probe()),
    ]

    async def scenario():
        start = time.monotonic()
        async with rig.proxied(tmp_path / "ledger", specs) as (client, _):
            startup = time.monotonic() - start
            names = [t.name for t in (await client.list_tools()).tools]
            assert "good__echo" in names and "crashy__crash" in names
            assert not any(n.startswith(("missing__", "hung__")) for n in names)
            crashed = await client.call_tool("crashy__crash", {})
            again = await client.call_tool("crashy__wait", {"seconds": 0})
            good = await client.call_tool("good__echo", {"still": "here"})
            names_after = [t.name for t in (await client.list_tools()).tools]
            return startup, crashed, again, good, names_after

    startup, crashed, again, good, names_after = anyio.run(scenario)
    assert startup < 5, startup
    assert crashed.is_error and crashed.content[0].text.startswith(
        "polarizer: upstream crashy failed: "
    )
    assert again.is_error and again.content[0].text.startswith(
        "polarizer: upstream crashy failed: "
    )
    assert not good.is_error and json.loads(good.content[0].text) == {"still": "here"}
    assert "good__echo" in names_after
    connected = {
        e["data"]["prefix"]: e["data"] for e in rig.kinds(tmp_path / "ledger", "upstream.connected")
    }
    assert connected["hung"] == {"prefix": "hung", "error": "timeout after 1 s"}
    assert set(connected["missing"]) == {"prefix", "error"}
    assert connected["good"]["tools"] == len(fake.names)
    assert connected["crashy"]["protocol_version"] == "2025-11-25"
    outcomes = [e["data"]["outcome"] for e in rig.kinds(tmp_path / "ledger", "call.returned")]
    assert outcomes == ["transport-error", "transport-error", "ok"]


def test_unknown_tool(tmp_path):
    fake = FakeUpstream()
    specs = [
        rig.spec("f", fake.server),
        rig.spec("dead", StdioServerParameters(command=str(tmp_path / "missing"))),
    ]
    names = ["nodelimiter", "zz__echo", "dead__echo", "f__nope", "f__", "__echo"]

    async def scenario():
        async with rig.proxied(tmp_path / "ledger", specs) as (client, _):
            return [await client.call_tool(name, {"secret": "s"}) for name in names]

    results = anyio.run(scenario)
    for name, result in zip(names, results, strict=True):
        assert result.is_error
        assert [c.text for c in result.content] == [f"polarizer: no tool named {name}"]
    refused = [e["data"] for e in rig.kinds(tmp_path / "ledger", "call.refused")]
    session = rig.kinds(tmp_path / "ledger", "session.started")[0]["data"]["session"]
    assert refused == [
        {"session": session, "tool": "nodelimiter", "reason": "not a prefixed name"},
        {"session": session, "tool": "zz__echo", "reason": 'no upstream with prefix "zz"'},
        {"session": session, "tool": "dead__echo", "reason": "upstream dead did not connect"},
        {"session": session, "tool": "f__nope", "reason": 'upstream f has no tool "nope"'},
        {"session": session, "tool": "f__", "reason": 'upstream f has no tool ""'},
        {"session": session, "tool": "__echo", "reason": 'no upstream with prefix ""'},
    ]
    assert rig.kinds(tmp_path / "ledger", "call.sent") == []
    assert rig.kinds(tmp_path / "ledger", "call.returned") == []
    assert not (tmp_path / "ledger" / ARGS).exists()
    assert fake.calls == []


def test_session_client_once(tmp_path):
    """Eight clients connect at the same moment, each first request carrying client info:
    exactly one session.client is written, and only one session.started."""
    fake = FakeUpstream()

    async def scenario():
        async with rig.gateway(tmp_path / "ledger", [rig.spec("f", fake.server)]) as gw:

            async def one(i):
                async with Client(gw.server, mode="auto" if i % 2 else "legacy") as client:
                    await client.list_tools()

            async with anyio.create_task_group() as tasks:
                for i in range(8):
                    tasks.start_soon(one, i)

    anyio.run(scenario)
    assert len(rig.kinds(tmp_path / "ledger", "session.started")) == 1
    (client,) = rig.kinds(tmp_path / "ledger", "session.client")
    assert client["data"]["client_name"] == "mcp"
    assert client["data"]["protocol_version"] in ("2026-07-28", "2025-11-25")
    # It comes before anything else the session wrote after startup.
    after = [e["kind"] for e in rig.entries(tmp_path / "ledger") if e["seq"] > client["seq"]]
    assert "session.client" not in after


def test_ledger_verifies_after_a_session(tmp_path):
    from polarizer.ledger import verify_bytes

    fake = FakeUpstream()

    async def scenario():
        async with rig.proxied(tmp_path / "ledger", [rig.spec("f", fake.server)]) as (client, _):
            await client.call_tool("f__echo", {"a": 1})
            await client.call_tool("f__fail", {})

    anyio.run(scenario)
    ledger = Path(tmp_path / "ledger")
    result = verify_bytes(
        (ledger / "ledger.jsonl").read_bytes(), (ledger / "ledger.head").read_bytes()
    )
    assert result.status == "intact"
    assert result.output_lines()[0] == "intact: 8 entries, 1 sessions, 2 calls"
