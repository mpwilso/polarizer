"""The definition hash and the stored copy (docs/PIN-SPEC.md, sections 2 and 5)."""

import copy
import json
import sys

import anyio
import mcp_types as types
import pytest
from helpers import probe_server, rig
from helpers.fakes import FakeUpstream
from mcp_types.methods import serialize_server_result

from polarizer import cli, defhash

WAIT = probe_server.TOOLS[0]
WAIT_HASH = "ac0778cf2bd27c6f802ca57ffc736ffddcd97d9773898dcc1c9c68ea9bc54e8b"
WAIT_CANON = (
    b'{"description":"Wait for a number of seconds.","inputSchema":{"properties":'
    b'{"seconds":{"type":"number"}},"required":["seconds"],"type":"object"},"name":"wait"}'
)
NESTED_HASH = "3462be54bba570aacd2a87ea2bb68c6806a68abac4761e0d848d1b6deb8a11b5"


def tool(obj: dict) -> types.Tool:
    return types.Tool.model_validate(obj)


def hash_of(obj: dict) -> str:
    return defhash.definition(tool(obj))[0]


def test_fixed_definition_fixed_hash(tmp_path):
    def_hash, canon = defhash.definition(tool(WAIT))
    assert def_hash == WAIT_HASH
    assert canon == WAIT_CANON
    assert defhash.write_copy(tmp_path, def_hash, canon) is True
    assert (tmp_path / "defs" / f"{WAIT_HASH}.json").read_bytes() == WAIT_CANON
    assert defhash.write_copy(tmp_path, def_hash, canon) is False  # never replaced
    assert defhash.read_copy(tmp_path, def_hash) == json.loads(WAIT_CANON)
    assert sorted(p.name for p in (tmp_path / "defs").iterdir()) == [f"{WAIT_HASH}.json"]


def test_nested_description_changes_hash():
    nested = copy.deepcopy(WAIT)
    nested["inputSchema"]["properties"]["seconds"]["description"] = "How long."
    assert hash_of(nested) == NESTED_HASH


EXECUTION_TOOL = types.Tool(
    name="t",
    description="A task-capable tool.",
    input_schema={"type": "object"},
    execution=types.ToolExecution(task_support="optional"),
)


@pytest.mark.parametrize("transport", ["memory", "stdio"])
def test_same_hash_in_both_eras(tmp_path, transport):
    """One tool with `execution`, reached in legacy mode (2025-11-25) and auto mode
    (2026-07-28): one hash. `execution` reaches the parsed Tool only in the first."""
    definitions = tmp_path / "definitions.json"
    definitions.write_text(
        json.dumps([EXECUTION_TOOL.model_dump(by_alias=True, mode="json", exclude_none=True)]),
        encoding="utf-8",
    )
    seen = {}

    async def one(mode):
        if transport == "memory":
            target = FakeUpstream(definitions=[EXECUTION_TOOL]).server
        else:
            target = rig.modern({"FAKE_DEFINITIONS": str(definitions)})
        ledger_dir = tmp_path / f"ledger-{mode}"
        async with rig.gateway(ledger_dir, [rig.spec("m", target, mode=mode)], False) as gw:
            upstream = gw.upstreams["m"]
            seen[mode] = (upstream.protocol_version, upstream.tools["t"].execution)
        (entry,) = rig.kinds(ledger_dir, "tool.seen")
        return entry["data"]["def_hash"]

    async def scenario():
        return {mode: await one(mode) for mode in ("legacy", "auto")}

    hashes = anyio.run(scenario)
    assert hashes["legacy"] == hashes["auto"] == defhash.definition(EXECUTION_TOOL)[0]
    assert seen["legacy"][0] == "2025-11-25" and seen["legacy"][1] is not None
    assert seen["auto"][0] == "2026-07-28" and seen["auto"][1] is None


EVERY_FIELD = {
    "name": "all",
    "title": "Every field",
    "description": "Uses every Tool field.",
    "inputSchema": {"type": "object", "properties": {"a": {"type": "string"}}},
    "execution": {"taskSupport": "optional"},
    "outputSchema": {"type": "object", "properties": {"b": {"type": "number"}}},
    "icons": [{"src": "https://example.com/i.png", "mimeType": "image/png", "sizes": ["16x16"]}],
    "annotations": {"title": "All", "readOnlyHint": True, "destructiveHint": False},
    "_meta": {"k": "v"},
}


def test_execution_is_the_only_era_field():
    # A new Tool field fails here first, so the comparison below always covers every field.
    assert set(types.Tool.model_fields) == {
        "name",
        "title",
        "description",
        "input_schema",
        "execution",
        "output_schema",
        "icons",
        "annotations",
        "meta",
    }
    mono = tool(EVERY_FIELD).model_dump(by_alias=True, mode="json", exclude_none=True)
    served = {}
    for version in ("2025-11-25", "2026-07-28"):
        envelope = {"tools": [mono]}
        if version == "2026-07-28":
            envelope.update(defhash.ENVELOPE)
        served[version] = serialize_server_result("tools/list", version, envelope)["tools"][0]
    old, new = served["2025-11-25"], served["2026-07-28"]
    assert set(old) - set(new) == {"execution"}
    assert set(new) - set(old) == set()
    assert {k: v for k, v in old.items() if k != "execution"} == new


def test_null_and_absent():
    base = {"name": "n", "inputSchema": {"type": "object"}}
    assert hash_of({**base, "title": None, "description": None}) == hash_of(base)
    assert hash_of({**base, "inputSchema": {"type": "object", "x": None}}) == hash_of(base)
    with_default = {"type": "object", "properties": {"p": {"type": "string"}}}
    null_default = copy.deepcopy(with_default)
    null_default["properties"]["p"]["default"] = None
    assert hash_of({**base, "inputSchema": null_default}) != hash_of(
        {**base, "inputSchema": with_default}
    )
    empty = hash_of({**base, "annotations": {}})
    assert hash_of({**base, "annotations": {"title": None}}) == empty
    assert empty != hash_of(base)
    assert defhash.definition(tool({**base, "annotations": {"title": None}}))[1] == (
        b'{"annotations":{},"inputSchema":{"type":"object"},"name":"n"}'
    )


def _reversed(value):
    if isinstance(value, dict):
        return {k: _reversed(value[k]) for k in reversed(list(value))}
    if isinstance(value, list):
        return [_reversed(v) for v in value]
    return value


def test_key_order():
    every = {k: v for k, v in EVERY_FIELD.items()}
    assert hash_of(_reversed(every)) == hash_of(every)
    assert hash_of(_reversed(WAIT)) == WAIT_HASH


def test_unknown_field_dropped(tmp_path):
    base = copy.deepcopy(EVERY_FIELD)
    noisy = copy.deepcopy(EVERY_FIELD)
    noisy["bogusField"] = 1
    noisy["annotations"]["x-bogus"] = 2
    noisy["icons"][0]["x-bogus"] = 3
    noisy["execution"]["x-bogus"] = 4
    assert defhash.definition(tool(noisy)) == defhash.definition(tool(base))

    keyword = copy.deepcopy(WAIT)
    keyword["inputSchema"]["x-keyword"] = {"kept": True}
    assert hash_of(keyword) != WAIT_HASH
    # ...and it is served: approved, the client gets it from the stored copy.
    fake = FakeUpstream(definitions=[tool(keyword)])

    async def scenario():
        async with rig.proxied(tmp_path / "ledger", [rig.spec("f", fake.server)]) as (client, _):
            return (await client.list_tools()).tools

    (served,) = anyio.run(scenario)
    assert served.input_schema["x-keyword"] == {"kept": True}


def test_meta_not_hashed_not_served(tmp_path):
    """A change to _meta alone changes neither the hash nor the stored copy, and doesn't
    hide the tool; the served copy has no _meta."""
    with_meta = {**WAIT, "_meta": {"version": 1}}
    other_meta = {**WAIT, "_meta": {"version": 2, "extra": "x"}}
    assert defhash.definition(tool(with_meta)) == (WAIT_HASH, WAIT_CANON)
    assert defhash.definition(tool(other_meta)) == (WAIT_HASH, WAIT_CANON)
    fake = FakeUpstream(definitions=[tool(with_meta)])

    async def scenario():
        async with rig.proxied(tmp_path / "ledger", [rig.spec("f", fake.server)]) as (client, _):
            first = (await client.list_tools()).tools
            fake.definitions = [tool(other_meta)]
            second = (await client.list_tools()).tools
            return first, second

    first, second = anyio.run(scenario)
    assert [t.name for t in first] == [t.name for t in second] == ["f__wait"]
    assert first[0].meta is None and second[0].meta is None
    assert rig.kinds(tmp_path / "ledger", "tool.drift") == []
    assert (tmp_path / "ledger" / "defs" / f"{WAIT_HASH}.json").read_bytes() == WAIT_CANON


def test_snake_case_input_schema_fails_listing(tmp_path, capfd):
    """input_schema on the wire fails the SDK client's whole listing: at connect the upstream
    doesn't connect, and on a later refresh it is a failed refresh."""

    async def scenario():
        specs = [
            rig.spec("never", rig.probe({"PROBE_SNAKE": "0"})),
            rig.spec("later", rig.probe({"PROBE_SNAKE": "1"})),
        ]
        async with rig.proxied(tmp_path / "ledger", specs) as (client, _):
            first = [t.name for t in (await client.list_tools()).tools]
            second = [t.name for t in (await client.list_tools()).tools]
            return first, second

    first, second = anyio.run(scenario)
    assert first == second == []
    connected = {
        e["data"]["prefix"]: e["data"] for e in rig.kinds(tmp_path / "ledger", "upstream.connected")
    }
    assert set(connected["never"]) == {"prefix", "error"}
    assert "inputSchema" in connected["never"]["error"]
    assert connected["later"]["tools"] == len(probe_server.TOOLS)
    (failed,) = rig.kinds(tmp_path / "ledger", "upstream.refresh_failed")
    assert (failed["data"]["prefix"], failed["data"]["trigger"]) == ("later", "client-list")
    assert "polarizer: upstream later: listing failed (" in capfd.readouterr().err


BIG = types.Tool(
    name="big",
    input_schema={
        "type": "object",
        "properties": {"n": {"type": "integer", "maximum": 9223372036854775807}},
    },
)


def test_unhashable_definition_hidden(tmp_path, capsys):
    with pytest.raises(defhash.Unhashable, match="exceeds safe integer domain"):
        defhash.definition(BIG)
    fake = FakeUpstream(definitions=[BIG, tool(WAIT)])

    async def scenario():
        async with rig.proxied(tmp_path / "ledger", [rig.spec("f", fake.server)]) as (client, _):
            names = [t.name for t in (await client.list_tools()).tools]
            refused = await client.call_tool("f__big", {"n": 1})
            return names, refused

    names, refused = anyio.run(scenario)
    assert names == ["f__wait"]  # approve_all approved only what can be hashed
    assert refused.content[0].text == "polarizer: f__big is not available: it cannot be served"
    (entry,) = rig.kinds(tmp_path / "ledger", "tool.unservable")
    assert entry["data"]["def_hash"] is None
    assert entry["data"]["problem"].startswith("cannot be hashed: ")
    (call,) = rig.kinds(tmp_path / "ledger", "call.refused")
    assert call["data"]["reason"].startswith('upstream f tool "big" cannot be served: cannot be')
    # approve can't approve it: no hash was ever seen for it.
    ledger_dir = str(tmp_path / "ledger")
    capsys.readouterr()  # serve's own stderr lines
    argv = ["approve", "--ledger-dir", ledger_dir, "f", "big", "0" * 64, "--allow-no-terminal"]
    code = cli.main(argv)
    out, err = capsys.readouterr()
    assert code == 2 and out == ""
    assert err == f"polarizer: Polarizer has not seen f__big with definition {'0' * 64}\n"


def test_written_copy_never_replaces(tmp_path):
    """A copy that exists, even an altered one, is never overwritten; temp files go away."""
    defhash.write_copy(tmp_path, WAIT_HASH, WAIT_CANON)
    path = tmp_path / "defs" / f"{WAIT_HASH}.json"
    path.write_bytes(WAIT_CANON + b" ")
    assert defhash.write_copy(tmp_path, WAIT_HASH, WAIT_CANON) is False
    assert path.read_bytes() == WAIT_CANON + b" "
    with pytest.raises(defhash.CopyProblem, match="does not match its hash"):
        defhash.read_copy(tmp_path, WAIT_HASH)
    assert [p.name for p in (tmp_path / "defs").iterdir()] == [f"{WAIT_HASH}.json"]


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX modes; Windows uses profile ACLs")
def test_copy_modes(tmp_path):
    defhash.write_copy(tmp_path, WAIT_HASH, WAIT_CANON)
    assert (tmp_path / "defs").stat().st_mode & 0o777 == 0o700
    assert (tmp_path / "defs" / f"{WAIT_HASH}.json").stat().st_mode & 0o777 == 0o600
