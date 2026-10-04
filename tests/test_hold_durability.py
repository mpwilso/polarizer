"""An allow made by another process is durable before serve acts on it (docs/HOLD-SPEC.md,
sections 5 and 6): the deciding writer fsyncs it under the lock, serve fsyncs the ledger itself
before forwarding, a decision from a real second process is acted on within a quarter-second
watch, and `holds` writes nothing."""

import os
import stat
import subprocess
import sys
import threading
from pathlib import Path

import anyio
import pytest
from helpers import rig
from helpers.fakes import FakeUpstream

from polarizer import cli
from polarizer.decisions import HoldDecider
from polarizer.holds import HoldState
from polarizer.writer import FileOps

FAST = {"watch_interval": 0.05, "hold_watch_interval": 0.05}
ARGS = {"n": 1}


def setup():
    fake = FakeUpstream()
    specs = [rig.spec("f", fake.server)]
    return fake, specs, rig.held(specs, echo="egress")


def _now():
    from datetime import UTC, datetime

    return datetime.now(UTC)


class HeldFsync(FileOps):
    """The deciding writer's file layer: once armed, its next fsync waits for `release`."""

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


def test_allow_fsynced_before_forward(tmp_path):
    """While the deciding writer's fsync is held, the upstream sees no call through ten watch
    intervals: that writer holds the ledger lock until its fsync returns, and serve reads new
    bytes only under the lock. Once released, the call is forwarded."""
    fake, specs, pol = setup()
    ld = tmp_path / "ledger"
    held = HeldFsync()

    def allow(hold_id):
        decider = HoldDecider.open(ld, ops=held)
        try:
            held.armed = True
            decider.decide(hold_id, "allow", None, lambda _: None, lambda _: None, _now())
        finally:
            decider.close()

    async def main():
        async with rig.proxied(ld, specs, policy=pol, **FAST) as (client, gw):
            results = {}
            async with anyio.create_task_group() as tasks:

                async def call():
                    results["a"] = await client.call_tool("f__echo", dict(ARGS))

                tasks.start_soon(call)
                [hold] = await rig.holds_created(ld, 1)
                thread = threading.Thread(target=allow, args=(hold,))
                thread.start()
                try:
                    await anyio.to_thread.run_sync(held.holding.wait, 30)
                    seen = []
                    for _ in range(10):
                        await anyio.sleep(0.05)
                        seen.append(len(fake.calls))
                finally:
                    held.release.set()
                    await anyio.to_thread.run_sync(thread.join, 30)
                await rig.until(lambda: "a" in results)
            return seen, results["a"]

    seen, result = anyio.run(main)
    assert seen == [0] * 10
    assert result.is_error is False and len(fake.calls) == 1


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


class RecordedHolds(HoldState):
    def __init__(self, events):
        super().__init__()
        self.events = events

    def apply(self, entry):
        if entry.get("kind") == "hold.decided":
            self.events.append("adopt")
        super().apply(entry)


class FailingFsync(FileOps):
    """The deciding writer's file layer: once armed, its next fsync raises after the write."""

    armed = False

    def fsync(self, fd):
        if self.armed:
            self.armed = False
            raise OSError(5, "Input/output error")
        super().fsync(fd)


@pytest.mark.parametrize("serve_fails", [False, True], ids=["serve-fsync-ok", "serve-fsync-fails"])
def test_serve_fsyncs_adopted_allow(tmp_path, serve_fails):
    """The deciding writer's fsync raises after its write, so the allow is in the file but
    maybe not durable. serve fsyncs the ledger itself, through its FileOps, right before the
    allow reaches its hold fold, and only then forwards. If that fsync raises too, nothing is
    forwarded, the writer stops, and the client gets the "could not record it" line."""
    fake, specs, pol = setup()
    ld = tmp_path / "ledger"
    events = []
    ops = Recorded(events)
    failing = FailingFsync()

    def allow(hold_id):
        decider = HoldDecider.open(ld, ops=failing)
        try:
            failing.armed = True
            try:
                decider.decide(hold_id, "allow", None, lambda _: None, lambda _: None, _now())
            except OSError:
                return "raised"
            return "allowed"
        finally:
            decider.close()

    async def main():
        state = RecordedHolds(events)
        async with rig.proxied(ld, specs, policy=pol, ops=ops, holds=state, **FAST) as (
            client,
            gw,
        ):
            results = {}
            async with anyio.create_task_group() as tasks:

                async def call():
                    results["a"] = await client.call_tool("f__echo", dict(ARGS))

                tasks.start_soon(call)
                [hold] = await rig.holds_created(ld, 1)
                await rig.until(lambda: not gw.writer._dirty)  # earlier records are synced
                ops.fail = serve_fails
                events.clear()
                outcome = await anyio.to_thread.run_sync(allow, hold)
                await rig.until(lambda: "a" in results)
            return outcome, results["a"], gw.writer.stopped

    outcome, result, stopped = anyio.run(main)
    assert outcome == "raised"
    assert len(rig.kinds(ld, "hold.decided")) == 1  # the line reached the file
    if serve_fails:
        assert result.content[0].text == (
            "polarizer: f__echo was not called: the ledger could not record it"
        )
        assert fake.calls == [] and "adopt" not in events
        assert "could not fsync an allow another process wrote" in stopped
        assert rig.kinds(ld, "call.sent") == []
    else:
        assert events[events.index("adopt") - 1] == "fsync"
        assert result.is_error is False and len(fake.calls) == 1 and stopped is None


def test_allow_from_second_process(tmp_path):
    """`python -m polarizer allow --allow-no-terminal` run as a subprocess while a gateway
    holds a call forwards it within 2 s of the command returning. The watch runs every 0.25 s
    while a hold is open, so 2 s is eight times what it needs."""
    fake, specs, pol = setup()
    ld = tmp_path / "ledger"

    def run_allow(hold_id):
        argv = [sys.executable, "-m", "polarizer", "allow", "--ledger-dir", str(ld), hold_id]
        argv.append("--allow-no-terminal")
        return subprocess.run(argv, stdin=subprocess.DEVNULL, capture_output=True, timeout=60)

    async def main():
        async with rig.proxied(ld, specs, policy=pol, watch_interval=1.0) as (client, gw):
            assert gw.hold_watch_interval == 0.25
            results = {}
            async with anyio.create_task_group() as tasks:

                async def call():
                    results["a"] = await client.call_tool("f__echo", dict(ARGS))

                tasks.start_soon(call)
                [hold] = await rig.holds_created(ld, 1)
                done = await anyio.to_thread.run_sync(run_allow, hold)
                assert done.returncode == 0, done.stderr
                await rig.until(lambda: "a" in results, 2)
            return hold, done, results["a"]

    hold, done, result = anyio.run(main)
    assert result.is_error is False and len(fake.calls) == 1
    out = done.stdout.decode()
    assert out.splitlines()[0].startswith(f"hold {hold} f__echo egress")
    assert out.splitlines()[-1].startswith(f"allowed hold {hold} at seq ")


def listing(root: Path) -> dict:
    out = {}
    for path in [root, *sorted(root.rglob("*"))]:
        st = os.lstat(path)
        out[str(path.relative_to(root))] = (st.st_size, st.st_mtime_ns, stat.S_IMODE(st.st_mode))
    return out


def test_allow_updates_head(tmp_path, capsys):
    """After `allow`, ledger.head names the decision's seq; `holds` changes nothing."""
    import json

    fake, specs, pol = setup()
    ld = tmp_path / "ledger"

    async def main():
        async with rig.proxied(ld, specs, policy=pol, **FAST) as (client, gw):
            results = {}
            async with anyio.create_task_group() as tasks:

                async def call():
                    results["a"] = await client.call_tool("f__echo", dict(ARGS))

                tasks.start_soon(call)
                [hold] = await rig.holds_created(ld, 1)
                before = listing(ld)
                assert cli.main(["holds", "--ledger-dir", str(ld)]) == 0
                after = listing(ld)
                await anyio.to_thread.run_sync(rig.decide_hold, ld, hold)
                head = json.loads((ld / "ledger.head").read_text())
                await rig.until(lambda: "a" in results)
            return before, after, head

    before, after, head = anyio.run(main)
    assert before == after
    assert "holds: 1 open" in capsys.readouterr().out
    assert head["seq"] == rig.kinds(ld, "hold.decided")[0]["seq"]
