"""Tools only: the proxy advertises and serves nothing else (docs/PROXY-SPEC.md, Scope)."""

import warnings

import anyio
import mcp_types as types
import pytest
from helpers import rig
from helpers.fakes import FakeUpstream
from mcp import MCPError
from mcp.shared.exceptions import MCPDeprecationWarning

from polarizer.proxy import Gateway


def test_capabilities_only_tools(tmp_path):
    seen = {}

    async def scenario():
        for mode in ("auto", "legacy"):
            fake = FakeUpstream()
            async with rig.proxied(tmp_path / mode, [rig.spec("f", fake.server)], mode) as (
                client,
                _,
            ):
                seen[client.protocol_version] = client.server_capabilities.model_dump(
                    by_alias=True, mode="json", exclude_none=True
                )
                for request in (
                    client.list_resources(),
                    client.list_resource_templates(),
                    client.list_prompts(),
                    client.complete(types.PromptReference(name="p"), {"name": "a", "value": "b"}),
                ):
                    with pytest.raises(MCPError) as e:
                        await request
                    assert e.value.code == types.METHOD_NOT_FOUND
                if mode == "legacy":
                    with warnings.catch_warnings():
                        warnings.simplefilter("ignore", MCPDeprecationWarning)
                        with pytest.raises(MCPError) as e:
                            await client.set_logging_level("info")
                    assert e.value.code == types.METHOD_NOT_FOUND

    anyio.run(scenario)
    assert seen == {
        "2026-07-28": {"tools": {"listChanged": True}},
        "2025-11-25": {"experimental": {}, "tools": {"listChanged": True}},
    }


def test_registered_handlers_are_tools_only(tmp_path):
    from polarizer.writer import LedgerWriter

    writer = LedgerWriter.open(tmp_path / "ledger")
    try:
        gateway = Gateway([], writer, config_sha256=rig.CONFIG_SHA)
        server = gateway.server
        assert sorted(server._request_handlers) == [
            "ping",
            "server/discover",
            "subscriptions/listen",
            "tools/call",
            "tools/list",
        ]
        assert server._notification_handlers == {}
    finally:
        writer.close()
