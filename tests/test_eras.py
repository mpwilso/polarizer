"""Protocol eras on both sides of the proxy: 2026-07-28 and 2025-11-25 upstreams, and clients
of both versions (docs/PROXY-SPEC.md; docs/verified-facts.md, spike rounds 2 and 3)."""

import json
import sys
import time

import anyio
import mcp_types as types
import pytest
from helpers import rig
from helpers.fakes import FakeUpstream
from mcp import Client


async def _next_event(subscription, seconds=2):
    with anyio.fail_after(seconds):
        return await subscription.__anext__()


async def _exercise_modern(client, wait_for_cancel):
    """List change through listen, a fresh list despite the TTL hint, progress, cancel."""
    first = (await client.list_tools()).tools
    assert {t.description for t in first} == {"Definition v1"}
    async with client.listen(tools_list_changed=True) as subscription:
        await client.call_tool("m__change", {})
        event = await _next_event(subscription)
        assert type(event).__name__ == "ToolsListChanged"
    second = (await client.list_tools()).tools
    assert {t.description for t in second} == {"Definition v2"}

    updates = []

    async def progress(p, total, message):
        updates.append((p, total, message))

    result = await client.call_tool("m__wait", {"seconds": 2}, progress_callback=progress)
    assert result.content[0].text == "waited 2 s"
    assert updates == [(1, 2, "step 1"), (2, 2, "step 2")]

    with anyio.move_on_after(0.5):
        await client.call_tool("m__wait", {"seconds": 10})
    await wait_for_cancel()


def _check_modern_ledger(ledger_dir):
    (connected,) = rig.kinds(ledger_dir, "upstream.connected")
    assert connected["data"]["protocol_version"] == "2026-07-28"
    (client,) = rig.kinds(ledger_dir, "session.client")
    assert client["data"]["protocol_version"] == "2026-07-28"
    outcomes = [e["data"]["outcome"] for e in rig.kinds(ledger_dir, "call.returned")]
    assert outcomes[-1] == "cancelled"
    assert outcomes[:-1] == ["ok", "ok"]


@pytest.mark.parametrize("transport", ["memory", "stdio"])
def test_modern_upstream(tmp_path, transport):
    ledger_dir = tmp_path / "ledger"
    if transport == "memory":
        fake = FakeUpstream(ttl_ms=60_000)

        async def cancelled_upstream():
            with anyio.fail_after(2):
                while "wait cancelled" not in fake.events:
                    await anyio.sleep(0.01)

        async def scenario():
            async with rig.proxied(ledger_dir, [rig.spec("m", fake.server)]) as (client, gw):
                assert client.protocol_version == "2026-07-28"
                assert gw.upstreams["m"].protocol_version == "2026-07-28"
                await _exercise_modern(client, cancelled_upstream)

    else:
        log = tmp_path / "fake.log"
        log.touch()
        toml = (
            f"[upstream.m]\ncommand = {rig.toml_str(sys.executable)}\n"
            f"args = [{rig.toml_str(rig.MODERN)}]\n"
            f'env = {{ FAKE_TTL_MS = "60000", FAKE_LOG = {rig.toml_str(log)} }}\n'
        )
        cfg = rig.serve_config(tmp_path, toml, ledger_dir)

        async def cancelled_upstream():
            with anyio.fail_after(2):
                while "wait cancelled" not in log.read_text(encoding="utf-8"):
                    await anyio.sleep(0.01)

        async def scenario():
            async with Client(rig.serve_params(cfg)) as client:
                assert client.protocol_version == "2026-07-28"
                await _exercise_modern(client, cancelled_upstream)

    anyio.run(scenario)
    _check_modern_ledger(ledger_dir)


def test_handshake_upstream(tmp_path):
    """A 2025-11-25 upstream behind the proxy while the client side is 2026-07-28: progress,
    the upstream's own cancel within 2 s, and a list change through the message handler."""
    log = tmp_path / "probe.log"
    legacy = FakeUpstream()
    specs = [
        rig.spec("p", rig.probe({"PROBE_LOG": str(log)})),
        rig.spec("l", legacy.server, mode="legacy"),
    ]
    updates = []

    async def progress(p, total, message):
        updates.append((p, total))

    async def scenario():
        async with rig.proxied(tmp_path / "ledger", specs) as (client, gw):
            assert client.protocol_version == "2026-07-28"
            assert gw.upstreams["p"].protocol_version == "2025-11-25"
            assert gw.upstreams["l"].protocol_version == "2025-11-25"
            await client.call_tool("p__wait", {"seconds": 2}, progress_callback=progress)
            with anyio.move_on_after(0.5):
                await client.call_tool("p__wait", {"seconds": 10})
            cancelled_at = time.time()
            with anyio.fail_after(3):
                while "notifications/cancelled" not in log.read_text(encoding="utf-8"):
                    await anyio.sleep(0.01)
            async with client.listen(tools_list_changed=True) as subscription:
                await client.call_tool("l__change", {})
                await _next_event(subscription)
            descriptions = {
                t.description for t in (await client.list_tools()).tools if t.name.startswith("l__")
            }
            return cancelled_at, descriptions

    cancelled_at, descriptions = anyio.run(scenario)
    assert updates == [(1, 2), (2, 2)]
    assert descriptions == {"Definition v2"}
    lines = [line.split(" ", 1) for line in log.read_text(encoding="utf-8").splitlines()]
    (cancel,) = [
        (float(ts), json.loads(rest)) for ts, rest in lines if "notifications/cancelled" in rest
    ]
    assert cancel[0] - cancelled_at < 2
    outcomes = [e["data"]["outcome"] for e in rig.kinds(tmp_path / "ledger", "call.returned")]
    assert outcomes == ["ok", "cancelled", "ok"]


def test_eras(tmp_path):
    """Clients on 2026-07-28 and 2025-11-25 through the same proxy: list, call, progress, and a
    list change delivered the way each era expects (listen, or notifications/tools/list_changed)."""
    fake = FakeUpstream()

    async def scenario():
        async with rig.gateway(tmp_path / "ledger", [rig.spec("f", fake.server)]) as gw:
            notices = []

            async def on_message(message):
                if isinstance(message, types.ToolListChangedNotification):
                    notices.append(message)

            async with (
                Client(gw.server, mode="auto") as modern,
                Client(gw.server, mode="legacy", message_handler=on_message) as legacy,
            ):
                assert (modern.protocol_version, legacy.protocol_version) == (
                    "2026-07-28",
                    "2025-11-25",
                )
                for client in (modern, legacy):
                    names = [t.name for t in (await client.list_tools()).tools]
                    assert "f__echo" in names
                    result = await client.call_tool("f__echo", {"era": client.protocol_version})
                    assert json.loads(result.content[0].text) == {"era": client.protocol_version}
                    updates = []

                    async def progress(p, total, message, updates=updates):
                        updates.append(p)

                    await client.call_tool("f__wait", {"seconds": 1}, progress_callback=progress)
                    assert updates == [1]
                async with modern.listen(tools_list_changed=True) as subscription:
                    await modern.call_tool("f__change", {})
                    await _next_event(subscription)
                with anyio.fail_after(2):
                    while not notices:
                        await anyio.sleep(0.01)

    anyio.run(scenario)
    assert len(rig.kinds(tmp_path / "ledger", "call.returned")) == 5
