"""Pins in the gateway: hiding, exposing from stored copies, refusing hidden tools, failed and
lost listings, sticky drift and the cap, and pins in motion: change notices on decisions,
mid-session drift through upstream notices, and the retry timer (docs/PIN-SPEC.md, sections 3
to 6). A second process approving while the gateway serves is in test_pin_durability.py."""

import os
import signal
import sys

import anyio
import mcp_types as types
import pytest
from helpers import rig
from helpers.fakes import FakeUpstream
from helpers.raw import RawClient
from mcp import Client
from mcp.shared.subscriptions import ToolsListChanged

from polarizer import cli, defhash
from polarizer.ledger import verify_bytes
from polarizer.pins import CAP, CAP_PROBLEM, PinState, ToolPins, state
from polarizer.sidefiles import ARGS


def hash_now(fake: FakeUpstream, name: str) -> str:
    """The definition hash of one of a fake's tools as it would list it now."""
    return defhash.definition(fake.tool(name))[0]


def run_cli(*argv) -> int:
    return cli.main([*argv, "--allow-no-terminal"])


async def approve(ledger_dir, prefix, tool, def_hash):
    code = await anyio.to_thread.run_sync(
        run_cli, "approve", "--ledger-dir", str(ledger_dir), prefix, tool, def_hash
    )
    assert code == 0


async def reject(ledger_dir, prefix, tool, def_hash, reason="not wanted"):
    argv = ["reject", "--ledger-dir", str(ledger_dir), prefix, tool, def_hash, "--reason", reason]
    code = await anyio.to_thread.run_sync(run_cli, *argv)
    assert code == 0


def names(tools) -> list[str]:
    return [t.name for t in tools]


# The fold ----------------------------------------------------------------------------------


def _fold(entries) -> PinState:
    pins = PinState()
    for seq, (kind, data) in enumerate(entries, 1):
        pins.apply({"seq": seq, "kind": kind, "data": {"upstream": "u", "tool": "t", **data}})
    return pins


def test_fold_gives_every_state():
    """Every row of the states table that the fold decides (section 4); list unknown and not
    listed depend on the live listing, and a failing copy on the file, which the gateway
    tests below cover."""
    a, b, c = "a" * 64, "b" * 64, "c" * 64
    s = {"session": "0" * 16}
    approved = {"def_hash": a, "actor": "person", "group": None}
    cases = [
        ([], a, "pending"),
        ([("tool.seen", {**s, "def_hash": a})], a, "pending"),
        ([("tool.approved", approved)], a, "approved"),
        ([("tool.approved", approved)], b, "changed"),
        (
            [("tool.approved", approved), ("tool.drift", {"approved_hash": a, "live_hash": b})],
            a,
            "changed",
        ),
        ([("tool.rejected", {"def_hash": a, "actor": "person", "reason": "r"})], a, "rejected"),
        ([("tool.rejected", {"def_hash": a, "actor": "person", "reason": "r"})], b, "pending"),
        # A rejection of B removes the approval of A: the latest decision wins.
        (
            [
                ("tool.approved", approved),
                ("tool.rejected", {"def_hash": b, "actor": "person", "reason": "r"}),
            ],
            a,
            "pending",
        ),
        # Approving A again ends the stickiness.
        (
            [
                ("tool.approved", approved),
                ("tool.drift", {"approved_hash": a, "live_hash": b}),
                ("tool.approved", approved),
            ],
            a,
            "approved",
        ),
    ]
    for entries, live, want in cases:
        assert state(_fold(entries).get("u", "t"), live)[0] == want, (entries, live)
    over = [("tool.seen", {"def_hash": f"{i:064x}"}) for i in range(CAP)]
    over.append(("tool.unservable", {"def_hash": c, "problem": CAP_PROBLEM}))
    pins = _fold(over).get("u", "t")
    assert state(pins, c) == ("unservable", CAP_PROBLEM)
    assert state(pins, f"{3:064x}")[0] == "pending"  # an observed one is still just pending
    assert state(ToolPins(), a)[0] == "pending"


# The gateway -------------------------------------------------------------------------------


def test_pending_tools_hidden(tmp_path, capfd):
    fake = FakeUpstream()
    ledger_dir = tmp_path / "ledger"

    async def scenario():
        async with rig.proxied(ledger_dir, [rig.spec("f", fake.server)], approve=False) as (c, _):
            listed = (await c.list_tools()).tools
            again = (await c.list_tools()).tools
            return listed, again

    listed, again = anyio.run(scenario)
    assert listed == again == []
    seen = rig.kinds(ledger_dir, "tool.seen")
    assert [e["data"]["tool"] for e in seen] == fake.names  # one each, not one per listing
    for entry in seen:
        assert (ledger_dir / "defs" / f"{entry['data']['def_hash']}.json").is_file()
    assert len(list((ledger_dir / "defs").iterdir())) == len(fake.names)
    err = capfd.readouterr().err
    assert f"polarizer: {len(fake.names)} tools wait for approval; run polarizer pending" in err


def test_approve_exposes(tmp_path):
    """After approve, the client gets a change notice, without listing first (the watch reads
    the decision), and the tool is listed from its stored copy."""
    fake = FakeUpstream(names=["echo", "wait"])
    ledger_dir = tmp_path / "ledger"

    async def scenario():
        async with rig.proxied(ledger_dir, [rig.spec("f", fake.server)], approve=False) as (c, _):
            before = (await c.list_tools()).tools
            async with c.listen(tools_list_changed=True) as subscription:
                await approve(ledger_dir, "f", "wait", hash_now(fake, "wait"))
                assert isinstance(await rig.next_notice(subscription), ToolsListChanged)
            after = (await c.list_tools()).tools
            return before, after

    before, after = anyio.run(scenario)
    assert before == [] and names(after) == ["f__wait"]
    stored = defhash.read_copy(ledger_dir, hash_now(fake, "wait"))
    assert after[0].model_dump(by_alias=True, mode="json", exclude_none=True) == {
        **stored,
        "name": "f__wait",
    }
    (approved,) = rig.kinds(ledger_dir, "tool.approved")
    assert approved["data"] == {
        "upstream": "f",
        "tool": "wait",
        "def_hash": hash_now(fake, "wait"),
        "actor": "person",
        "group": None,
    }


def test_reject_hides_and_needs_reason(tmp_path, capsys):
    """reject without a reason writes nothing; with one, it hides an approved tool, records
    the reason, and the client gets a change notice."""
    fake = FakeUpstream(names=["echo"])
    ledger_dir = tmp_path / "ledger"
    h = hash_now(fake, "echo")

    async def scenario():
        async with rig.proxied(ledger_dir, [rig.spec("f", fake.server)]) as (c, _):
            before = (await c.list_tools()).tools
            size = (ledger_dir / "ledger.jsonl").stat().st_size
            for reason in ([], ["--reason", "  \n "]):
                argv = ["reject", "--ledger-dir", str(ledger_dir), "f", "echo", h, *reason]
                assert await anyio.to_thread.run_sync(run_cli, *argv) == 2
            assert (ledger_dir / "ledger.jsonl").stat().st_size == size
            async with c.listen(tools_list_changed=True) as subscription:
                await reject(ledger_dir, "f", "echo", h, "it  reads\nmy files")
                assert isinstance(await rig.next_notice(subscription), ToolsListChanged)
            after = (await c.list_tools()).tools
            refused = await c.call_tool("f__echo", {})
            return before, after, refused

    before, after, refused = anyio.run(scenario)
    assert names(before) == ["f__echo"] and after == []
    assert refused.content[0].text == "polarizer: f__echo is not available: rejected"
    (rejected,) = rig.kinds(ledger_dir, "tool.rejected")
    assert rejected["data"] == {
        "upstream": "f",
        "tool": "echo",
        "def_hash": h,
        "actor": "person",
        "reason": "it reads my files",
    }
    assert "polarizer: reject needs --reason <text>" in capsys.readouterr().err


def _fold_file(ledger_dir) -> PinState:
    pins = PinState()
    data = (ledger_dir / "ledger.jsonl").read_bytes()
    head = (ledger_dir / "ledger.head").read_bytes()
    assert verify_bytes(data, head, pins.apply).status == "intact"
    return pins


def test_state_from_ledger_matches_live(tmp_path):
    """After seen, approve, drift, reject and approve again, the state folded from the file
    alone equals the gateway's, and gives the same exposed list for the same upstream lists."""
    fake = FakeUpstream(names=["echo", "wait"])
    ledger_dir = tmp_path / "ledger"
    out = {}

    async def scenario():
        async with rig.proxied(ledger_dir, [rig.spec("f", fake.server)], approve=False) as (c, gw):
            v1 = hash_now(fake, "echo")
            await approve(ledger_dir, "f", "echo", v1)
            await approve(ledger_dir, "f", "wait", hash_now(fake, "wait"))
            assert names((await c.list_tools()).tools) == ["f__echo", "f__wait"]
            fake.version = 2  # drift for both
            assert (await c.list_tools()).tools == []
            await reject(ledger_dir, "f", "echo", hash_now(fake, "echo"))
            await approve(ledger_dir, "f", "wait", hash_now(fake, "wait"))
            out["live"] = names((await c.list_tools()).tools)
            out["pins"] = gw.pins.snapshot()
        async with rig.proxied(ledger_dir, [rig.spec("f", fake.server)], approve=False) as (c, gw):
            out["restarted"] = names((await c.list_tools()).tools)

    anyio.run(scenario)
    assert out["live"] == out["restarted"] == ["f__wait"]
    assert _fold_file(ledger_dir).snapshot() == out["pins"]
    kinds = [e["kind"] for e in rig.entries(ledger_dir) if e["kind"].startswith("tool.")]
    assert kinds.count("tool.drift") == 2 and kinds.count("tool.rejected") == 1


def test_restart_rebuilds_exposed_set(tmp_path):
    fake = FakeUpstream(names=["echo", "wait", "fail"])
    ledger_dir = tmp_path / "ledger"

    async def scenario():
        async with rig.proxied(ledger_dir, [rig.spec("f", fake.server)], approve=False) as (c, _):
            await approve(ledger_dir, "f", "wait", hash_now(fake, "wait"))
            await approve(ledger_dir, "f", "echo", hash_now(fake, "echo"))
            first = names((await c.list_tools()).tools)
        count = len(rig.entries(ledger_dir))
        async with rig.proxied(ledger_dir, [rig.spec("f", fake.server)], approve=False) as (c, _):
            second = names((await c.list_tools()).tools)
        return first, second, count

    first, second, count = anyio.run(scenario)
    assert first == second == ["f__echo", "f__wait"]  # the upstream's order
    later = rig.entries(ledger_dir)[count:]
    assert not [e for e in later if e["kind"] in ("tool.seen", "tool.drift")]


def test_drift_once_per_pair_and_sticky(tmp_path):
    """A rug pull mid-session records one drift, however often the changed definition is
    listed again; going back to the approved definition, with its own notice, keeps the tool
    hidden and records nothing."""
    fake = FakeUpstream(names=["echo"])
    ledger_dir = tmp_path / "ledger"

    async def scenario():
        async with rig.proxied(ledger_dir, [rig.spec("f", fake.server)]) as (c, gw):
            assert names((await c.list_tools()).tools) == ["f__echo"]
            await fake.rug_pull()
            await rig.until(lambda: rig.kinds(ledger_dir, "tool.drift"))
            for _ in range(3):
                assert (await c.list_tools()).tools == []
            count = len(rig.entries(ledger_dir))
            calls = fake.list_calls
            fake.version = 0
            await fake.rug_pull()  # back to v1, the approved definition, with a notice
            await rig.until(lambda: fake.list_calls > calls and not gw._notice_refreshing)
            back = (await c.list_tools()).tools
            refused = await c.call_tool("f__echo", {})
            return back, count, refused

    back, count, refused = anyio.run(scenario)
    assert back == []  # changed is sticky
    assert refused.content[0].text == (
        "polarizer: f__echo is not available: its definition changed after approval"
    )
    (drift,) = rig.kinds(ledger_dir, "tool.drift")
    v1 = defhash.definition(FakeUpstream(names=["echo"]).tool("echo"))[0]
    assert drift["data"]["approved_hash"] == v1 and drift["data"]["live_hash"] != v1
    after = [e["kind"] for e in rig.entries(ledger_dir)[count:]]
    assert after == ["call.refused"]  # the revert recorded nothing


MODERN_META = {
    "io.modelcontextprotocol/protocolVersion": "2026-07-28",
    "io.modelcontextprotocol/clientInfo": {"name": "raw-test", "version": "1"},
    "io.modelcontextprotocol/clientCapabilities": {},
}


@pytest.mark.parametrize("era", ["2025-11-25", "2026-07-28"])
def test_served_copy_is_stored_copy(tmp_path, era):
    """What a raw client receives, in either era, is the stored copy renamed: no `execution`
    and no `_meta`, though the upstream sends both."""
    definition = {
        "name": "t",
        "description": "Served from the stored copy.",
        "inputSchema": {"type": "object", "properties": {"x": {"type": "integer"}}},
        "execution": {"taskSupport": "optional"},
        "annotations": {"readOnlyHint": True},
        "_meta": {"vendor/key": "value"},
    }
    path = tmp_path / "definitions.json"
    path.write_text(__import__("json").dumps([definition]), encoding="utf-8")
    toml = (
        f"[upstream.m]\ncommand = {rig.toml_str(sys.executable)}\n"
        f"args = [{rig.toml_str(rig.MODERN)}]\nenv = {{ FAKE_DEFINITIONS = {rig.toml_str(path)} }}\n"
    )
    cfg = rig.serve_config(tmp_path, toml, tmp_path / "ledger")
    ledger_dir = rig.prime(cfg)
    serve = [sys.executable, "-m", "polarizer", "serve", "--config", str(cfg)]
    with RawClient(serve) as client:
        if era == "2026-07-28":
            listed = client.request("tools/list", {"_meta": MODERN_META})["result"]["tools"]
        else:
            client.initialize(era)
            listed = client.request("tools/list")["result"]["tools"]
    (entry,) = rig.kinds(ledger_dir, "tool.approved")
    stored = defhash.read_copy(ledger_dir, entry["data"]["def_hash"])
    assert "execution" not in stored and "_meta" not in stored
    assert listed == [{**stored, "name": "m__t"}]


def test_tampered_stored_copy_hides(tmp_path):
    fake = FakeUpstream(names=["echo"])
    ledger_dir = tmp_path / "ledger"
    h = hash_now(fake, "echo")
    path = ledger_dir / "defs" / f"{h}.json"

    async def scenario():
        async with rig.proxied(ledger_dir, [rig.spec("f", fake.server)]) as (c, _):
            assert names((await c.list_tools()).tools) == ["f__echo"]
            raw = path.read_bytes()
            path.write_bytes(raw[:-2] + b"X" + raw[-1:])
            hidden = [(await c.list_tools()).tools for _ in range(2)]
            refused = await c.call_tool("f__echo", {})
            path.unlink()  # the person deletes the altered copy...
            back = (await c.list_tools()).tools  # ...and the next refresh stores it again
            return hidden, refused, back

    hidden, refused, back = anyio.run(scenario)
    assert hidden == [[], []]
    assert refused.content[0].text == "polarizer: f__echo is not available: it cannot be served"
    (unservable,) = rig.kinds(ledger_dir, "tool.unservable")
    assert unservable["data"]["problem"] == "stored copy does not match its hash"
    assert unservable["data"]["def_hash"] == h
    (call,) = rig.kinds(ledger_dir, "call.refused")
    assert call["data"]["reason"] == (
        'upstream f tool "echo" cannot be served: stored copy does not match its hash'
    )
    assert names(back) == ["f__echo"]
    assert defhash.read_copy(ledger_dir, h)["name"] == "echo"


@pytest.mark.skipif(
    sys.platform == "win32", reason="Windows has no read-only directories in this sense"
)
def test_unwritable_stored_copy_hides(tmp_path):
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        pytest.skip("root can write to a read-only directory")
    fake = FakeUpstream(names=["echo"])
    ledger_dir = tmp_path / "ledger"
    defs = ledger_dir / "defs"

    async def scenario():
        async with rig.proxied(ledger_dir, [rig.spec("f", fake.server)]) as (c, _):
            assert names((await c.list_tools()).tools) == ["f__echo"]
            os.chmod(defs, 0o500)
            try:
                fake.version = 2  # a new definition, whose copy can't be written
                return (await c.list_tools()).tools
            finally:
                os.chmod(defs, 0o700)

    assert anyio.run(scenario) == []
    (unservable,) = rig.kinds(ledger_dir, "tool.unservable")
    assert unservable["data"]["problem"].startswith("stored copy could not be written: ")
    (drift,) = rig.kinds(ledger_dir, "tool.drift")  # still recorded, as section 5 says
    assert unservable["data"]["def_hash"] == drift["data"]["live_hash"]
    assert not (defs / f"{drift['data']['live_hash']}.json").exists()


def test_hidden_tool_called_by_name(tmp_path):
    """Each hidden state: one call.refused with the exact reason, the exact line for the
    client, no call upstream and no side file."""
    main = FakeUpstream(names=["pending", "rejected", "unservable"])
    changing = FakeUpstream(names=["changed"])
    slow = FakeUpstream(names=["slow"])
    ledger_dir = tmp_path / "ledger"
    specs = [
        rig.spec("a", main.server),
        rig.spec("c", changing.server),
        rig.spec("s", slow.server, timeout=0.5),
    ]

    async def scenario():
        async with rig.proxied(ledger_dir, specs, approve=False) as (client, _):
            for prefix, fake, tool in [
                ("a", main, "rejected"),
                ("a", main, "unservable"),
                ("c", changing, "changed"),
                ("s", slow, "slow"),
            ]:
                await approve(ledger_dir, prefix, tool, hash_now(fake, tool))
            await reject(ledger_dir, "a", "rejected", hash_now(main, "rejected"))
            copy = ledger_dir / "defs" / f"{hash_now(main, 'unservable')}.json"
            copy.write_bytes(b"{}")
            changing.version = 2
            slow.list_delay = 3  # past its 0.5 s timeout
            assert (await client.list_tools()).tools == []
            calls = [
                "a__pending",
                "a__rejected",
                "a__unservable",
                "c__changed",
                "s__slow",
                "a__nope",
            ]
            return [(await client.call_tool(name, {"k": 1})).content[0].text for name in calls]

    lines = anyio.run(scenario)
    assert lines == [
        "polarizer: a__pending is not available: waiting for approval",
        "polarizer: a__rejected is not available: rejected",
        "polarizer: a__unservable is not available: it cannot be served",
        "polarizer: c__changed is not available: its definition changed after approval",
        "polarizer: s__slow is not available: its server's tool list could not be checked",
        "polarizer: no tool named a__nope",
    ]
    reasons = [e["data"]["reason"] for e in rig.kinds(ledger_dir, "call.refused")]
    assert reasons == [
        'upstream a tool "pending" is pending approval',
        'upstream a tool "rejected" was rejected',
        'upstream a tool "unservable" cannot be served: stored copy does not match its hash',
        'upstream c tool "changed" changed after approval',
        "upstream s tool list could not be refreshed",
        'upstream a has no tool "nope"',
    ]
    assert main.calls == changing.calls == slow.calls == []
    assert rig.kinds(ledger_dir, "call.sent") == []
    assert not (ledger_dir / ARGS).exists()


def test_failed_refresh_recorded_not_unchanged(tmp_path, capfd):
    """A refresh that times out hides the upstream's tools and records one
    upstream.refresh_failed. A definition changed during the failure is drift afterwards; an
    unchanged one comes back with no new approval."""
    steady = FakeUpstream(names=["echo"])
    moving = FakeUpstream(names=["echo"])
    ledger_dir = tmp_path / "ledger"
    specs = [rig.spec("s", steady.server, timeout=0.5), rig.spec("m", moving.server, timeout=0.5)]

    async def scenario():
        async with rig.proxied(ledger_dir, specs) as (client, _):
            assert names((await client.list_tools()).tools) == ["s__echo", "m__echo"]
            steady.list_delay = moving.list_delay = 3
            failing = [(await client.list_tools()).tools for _ in range(2)]
            moving.version = 2  # changed while its listing fails
            steady.list_delay = moving.list_delay = 0
            after = names((await client.list_tools()).tools)
            return failing, after

    failing, after = anyio.run(scenario)
    assert failing == [[], []]
    assert after == ["s__echo"]  # steady came back as it was; moving changed
    failed = [e["data"] for e in rig.kinds(ledger_dir, "upstream.refresh_failed")]
    assert sorted((d["prefix"], d["trigger"], d["error"]) for d in failed) == [
        ("m", "client-list", "timeout after 0.5 s"),
        ("s", "client-list", "timeout after 0.5 s"),
    ]
    (drift,) = rig.kinds(ledger_dir, "tool.drift")
    assert drift["data"]["upstream"] == "m"
    assert (
        "polarizer: upstream s: listing timed out; its tools are hidden" in capfd.readouterr().err
    )


def test_lost_upstream_hides_tools(tmp_path):
    """An upstream that exits after connecting, during a call or while idle, hides its tools
    and records connection-lost once."""
    crash_log, idle_log = tmp_path / "crash.log", tmp_path / "idle.log"
    specs = [
        rig.spec("crashy", rig.probe({"PROBE_LOG": str(crash_log)})),
        rig.spec("idle", rig.probe({"PROBE_LOG": str(idle_log)})),
    ]
    ledger_dir = tmp_path / "ledger"

    async def scenario():
        async with rig.proxied(ledger_dir, specs) as (client, _):
            assert {n.split("__")[0] for n in names((await client.list_tools()).tools)} == {
                "crashy",
                "idle",
            }
            await client.call_tool("crashy__crash", {})
            pid = int(idle_log.read_text(encoding="utf-8").splitlines()[0].split()[-1])
            os.kill(pid, signal.SIGTERM)  # exits while idle (TerminateProcess on Windows)
            # Once its pipe closes, the SDK fails any request at once with -32000; a request
            # sent just before that is woken with the same error (verified-facts.md, Stage 4).
            await anyio.sleep(0.5)
            first = names((await client.list_tools()).tools)
            second = names((await client.list_tools()).tools)
            refused = await client.call_tool("idle__wait", {"seconds": 0})
            return first, second, refused

    first, second, refused = anyio.run(scenario)
    assert first == second == []
    assert refused.content[0].text == (
        "polarizer: idle__wait is not available: its server's tool list could not be checked"
    )
    lost = [e["data"] for e in rig.kinds(ledger_dir, "upstream.refresh_failed")]
    assert sorted((d["prefix"], d["trigger"]) for d in lost) == [
        ("crashy", "connection-lost"),
        ("idle", "connection-lost"),
    ]


def test_flip_flop_is_bounded(tmp_path):
    """An upstream with a new definition on every listing records at most 16 observations
    between decisions, then one cap entry, then nothing more. Driven by its own notices, it
    makes the client hear at most one notice per second: here exactly one, when the tool hides,
    since a changed tool stays hidden."""
    fake = FakeUpstream(names=["echo"])
    ledger_dir = tmp_path / "ledger"

    async def scenario():
        async with rig.proxied(ledger_dir, [rig.spec("f", fake.server)]) as (client, _):
            fake.bump_on_list = True
            for _ in range(CAP + 10):
                assert (await client.list_tools()).tools == []
            return await client.call_tool("f__echo", {})

    refused = anyio.run(scenario)
    assert refused.content[0].text == "polarizer: f__echo is not available: it cannot be served"
    drifts = rig.kinds(ledger_dir, "tool.drift")
    assert len(drifts) == CAP
    (cap,) = rig.kinds(ledger_dir, "tool.unservable")
    assert cap["data"]["problem"] == CAP_PROBLEM
    assert cap["seq"] > drifts[-1]["seq"]
    assert not (ledger_dir / "defs" / f"{cap['data']['def_hash']}.json").exists()
    assert len(list((ledger_dir / "defs").iterdir())) == CAP + 1  # the approved one, and 16

    flipping = FakeUpstream(names=["echo"])

    async def notices():
        ledger2 = tmp_path / "ledger2"
        async with rig.proxied(ledger2, [rig.spec("f", flipping.server)]) as (client, gw):
            assert names((await client.list_tools()).tools) == ["f__echo"]
            heard = []
            async with client.listen(tools_list_changed=True) as subscription:
                began = anyio.current_time()
                for _ in range(CAP + 10):
                    await flipping.rug_pull()
                    await anyio.sleep(0.02)
                await rig.until(lambda: not gw._notice_refreshing)
                with anyio.move_on_after(1.5):
                    async for event in subscription:
                        heard.append(event)
                elapsed = anyio.current_time() - began
            return heard, elapsed, len(rig.kinds(ledger2, "tool.drift"))

    heard, elapsed, drifts = anyio.run(notices)
    assert 1 <= len(heard) <= 1 + int(elapsed)
    assert len(heard) == 1  # the tool hid once; later changes don't change what is exposed
    assert 1 <= drifts <= CAP


def test_drift_mid_session_modern(tmp_path):
    """A 2026-07-28 upstream's rug_pull() hides its tool through a listen event, with no
    client tools/list: one tool.drift, and a change published to the client."""
    fake = FakeUpstream(names=["echo", "wait"])
    ledger_dir = tmp_path / "ledger"

    async def scenario():
        async with rig.proxied(ledger_dir, [rig.spec("m", fake.server)]) as (client, gw):
            assert gw.upstreams["m"].protocol_version == "2026-07-28"
            assert names((await client.list_tools()).tools) == ["m__echo", "m__wait"]
            async with client.listen(tools_list_changed=True) as subscription:
                await fake.rug_pull()
                event = await rig.next_notice(subscription)
            hidden = await gw.exposed()
            listed = (await client.list_tools()).tools
            return event, hidden, listed

    event, hidden, listed = anyio.run(scenario)
    assert isinstance(event, ToolsListChanged)
    assert hidden == [] and listed == []
    drifts = rig.kinds(ledger_dir, "tool.drift")
    assert sorted(d["data"]["tool"] for d in drifts) == ["echo", "wait"]  # one per tool


def test_drift_mid_session_handshake(tmp_path):
    """The same through a 2025-11-25 upstream's notifications/tools/list_changed: in memory
    (FakeUpstream.rug_pull), and over stdio with the probe's change tool."""
    fake = FakeUpstream(names=["echo"])
    specs = [
        rig.spec("l", fake.server, mode="legacy"),
        rig.spec("p", rig.probe({"PROBE_LOG": str(tmp_path / "probe.log")})),
    ]
    ledger_dir = tmp_path / "ledger"

    async def scenario():
        async with rig.proxied(ledger_dir, specs) as (client, gw):
            assert gw.upstreams["l"].protocol_version == "2025-11-25"
            assert gw.upstreams["p"].protocol_version == "2025-11-25"
            before = names((await client.list_tools()).tools)
            async with client.listen(tools_list_changed=True) as subscription:
                await fake.rug_pull()
                first = await rig.next_notice(subscription)
                answer = await client.call_tool("p__change", {})
                second = await rig.next_notice(subscription)
            after = names((await client.list_tools()).tools)
            return before, first, answer, second, after

    before, first, answer, second, after = anyio.run(scenario)
    assert "l__echo" in before and "p__wait" in before and "p__change" in before
    assert isinstance(first, ToolsListChanged) and isinstance(second, ToolsListChanged)
    assert answer.content[0].text == "changed"
    assert "l__echo" not in after and "p__wait" not in after
    assert "p__change" in after  # only wait's definition changed
    drifts = {
        (d["data"]["upstream"], d["data"]["tool"]) for d in rig.kinds(ledger_dir, "tool.drift")
    }
    assert drifts == {("l", "echo"), ("p", "wait")}


def test_failed_refresh_retries_on_timer(tmp_path, capfd):
    """After a failed refresh, serve lists the upstream again on the retry timer: 30 s, then
    doubling to the 5-minute cap (the sleep is replaced, so each interval is seen as asked but
    lasts milliseconds). Each attempt is bounded by connect_timeout_seconds. The first success
    exposes the approved tools again and resets the interval. A lost connection is never
    retried."""
    fake = FakeUpstream(names=["echo"])
    specs = [
        rig.spec("f", fake.server, timeout=0.5),
        rig.spec("crashy", rig.probe({"PROBE_LOG": str(tmp_path / "probe.log")})),
    ]
    ledger_dir = tmp_path / "ledger"
    asked = []

    async def scenario():
        async with rig.proxied(ledger_dir, specs) as (client, gw):
            assert "f__echo" in names((await client.list_tools()).tools)

            async def sleep(seconds):
                asked.append(seconds)
                if len(asked) == 3:
                    fake.fail_list, fake.list_delay = False, 3  # the 3rd attempt hangs
                elif len(asked) == 4:
                    fake.fail_list, fake.list_delay = True, 0
                elif len(asked) == 7:
                    fake.fail_list = False  # the 7th attempt succeeds
                await anyio.sleep(0.01)

            gw.sleep = sleep
            fake.fail_list = True
            async with client.listen(tools_list_changed=True) as subscription:
                assert names((await client.list_tools()).tools) == [
                    "crashy__" + n
                    for n in ("wait", "crash", "env", "fail", "rich", "invalid", "change")
                ]
                await rig.until(lambda: len(asked) >= 7 and not gw._retrying, seconds=30)
                back = await rig.next_notice(subscription)  # f__echo is exposed again
            exposed = names(await gw.exposed())
            first_run = list(asked)
            fake.fail_list = True  # a second run of failures starts at 30 s again
            assert names((await client.list_tools()).tools)[0].startswith("crashy__")
            await rig.until(lambda: len(asked) > len(first_run), seconds=30)
            fake.fail_list = False
            await rig.until(lambda: not gw._retrying, seconds=30)
            # A lost connection: hidden, recorded, and never put on the timer.
            await client.call_tool("crashy__crash", {})
            await rig.until(lambda: gw.upstreams["crashy"].client is None)
            await anyio.sleep(0.2)
            return exposed, first_run, list(asked), "crashy" in gw._retrying, back

    exposed, first_run, all_asked, crashy_retrying, back = anyio.run(scenario)
    assert first_run == [30, 60, 120, 240, 300, 300, 300]
    assert all_asked[len(first_run)] == 30  # reset after the success
    assert "f__echo" in exposed  # came back with no new approval
    assert isinstance(back, ToolsListChanged)
    assert not crashy_retrying
    failed = [
        (e["data"]["prefix"], e["data"]["trigger"])
        for e in rig.kinds(ledger_dir, "upstream.refresh_failed")
    ]
    # One record per run of failures: the timer's own failures are never the first of a run.
    assert failed == [("f", "client-list"), ("f", "client-list"), ("crashy", "connection-lost")]
    assert (
        "polarizer: upstream f: listing timed out; its tools are hidden" in capfd.readouterr().err
    )


def test_notice_only_when_exposed_list_changes(tmp_path):
    """An upstream notice that changes nothing exposed sends clients nothing, in either era:
    a notice with no change, and a change to a tool that isn't approved. A change to an
    approved tool does reach both."""
    echo = types.Tool(name="echo", description="Echo.", input_schema={"type": "object"})
    wait = types.Tool(name="wait", description="Wait.", input_schema={"type": "object"})
    fake = FakeUpstream(definitions=[echo, wait])
    ledger_dir = tmp_path / "ledger"
    legacy_notices = []

    async def on_message(message):
        if isinstance(message, types.ToolListChangedNotification):
            legacy_notices.append(message)

    async def scenario():
        async with rig.gateway(ledger_dir, [rig.spec("f", fake.server)], approve=False) as gw:
            # Approved before any client connects, so its own notice reaches no one.
            await approve(ledger_dir, "f", "echo", defhash.definition(echo)[0])
            await gw.catch_up()
            await rig.until(lambda: gw._announced is not None and len(gw._announced) == 1)
            async with (
                Client(gw.server, mode="auto") as modern,
                Client(gw.server, mode="legacy", message_handler=on_message) as legacy,
            ):
                for client in (modern, legacy):
                    assert names((await client.list_tools()).tools) == ["f__echo"]
                async with modern.listen(tools_list_changed=True) as subscription:
                    quiet = []
                    for change in ("nothing", "a pending tool"):
                        calls = fake.list_calls
                        if change == "a pending tool":
                            fake.definitions = [echo, wait.model_copy(update={"description": "W2"})]
                        await fake.bus.publish(ToolsListChanged())
                        await rig.until(lambda c=calls: fake.list_calls > c)
                        quiet.append(await rig.no_notice(subscription, 1.5))
                    assert not legacy_notices
                    fake.definitions = [echo.model_copy(update={"description": "E2"}), wait]
                    await fake.bus.publish(ToolsListChanged())
                    loud = await rig.next_notice(subscription)
                    await rig.until(lambda: legacy_notices)
                return quiet, loud

    quiet, loud = anyio.run(scenario)
    assert quiet == [True, True]
    assert isinstance(loud, ToolsListChanged) and len(legacy_notices) == 1
    assert [e["data"]["tool"] for e in rig.kinds(ledger_dir, "tool.seen")][-1] == "wait"


def test_notices_at_most_once_per_second(tmp_path):
    """Two decisions a moment apart give two notices, the second folded to the end of the
    first one's second: never two within a second."""
    fake = FakeUpstream(names=["echo", "wait"])
    ledger_dir = tmp_path / "ledger"

    async def scenario():
        async with rig.proxied(
            ledger_dir, [rig.spec("f", fake.server)], approve=False, watch_interval=0.05
        ) as (client, _):
            times = []
            async with client.listen(tools_list_changed=True) as subscription:
                await approve(ledger_dir, "f", "echo", hash_now(fake, "echo"))
                await rig.next_notice(subscription)
                times.append(anyio.current_time())
                await approve(ledger_dir, "f", "wait", hash_now(fake, "wait"))
                await rig.next_notice(subscription)
                times.append(anyio.current_time())
            return times, names((await client.list_tools()).tools)

    times, listed = anyio.run(scenario)
    assert times[1] - times[0] >= 0.95  # lower bound only: load can only make it later
    assert listed == ["f__echo", "f__wait"]
