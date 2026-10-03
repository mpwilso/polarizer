"""Result fidelity: a tool's result through the proxy compared with the same call made
directly (docs/PROXY-SPEC.md, Results). Only the names should change; every difference the
SDK makes is pinned here exactly, so a new one fails the test."""

import copy
import sys

import anyio
import pytest
from helpers import probe_server, rig
from helpers.fakes import FakeUpstream
from helpers.raw import RawClient
from mcp import Client
from mcp_types import SERVER_INFO_META_KEY

from polarizer import __version__


def dump(result) -> dict:
    return result.model_dump(by_alias=True, mode="json")


def known(value):
    """`value` without the keys the protocol doesn't define (the probe names them x-unknown*)."""
    if isinstance(value, dict):
        return {k: known(v) for k, v in value.items() if not k.startswith("x-unknown")}
    if isinstance(value, list):
        return [known(v) for v in value]
    return value


def stamped(result: dict, name: str, version: str) -> dict:
    """`result` with the serverInfo stamp the SDK adds to 2026-07-28 results."""
    result = copy.deepcopy(result)
    result["_meta"] = {
        **(result.get("_meta") or {}),
        SERVER_INFO_META_KEY: {"name": name, "version": version},
    }
    return result


@pytest.mark.parametrize("mode", ["auto", "legacy"])
def test_rich_result_in_memory(tmp_path, mode):
    """Text, image, audio, embedded text and blob resources, a resource link, annotations,
    _meta at each level, structuredContent, and isError both ways."""
    names = ("rich", "rich_error")

    async def scenario():
        direct, proxied = {}, {}
        fake = FakeUpstream(names=(), extra=names)
        async with Client(fake.server, mode=mode) as client:
            for name in names:
                direct[name] = dump(await client.call_tool(name, {}))
        fake = FakeUpstream(names=(), extra=names)
        async with rig.proxied(tmp_path / "ledger", [rig.spec("f", fake.server)], mode) as (
            client,
            _,
        ):
            for name in names:
                proxied[name] = dump(await client.call_tool(f"f__{name}", {}))
        return direct, proxied

    direct, proxied = anyio.run(scenario)
    assert direct["rich"]["isError"] is False and direct["rich_error"]["isError"] is True
    for name in names:
        if mode == "auto":
            # Client and upstream both speak 2026-07-28: identical, including the upstream's
            # own serverInfo stamp, which the proxy passes through.
            assert proxied[name] == direct[name]
        else:
            # A 2025-11-25 client gets no stamp directly, but the proxy's link to the upstream
            # is 2026-07-28, so the upstream's stamp comes through.
            assert proxied[name] == stamped(direct[name], "fake", "1")


def test_rich_result_from_older_upstream_over_stdio(tmp_path):
    """The Claude Code case: a 2026-07-28 client, a 2025-11-25 upstream, real stdio on both
    sides of `polarizer serve`. The SDK stamps Polarizer's serverInfo on the client's side."""
    toml = f"[upstream.p]\ncommand = {rig.toml_str(sys.executable)}\nargs = [{rig.toml_str(rig.PROBE)}]\n"
    cfg = rig.serve_config(tmp_path, toml, tmp_path / "ledger")

    async def scenario():
        async with Client(rig.probe()) as client:
            direct = dump(await client.call_tool("rich", {}))
        async with Client(rig.serve_params(cfg)) as client:
            assert client.protocol_version == "2026-07-28"
            proxied = dump(await client.call_tool("p__rich", {}))
        return direct, proxied

    direct, proxied = anyio.run(scenario)
    assert proxied == stamped(direct, "polarizer", __version__)


MODERN_META = {
    "io.modelcontextprotocol/protocolVersion": "2026-07-28",
    "io.modelcontextprotocol/clientInfo": {"name": "raw-test", "version": "1"},
    "io.modelcontextprotocol/clientCapabilities": {},
}


@pytest.mark.parametrize("era", ["2025-11-25", "2026-07-28"])
def test_rich_result_on_the_wire(tmp_path, era):
    """Raw bytes, no SDK client on the test's side: what a non-SDK client such as Claude Code
    receives. Fields the protocol doesn't define are dropped, at every level, because the
    SDK's client parses the upstream's result into its models (a stated limit)."""
    toml = f"[upstream.p]\ncommand = {rig.toml_str(sys.executable)}\nargs = [{rig.toml_str(rig.PROBE)}]\n"
    cfg = rig.serve_config(tmp_path, toml, tmp_path / "ledger")
    serve = [sys.executable, "-m", "polarizer", "serve", "--config", str(cfg)]
    with RawClient(serve) as client:
        if era == "2026-07-28":
            params = {"name": "p__rich", "arguments": {}, "_meta": MODERN_META}
        else:
            client.initialize(era)
            params = {"name": "p__rich", "arguments": {}}
        result = client.request("tools/call", params)["result"]
    assert client.proc.returncode == 0
    assert known(probe_server.RICH) != probe_server.RICH  # the probe did send unknown fields
    if era == "2025-11-25":
        assert result == known(probe_server.RICH)
    else:
        # 2026-07-28 requires resultType, and the SDK stamps its serverInfo.
        expected = stamped(known(probe_server.RICH), "polarizer", __version__)
        assert result == {**expected, "resultType": "complete"}


def test_unreadable_result(tmp_path):
    """A result the SDK can't parse becomes a transport-error with a fixed line. The SDK's own
    message quotes the result, so neither the client nor the ledger gets it."""

    async def scenario():
        async with rig.proxied(tmp_path / "ledger", [rig.spec("p", rig.probe())]) as (client, _):
            bad = await client.call_tool("p__invalid", {})
            after = await client.call_tool("p__wait", {"seconds": 0})
        return bad, after

    bad, after = anyio.run(scenario)
    line = "polarizer: upstream p failed: result did not match the MCP schema"
    assert bad.is_error and [c.text for c in bad.content] == [line]
    assert not after.is_error  # the upstream connection survives
    first, second = (e["data"] for e in rig.kinds(tmp_path / "ledger", "call.returned"))
    assert (first["outcome"], first["error"]) == ("transport-error", line)
    assert second["outcome"] == "ok"
    raw = (tmp_path / "ledger" / "ledger.jsonl").read_text(encoding="utf-8")
    secret = probe_server.INVALID["content"][0]["data"]
    assert secret not in raw and "video" not in raw
