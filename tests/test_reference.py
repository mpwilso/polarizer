"""Smoke tests against the pinned reference servers, Everything and Filesystem, run by npx.

Local only: they fetch from npm, so they are skipped unless POLARIZER_REFERENCE=1, and CI
never sets it. The pinned versions and their install commands are in docs/verified-facts.md.
Each check calls the server directly and through `polarizer serve`, and compares: with SDK
clients on both sides, then with raw JSON-RPC on both sides, which shows anything the SDK
hides (docs/PROXY-SPEC.md, Results)."""

import copy
import json
import os
import shutil
import sys

import anyio
import pytest
from helpers import rig
from helpers.raw import RawClient
from mcp import Client, StdioServerParameters
from mcp_types import SERVER_INFO_META_KEY

from polarizer import __version__, defhash

pytestmark = [
    pytest.mark.reference,
    pytest.mark.skipif(
        os.environ.get("POLARIZER_REFERENCE") != "1",
        reason="reference servers run only with POLARIZER_REFERENCE=1 (they fetch from npm)",
    ),
]

EVERYTHING = "@modelcontextprotocol/server-everything@2026.8.31"
FILESYSTEM = "@modelcontextprotocol/server-filesystem@2026.8.31"


@pytest.fixture
def servers(tmp_path):
    """{prefix: argv} for both servers, the Filesystem server confined to a temp directory
    holding one file, and a polarizer.toml with both as upstreams."""
    npx = shutil.which("npx")
    if npx is None:
        pytest.fail("npx is not on PATH")
    root = tmp_path / "root"
    root.mkdir()
    (root / "note.txt").write_text("hello from a reference test\n", encoding="utf-8")
    argv = {
        "every": [npx, "-y", EVERYTHING, "stdio"],
        "fs": [npx, "-y", FILESYSTEM, str(root)],
    }
    toml = "".join(
        f"[upstream.{prefix}]\ncommand = {rig.toml_str(a[0])}\n"
        f"args = [{', '.join(rig.toml_str(x) for x in a[1:])}]\nconnect_timeout_seconds = 20\n\n"
        for prefix, a in argv.items()
    )
    cfg = rig.serve_config(tmp_path, toml, tmp_path / "ledger")
    return {"argv": argv, "cfg": cfg, "root": root, "toml": toml}


CALLS = [
    ("every", "echo", {"message": "hello through the proxy"}),
    ("fs", "list_directory", {"path": "<root>"}),
    ("fs", "read_text_file", {"path": "<root>/note.txt"}),
]


def _arguments(arguments: dict, root) -> dict:
    return {k: v.replace("<root>", root.as_posix()) for k, v in arguments.items()}


def _tool_differences(direct: list[dict], proxied: list[dict], prefix: str) -> list[str]:
    """Every difference between two tool lists other than the prefix on the names, as
    "<tool>: [<keys>] differ" lines."""
    found = []
    by_name = {t["name"]: t for t in direct}
    renamed = {}
    for tool in proxied:
        if not tool["name"].startswith(f"{prefix}__"):
            found.append(f"unexpected name {tool['name']}")
            continue
        renamed[tool["name"][len(prefix) + 2 :]] = {**tool, "name": tool["name"][len(prefix) + 2 :]}
    if list(renamed) != list(by_name):
        found.append(f"names or order differ: {list(by_name)} vs {list(renamed)}")
    for name, tool in by_name.items():
        if name in renamed and renamed[name] != tool:
            keys = sorted(
                k for k in tool.keys() | renamed[name].keys() if tool.get(k) != renamed[name].get(k)
            )
            found.append(f"{name}: {keys} differ")
    return found


def _without_stamp(result: dict) -> dict:
    """A result without the serverInfo stamp the SDK adds on the 2026-07-28 side."""
    result = copy.deepcopy(result)
    meta = result.get("_meta")
    if isinstance(meta, dict):
        meta.pop(SERVER_INFO_META_KEY, None)
        result["_meta"] = meta or None
    return result


def test_sdk_clients(servers):
    root = servers["root"]
    rig.prime(servers["cfg"])

    async def scenario():
        direct_tools, direct_results, versions = {}, [], {}
        for prefix, argv in servers["argv"].items():
            params = StdioServerParameters(command=argv[0], args=argv[1:])
            async with Client(params) as client:
                versions[prefix] = client.protocol_version
                direct_tools[prefix] = [
                    t.model_dump(by_alias=True, mode="json")
                    for t in (await client.list_tools()).tools
                ]
                for p, tool, arguments in CALLS:
                    if p == prefix:
                        result = await client.call_tool(tool, _arguments(arguments, root))
                        direct_results.append(result.model_dump(by_alias=True, mode="json"))
        async with Client(rig.serve_params(servers["cfg"])) as client:
            assert client.protocol_version == "2026-07-28"
            listed = [
                t.model_dump(by_alias=True, mode="json") for t in (await client.list_tools()).tools
            ]
            proxied_results = []
            for p, tool, arguments in CALLS:
                result = await client.call_tool(f"{p}__{tool}", _arguments(arguments, root))
                proxied_results.append(result.model_dump(by_alias=True, mode="json"))
        return direct_tools, direct_results, listed, proxied_results, versions

    direct_tools, direct_results, listed, proxied_results, versions = anyio.run(scenario)
    print(f"\ndirect protocol versions: {versions}")
    for prefix, tools in direct_tools.items():
        mine = [t for t in listed if t["name"].startswith(f"{prefix}__")]
        found = _tool_differences(tools, mine, prefix)
        print(f"{prefix}: {len(tools)} tools, differences: {found}")
        # The one known difference: the 2026-07-28 surface has no `execution` (tasks are
        # 2025-11-25 only), so the SDK drops it on the client's side of the proxy.
        assert versions[prefix] != "2026-07-28"
        with_execution = [t["name"] for t in tools if t["execution"] is not None]
        assert found == [f"{name}: ['execution'] differ" for name in with_execution]
        assert all(t["execution"] is None for t in mine)
    stamp = {"name": "polarizer", "version": __version__}
    for (prefix, tool, _), direct, proxied in zip(
        CALLS, direct_results, proxied_results, strict=True
    ):
        assert not direct["isError"], (tool, direct)
        # The client side of the proxy is 2026-07-28; the direct link is the server's own era.
        if versions[prefix] != "2026-07-28":
            assert proxied["_meta"][SERVER_INFO_META_KEY] == stamp
        assert _without_stamp(proxied) == _without_stamp(direct), tool
    assert "hello from a reference test" in str(direct_results[2])


def test_raw_wire(servers):
    """Both sides at 2025-11-25 over raw JSON-RPC: the bytes a non-SDK client gets. From M1a,
    tools are served from stored copies in the 2026-07-28 form, so `execution` is gone in this
    era too (docs/PIN-SPEC.md, section 2)."""
    root = servers["root"]
    rig.prime(servers["cfg"])
    direct_tools, direct_results = {}, []
    for prefix, argv in servers["argv"].items():
        with RawClient(argv) as client:
            client.initialize()
            direct_tools[prefix] = client.request("tools/list")["result"]["tools"]
            for p, tool, arguments in CALLS:
                if p == prefix:
                    params = {"name": tool, "arguments": _arguments(arguments, root)}
                    direct_results.append(client.request("tools/call", params)["result"])
    serve = [sys.executable, "-m", "polarizer", "serve", "--config", str(servers["cfg"])]
    with RawClient(serve) as client:
        client.initialize()
        listed = client.request("tools/list")["result"]["tools"]
        proxied_results = []
        for p, tool, arguments in CALLS:
            params = {"name": f"{p}__{tool}", "arguments": _arguments(arguments, root)}
            proxied_results.append(client.request("tools/call", params)["result"])
    for prefix, tools in direct_tools.items():
        mine = [t for t in listed if t["name"].startswith(f"{prefix}__")]
        found = _tool_differences(tools, mine, prefix)
        print(f"\n{prefix}: {len(tools)} tools, differences: {found}")
        with_execution = [t["name"] for t in tools if "execution" in t]
        assert found == [f"{name}: ['execution'] differ" for name in with_execution]
    for (_, tool, _), direct, proxied in zip(CALLS, direct_results, proxied_results, strict=True):
        # The one known difference: the SDK's model defaults isError to false, so a result
        # that leaves it out gains "isError": false. Absent means false in the protocol.
        added = {k: v for k, v in proxied.items() if k not in direct}
        print(f"{tool}: added {added}")
        assert added in ({}, {"isError": False}), tool
        assert proxied == {**direct, **added}, tool


def _integers(value):
    """Every integer (not bool) anywhere in a JSON value."""
    if isinstance(value, bool):
        return
    if isinstance(value, int):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _integers(item)
    elif isinstance(value, list):
        for item in value:
            yield from _integers(item)


def test_reference_tools_hash_and_serve(servers, tmp_path):
    """Every tool of the pinned servers hashes without error. Approved, each is served equal
    to its stored copy, renamed, and two runs give the same hashes. Prints the counts the
    owner asked for: tools hashed, tools with _meta, unservable tools and why, and the largest
    integer in any schema."""
    raw = {}
    for prefix, argv in servers["argv"].items():
        with RawClient(argv) as client:
            client.initialize()
            raw[prefix] = client.request("tools/list")["result"]["tools"]
    runs = []
    for name in ("first", "second"):
        cfg = servers["cfg"]
        if name == "second":
            (tmp_path / "second").mkdir()
            cfg = rig.serve_config(tmp_path / "second", servers["toml"], tmp_path / "ledger2")
        ledger_dir = rig.prime(cfg)
        seen = {
            (e["data"]["upstream"], e["data"]["tool"]): e["data"]["def_hash"]
            for e in rig.kinds(ledger_dir, "tool.seen")
        }
        unservable = [e["data"] for e in rig.kinds(ledger_dir, "tool.unservable")]
        runs.append((cfg, ledger_dir, seen, unservable))
    (cfg, ledger_dir, seen, unservable), (_, _, seen2, unservable2) = runs
    total = sum(len(tools) for tools in raw.values())
    assert seen == seen2
    assert len(seen) + len({(u["upstream"], u["tool"]) for u in unservable}) == total
    approved = {
        (e["data"]["upstream"], e["data"]["tool"]): e["data"]["def_hash"]
        for e in rig.kinds(ledger_dir, "tool.approved")
    }
    assert approved == seen

    async def sdk_listing():
        async with Client(rig.serve_params(cfg)) as client:
            assert client.protocol_version == "2026-07-28"
            return [
                t.model_dump(by_alias=True, mode="json", exclude_none=True)
                for t in (await client.list_tools()).tools
            ]

    sdk = anyio.run(sdk_listing)
    serve = [sys.executable, "-m", "polarizer", "serve", "--config", str(cfg)]
    with RawClient(serve) as client:
        client.initialize()
        wire = client.request("tools/list")["result"]["tools"]
    for listed in (sdk, wire):
        assert len(listed) == len(seen)
        for tool in listed:
            prefix, name = tool["name"].split("__", 1)
            stored = defhash.read_copy(ledger_dir, seen[(prefix, name)])
            assert tool == {**stored, "name": tool["name"]}, tool["name"]

    with_meta = [f"{p}__{t['name']}" for p, tools in raw.items() for t in tools if "_meta" in t]
    schemas = [
        t.get(k) for tools in raw.values() for t in tools for k in ("inputSchema", "outputSchema")
    ]
    largest = max((n for schema in schemas for n in _integers(schema)), default=None)
    report = {
        "tools listed": {p: len(tools) for p, tools in raw.items()},
        "tools hashed": len(seen),
        "tools with _meta": with_meta,
        "unservable": [(u["upstream"], u["tool"], u["problem"]) for u in unservable],
        "largest integer in any schema": largest,
    }
    assert unservable == unservable2
    print("\nreference report: " + json.dumps(report, sort_keys=True))
