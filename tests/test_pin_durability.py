"""A decision made by another process while serve runs: it is durable before anything acts on it,
serve fsyncs the ledger itself before acting on an adopted approval, the once-a-second watch
applies it, and the watch reads only bytes it hasn't seen (docs/PIN-SPEC.md, section 8)."""

import subprocess
import sys
import threading

import anyio
import pytest
from conftest import build_chain
from helpers import rig
from helpers.fakes import FakeUpstream
from mcp.shared.subscriptions import ToolsListChanged

from polarizer import cli, defhash
from polarizer.decisions import Decider
from polarizer.pins import PinState
from polarizer.writer import FileOps, LedgerWriter


def names(tools) -> list[str]:
    return [t.name for t in tools]


def echo_hash(fake: FakeUpstream) -> str:
    return defhash.definition(fake.tool("echo"))[0]


class HeldFsync(FileOps):
    """The approving writer's file layer: once armed, its next fsync waits for `release`."""

    def __init__(self):
        self.armed = False
        self.holding = threading.Event()
        self.release = threading.Event()

    def fsync(self, fd):
        if self.armed:
            self.armed = False
            self.holding.set()
            assert self.release.wait(30)
        super().fsync(fd)


def test_approval_fsynced_before_exposed(tmp_path):
    """While the approving writer's fsync is held, the tool stays hidden through ten watch
    intervals: the writer holds the ledger lock until its fsync returns, and serve reads new
    bytes only under the lock, so serve's catch-up waits. Once the fsync is released, the
    tool is exposed."""
    fake = FakeUpstream(names=["echo"])
    ledger_dir = tmp_path / "ledger"
    held = HeldFsync()

    def approve():
        decider = Decider.open(ledger_dir, ops=held)
        try:
            held.armed = True
            decider.approve_one("f", "echo", echo_hash(fake), lambda line: None)
        finally:
            decider.close()

    async def scenario():
        specs = [rig.spec("f", fake.server)]
        async with rig.gateway(ledger_dir, specs, approve=False, watch_interval=0.05) as gw:
            thread = threading.Thread(target=approve)
            thread.start()
            try:
                await anyio.to_thread.run_sync(held.holding.wait, 30)
                hidden = []
                for _ in range(10):
                    await anyio.sleep(0.05)
                    hidden.append(names(await gw.exposed()))
                pins_while_held = gw.pins.get("f", "echo").decision
            finally:
                held.release.set()
                await anyio.to_thread.run_sync(thread.join, 30)
            await rig.until(lambda: gw.pins.get("f", "echo").decision == "approved")
            return hidden, pins_while_held, names(await gw.exposed())

    hidden, pins_while_held, exposed = anyio.run(scenario)
    assert hidden == [[]] * 10 and pins_while_held is None
    assert exposed == ["f__echo"]


class Recorded(FileOps):
    """serve's file layer: records each fsync in `events`, and raises once `fail` is set."""

    def __init__(self, events):
        self.events = events
        self.fail = False

    def fsync(self, fd):
        self.events.append("fsync")
        if self.fail:
            raise OSError(5, "Input/output error")
        super().fsync(fd)


class RecordedPins(PinState):
    def __init__(self, events):
        super().__init__()
        self.events = events

    def apply(self, entry):
        if entry.get("kind") == "tool.approved":
            self.events.append("adopt")
        super().apply(entry)


class FailingFsync(FileOps):
    """The approving writer's file layer: once armed, its next fsync raises after the write."""

    armed = False

    def fsync(self, fd):
        if self.armed:
            self.armed = False
            raise OSError(5, "Input/output error")
        super().fsync(fd)


@pytest.mark.parametrize("serve_fails", [False, True], ids=["serve-fsync-ok", "serve-fsync-fails"])
def test_serve_fsyncs_adopted_approval(tmp_path, serve_fails):
    """The approving writer's fsync raises after its write, so the approval is in the file but
    maybe not durable. serve fsyncs the ledger itself, through its FileOps, right before the
    approval reaches its pin state, and then exposes the tool. If serve's fsync raises too,
    nothing is exposed and its writer stops."""
    fake = FakeUpstream(names=["echo"])
    ledger_dir = tmp_path / "ledger"
    events = []
    ops = Recorded(events)
    failing = FailingFsync()

    def approve():
        decider = Decider.open(ledger_dir, ops=failing)
        try:
            failing.armed = True
            try:
                decider.approve_one("f", "echo", echo_hash(fake), lambda line: None)
            except OSError:
                return "raised"
            return "approved"
        finally:
            decider.close()

    async def scenario():
        async with rig.gateway(
            ledger_dir,
            [rig.spec("f", fake.server)],
            approve=False,
            ops=ops,
            state=RecordedPins(events),
            watch_interval=0.05,
        ) as gw:
            await rig.until(lambda: not gw.writer._dirty)  # startup's records are synced
            ops.fail = serve_fails
            outcome = await anyio.to_thread.run_sync(approve)
            if serve_fails:
                await rig.until(lambda: gw.writer.stopped)
            else:
                await rig.until(lambda: "adopt" in events)
            return outcome, names(await gw.exposed()), gw.writer.stopped

    outcome, exposed, stopped = anyio.run(scenario)
    assert outcome == "raised"
    approvals = [e for e in rig.entries(ledger_dir) if e["kind"] == "tool.approved"]
    assert len(approvals) == 1  # the line reached the file although its fsync failed
    if serve_fails:
        assert exposed == [] and "adopt" not in events
        assert "could not fsync an approval another process wrote" in stopped
    else:
        assert events[events.index("adopt") - 1] == "fsync"
        assert exposed == ["f__echo"] and stopped is None


def test_approve_from_second_process(tmp_path):
    """`python -m polarizer approve` run as a subprocess while a gateway serves exposes the
    tool within 2 s of the command returning, and the client receives a change notice. The
    watch runs every 0.25 s here (1 s by default), so 2 s is eight times what it needs."""
    fake = FakeUpstream(names=["echo"])
    ledger_dir = tmp_path / "ledger"
    argv = [sys.executable, "-m", "polarizer", "approve", "--ledger-dir", str(ledger_dir)]
    argv += ["f", "echo", echo_hash(fake), "--allow-no-terminal"]

    def run_approve():
        return subprocess.run(argv, stdin=subprocess.DEVNULL, capture_output=True, timeout=60)

    async def scenario():
        async with rig.proxied(
            ledger_dir, [rig.spec("f", fake.server)], approve=False, watch_interval=0.25
        ) as (client, gw):
            assert (await client.list_tools()).tools == []
            async with client.listen(tools_list_changed=True) as subscription:
                done = await anyio.to_thread.run_sync(run_approve)
                assert done.returncode == 0, done.stderr
                event = await rig.next_notice(subscription, 2)
                exposed = names(await gw.exposed())
            return event, exposed, names((await client.list_tools()).tools)

    event, exposed, listed = anyio.run(scenario)
    assert isinstance(event, ToolsListChanged)
    assert exposed == listed == ["f__echo"]


class CountingOps(FileOps):
    def __init__(self):
        self.bytes_read = 0
        self.sizes = 0

    def size(self, fd):
        self.sizes += 1
        return super().size(fd)

    def read_at(self, fd, offset, length):
        data = super().read_at(fd, offset, length)
        self.bytes_read += len(data)
        return data


def test_watch_reads_only_new_bytes(tmp_path):
    """The watch reads nothing while the file hasn't grown, and exactly the new bytes when it
    has, at 100 and at 10,000 entries."""
    counts = {}
    for size in (100, 10_000):
        directory = tmp_path / f"ledger-{size}"
        build_chain(directory, [("note", {"i": i}) for i in range(size - 1)], head_at=0)
        ops = CountingOps()

        async def scenario(directory=directory, ops=ops):
            async with rig.gateway(directory, [], approve=False, ops=ops, watch_interval=0.01):
                await anyio.sleep(0.1)  # let startup's own appends settle
                ops.bytes_read, ops.sizes = 0, 0
                await rig.until(lambda: ops.sizes >= 10)  # ten watch ticks
                idle = ops.bytes_read
                before = (directory / "ledger.jsonl").stat().st_size
                other = LedgerWriter.open(directory, idle_fsync=False)
                try:
                    for i in range(3):
                        other.append("note", {"from": "another process", "i": i}).result()
                finally:
                    other.close()
                grown = (directory / "ledger.jsonl").stat().st_size - before
                await rig.until(lambda: ops.bytes_read >= grown)
                ticks = ops.sizes
                await rig.until(lambda: ops.sizes >= ticks + 10)  # ten more, with nothing new
                return idle, grown, ops.bytes_read

        counts[size] = anyio.run(scenario)
    for size, (idle, grown, read) in counts.items():
        assert idle == 0, size
        assert read == grown, size  # three entries' bytes, at either size


class RejectionFails(PinState):
    """serve's pin state, with a fold that raises on a rejection."""

    def apply(self, entry):
        if entry.get("kind") == "tool.rejected":
            raise RuntimeError("fold broke")
        super().apply(entry)


def test_rejection_that_cannot_fold_stops_exposing(tmp_path, capfd):
    """Another process rejects an approved tool, and serve's fold of that rejection raises.
    Pin state would still say approved, so the writer stops: the first list after the
    rejection is empty, the call is refused as unrecordable, the client is told once, and the
    stop is one line on stderr. The watch is slowed so the client's own list does the
    catch-up."""
    fake = FakeUpstream(names=["echo"])
    ledger_dir = tmp_path / "ledger"
    specs = [rig.spec("f", fake.server)]
    options = {"state": RejectionFails(), "watch_interval": 3600}

    async def scenario():
        async with rig.proxied(ledger_dir, specs, **options) as (c, gw):
            before = names((await c.list_tools()).tools)
            async with c.listen(tools_list_changed=True) as subscription:
                argv = ["reject", "--ledger-dir", str(ledger_dir), "f", "echo", echo_hash(fake)]
                argv += ["--reason", "r", "--allow-no-terminal"]
                assert await anyio.to_thread.run_sync(cli.main, argv) == 0
                after = names((await c.list_tools()).tools)
                stopped = gw.writer.stopped
                refused = await c.call_tool("f__echo", {})
                assert isinstance(await rig.next_notice(subscription), ToolsListChanged)
                quiet = await rig.no_notice(subscription)
                again = names((await c.list_tools()).tools)
            return before, after, stopped, refused, quiet, again, names(await gw.exposed())

    before, after, stopped, refused, quiet, again, exposed = anyio.run(scenario)
    seq = rig.kinds(ledger_dir, "tool.rejected")[0]["seq"]
    line = f"polarizer: stopped writing the ledger: could not fold seq {seq} into pin state: "
    assert before == ["f__echo"]
    assert after == [] and again == [] and exposed == []
    assert stopped == line + "fold broke"
    assert refused.content[0].text == (
        "polarizer: f__echo was not called: the ledger could not record it"
    )
    assert quiet
    err = capfd.readouterr().err.splitlines()
    assert err.count(stopped) == 1
    assert f"polarizer: refused f__echo: could not record it: {stopped}" in err
    assert not rig.kinds(ledger_dir, "call.sent")
