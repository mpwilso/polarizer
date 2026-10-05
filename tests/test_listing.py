"""Listing: paging and its limits, and a fresh upstream list on every client tools/list
(docs/PROXY-SPEC.md, Listing)."""

import os

import anyio
from helpers import rig
from helpers.fakes import FakeUpstream


def _connected(ledger_dir):
    return {e["data"]["prefix"]: e["data"] for e in rig.kinds(ledger_dir, "upstream.connected")}


def test_paging_follows_cursor(tmp_path):
    fake = FakeUpstream(extra=[f"t{i}" for i in range(12)], page_size=3)

    async def scenario():
        async with rig.proxied(tmp_path / "ledger", [rig.spec("f", fake.server)]) as (client, _):
            return [t.name for t in (await client.list_tools()).tools]

    names = anyio.run(scenario)
    assert names == [f"f__{n}" for n in fake.names]
    assert _connected(tmp_path / "ledger")["f"]["tools"] == len(fake.names)


def test_paging_limits(tmp_path, monkeypatch):
    """100 pages and 1,000 tools are allowed; one more page or tool refuses the upstream.

    The 1,100 tools each get a stored copy and an approval, about 2,200 fsyncs, which say
    nothing about paging and cost minutes where fsync is slow (Windows CI). fsync is a no-op
    in this test only; test_writer and test_defhash cover durability."""
    fsyncs = []
    monkeypatch.setattr(os, "fsync", fsyncs.append)
    upstreams = {
        "pages100": FakeUpstream(names=[f"t{i}" for i in range(100)], page_size=1),
        "pages101": FakeUpstream(names=[f"t{i}" for i in range(101)], page_size=1),
        "tools1000": FakeUpstream(names=[f"t{i}" for i in range(1000)], page_size=500),
        "tools1001": FakeUpstream(names=[f"t{i}" for i in range(1001)], page_size=500),
        "onepage1001": FakeUpstream(names=[f"t{i}" for i in range(1001)]),
    }
    specs = [rig.spec(prefix, fake.server) for prefix, fake in upstreams.items()]

    async def scenario():
        async with rig.proxied(tmp_path / "ledger", specs) as (client, _):
            names = [t.name for t in (await client.list_tools()).tools]
            refused = await client.call_tool("pages101__t0", {})
            return names, refused

    names, refused = anyio.run(scenario)
    connected = _connected(tmp_path / "ledger")
    assert connected["pages100"]["tools"] == 100
    assert connected["tools1000"]["tools"] == 1000
    assert connected["pages101"] == {"prefix": "pages101", "error": "more than 100 pages"}
    assert connected["tools1001"] == {"prefix": "tools1001", "error": "more than 1000 tools"}
    assert connected["onepage1001"] == {"prefix": "onepage1001", "error": "more than 1000 tools"}
    assert len(names) == 1100
    assert {n.split("__")[0] for n in names} == {"pages100", "tools1000"}
    assert refused.is_error
    (entry,) = rig.kinds(tmp_path / "ledger", "call.refused")
    assert entry["data"]["reason"] == "upstream pages101 did not connect"
    assert len(fsyncs) > 2000  # the patch was in effect for the ledger and the copies


def test_every_client_list_refreshes(tmp_path):
    """Each client tools/list lists the upstream again, even when the upstream's TTL hint
    would let the SDK serve its cache, and even with no change notice."""
    fake = FakeUpstream(ttl_ms=60_000, listen=False)

    async def scenario():
        async with rig.proxied(tmp_path / "ledger", [rig.spec("f", fake.server)]) as (client, gw):
            before = fake.list_calls
            first = (await client.list_tools()).tools[0].description
            fake.version = 2  # changed behind the proxy's back, with no notice
            # The refresh saw the change: with pins, a changed definition is hidden.
            hidden = (await client.list_tools()).tools
            calls = fake.list_calls - before
            await rig.approve_pending(gw, changed=True)
            second = (await client.list_tools()).tools[0].description
            return calls, first, hidden, second

    calls, first, hidden, second = anyio.run(scenario)
    assert calls == 2
    assert hidden == []
    assert (first, second) == ("Definition v1", "Definition v2")


def test_refresh_failure_hides_the_upstream(tmp_path, capfd):
    """M0 kept the last good list after a failed refresh (docs/dev/STAGE2-NOTES.md, guess 2). With pins,
    the upstream's tools are hidden and the failure is recorded (docs/PIN-SPEC.md, section 6)."""
    fake = FakeUpstream()

    async def scenario():
        async with rig.proxied(tmp_path / "ledger", [rig.spec("f", fake.server)]) as (client, _):
            before = [t.name for t in (await client.list_tools()).tools]
            fake.page_size = 1  # now the listing pages, and with names this long...
            fake.names = [f"t{i}" for i in range(101)]  # ...it passes the page limit
            names = [t.name for t in (await client.list_tools()).tools]
            return before, names

    before, names = anyio.run(scenario)
    assert before[:2] == ["f__echo", "f__wait"]
    assert names == []
    assert (
        "polarizer: upstream f: listing failed (more than 100 pages); its tools are hidden"
        in capfd.readouterr().err
    )
    (failed,) = rig.kinds(tmp_path / "ledger", "upstream.refresh_failed")
    assert failed["data"]["trigger"] == "client-list"
    assert failed["data"]["error"] == "more than 100 pages"
