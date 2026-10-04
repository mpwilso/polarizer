"""Holds in a running gateway (docs/HOLD-SPEC.md, sections 5 and 6): what is held stays away
from the upstream until a person allows it, every ending is recorded once, and what is forwarded
after an allow is what the side file holds. In memory, with FakeUpstream."""

import json
import os
import threading
from pathlib import Path

import anyio
import pytest
from conftest import build_chain
from helpers import rig
from helpers.fakes import FakeUpstream

from polarizer import config, defhash, holds, proxy
from polarizer.decisions import Decider, HoldDecider, Refusal
from polarizer.ledger import verify_bytes
from polarizer.policy import Policy
from polarizer.writer import FileOps, LedgerWriter

FAST = {"watch_interval": 0.05, "hold_watch_interval": 0.05}
NOT_ALLOWED = "polarizer: f__echo was not allowed"
UNRECORDABLE = "polarizer: f__echo was not called: the ledger could not record it"
ARGS = {"n": 1, "text": "caf" + chr(0xE9) + chr(0x7F)}


def setup(**classes):
    """A fake upstream "f" whose echo is egress (held on every call) unless `classes` says
    otherwise, and whose other tools are local-read (never held)."""
    fake = FakeUpstream()
    specs = [rig.spec("f", fake.server)]
    return fake, specs, rig.held(specs, **{"echo": "egress", **classes})


class Calls:
    """Calls made in the background of a task group, each in a cancel scope of its own."""

    def __init__(self, client, tasks):
        self.client, self.tasks = client, tasks
        self.results: dict = {}
        self.scopes: dict = {}

    def start(self, key, name="f__echo", arguments=None):
        scope = anyio.CancelScope()
        self.scopes[key] = scope

        async def call():
            with scope:
                self.results[key] = await self.client.call_tool(name, arguments or dict(ARGS))

        self.tasks.start_soon(call)

    async def result(self, key, seconds: float = 10):
        await rig.until(lambda: key in self.results, seconds)
        return self.results[key]


def text(result) -> str:
    return result.content[0].text


def entries_for(ledger_dir: Path, hold: str) -> list[tuple[str, dict]]:
    """(kind, data) of every entry that names the hold, after its hold.created."""
    found = []
    for e in rig.entries(ledger_dir):
        if e["data"].get("hold") == hold:
            found.append((e["kind"], e["data"]))
    return found


def test_held_call_does_not_reach_upstream_until_allowed(tmp_path):
    fake, specs, pol = setup()
    ld = tmp_path / "ledger"

    async def main():
        async with rig.proxied(ld, specs, policy=pol, **FAST) as (client, gw):
            async with anyio.create_task_group() as tasks:
                calls = Calls(client, tasks)
                calls.start("a")
                [hold] = await rig.holds_created(ld, 1)
                await anyio.sleep(0.5)  # ten watch intervals
                assert fake.calls == [] and "a" not in calls.results
                out, err = await anyio.to_thread.run_sync(rig.decide_hold, ld, hold)
                result = await calls.result("a")
            return hold, result, out, err

    hold, result, out, err = anyio.run(main)
    assert [(name, args) for name, args, _ in fake.calls] == [("echo", ARGS)]
    assert result.is_error is False
    assert text(result) == json.dumps(ARGS, sort_keys=True)
    assert out[-1].startswith(f"allowed hold {hold} at seq ")
    kinds = [k for k, _ in entries_for(ld, hold)]
    assert kinds == ["hold.created", "hold.decided", "call.sent"]
    sent = rig.kinds(ld, "call.sent")[-1]["data"]
    created = rig.kinds(ld, "hold.created")[0]["data"]
    assert (sent["hold"], sent["allowed_by"], sent["args_commit"]) == (
        hold,
        "hold",
        created["args_commit"],
    )
    assert created["rule"] == "egress" and created["class"] == "egress"
    assert (
        rig.kinds(ld, "call.returned")[-1]["data"]["call_seq"]
        == rig.kinds(ld, "call.sent")[-1]["seq"]
    )


def test_deny_never_reaches_upstream(tmp_path):
    fake, specs, pol = setup()
    ld = tmp_path / "ledger"

    async def main():
        async with rig.proxied(ld, specs, policy=pol, **FAST) as (client, gw):
            async with anyio.create_task_group() as tasks:
                calls = Calls(client, tasks)
                calls.start("a")
                [hold] = await rig.holds_created(ld, 1)
                await anyio.to_thread.run_sync(rig.decide_hold, ld, hold, "deny", "no thanks")
                return hold, await calls.result("a")

    hold, result = anyio.run(main)
    assert text(result) == NOT_ALLOWED and result.is_error
    assert fake.calls == []
    trail = entries_for(ld, hold)
    assert [k for k, _ in trail] == ["hold.created", "hold.decided", "call.refused"]
    assert trail[1][1]["decision"] == "deny" and trail[1][1]["reason"] == "no thanks"
    assert trail[2][1]["reason"] == f"hold {hold} was denied"


def test_expiry_never_reaches_upstream(tmp_path):
    fake, specs, pol = setup()
    ld = tmp_path / "ledger"

    async def main():
        async with rig.proxied(ld, specs, policy=pol, hold_timeout=0.2, **FAST) as (client, gw):
            result = await client.call_tool("f__echo", dict(ARGS))
            return rig.hold_ids(ld)[0], result

    hold, result = anyio.run(main)
    assert text(result) == NOT_ALLOWED and fake.calls == []
    trail = entries_for(ld, hold)
    assert [k for k, _ in trail] == ["hold.created", "hold.expired", "call.refused"]
    # The ledger records the policy's timeout; 0.2 s is the test's injected wait.
    assert trail[1][1]["reason"] == "timeout after 300 s"
    assert trail[2][1]["reason"] == f"hold {hold} expired: timeout after 300 s"


def test_unclassified_tool_is_held(tmp_path):
    fake, specs, pol = setup()
    del pol.upstreams["f"].tools["echo"]
    ld = tmp_path / "ledger"

    async def main():
        async with rig.proxied(ld, specs, policy=pol, hold_timeout=0.1, **FAST) as (client, gw):
            return await client.call_tool("f__echo", {})

    assert text(anyio.run(main)) == NOT_ALLOWED
    created = rig.kinds(ld, "hold.created")[0]["data"]
    assert (created["rule"], created["class"], created["class_from"]) == (
        "unclassified",
        None,
        None,
    )
    assert created["reason"] == "f__echo has no class in polarizer.toml"


def test_decision_is_bound_to_the_arguments(tmp_path, capsys):
    """Two calls with different arguments are two holds; allowing the first forwards only its
    arguments; a hand-written decision naming the second with the first's args_commit is
    ignored, with one stderr line, and the second keeps waiting."""
    fake, specs, pol = setup()
    ld = tmp_path / "ledger"

    async def main():
        async with rig.proxied(ld, specs, policy=pol, **FAST) as (client, gw):
            async with anyio.create_task_group() as tasks:
                calls = Calls(client, tasks)
                calls.start("one", arguments={"n": 1})
                await rig.holds_created(ld, 1)
                calls.start("two", arguments={"n": 2})
                first, second = await rig.holds_created(ld, 2)
                await anyio.to_thread.run_sync(rig.decide_hold, ld, first)
                await calls.result("one")
                commit = rig.kinds(ld, "hold.created")[0]["data"]["args_commit"]
                other = LedgerWriter.open(ld)
                other.append(
                    "hold.decided",
                    {"hold": second, "args_commit": commit, "decision": "allow",
                     "actor": "person", "reason": None},
                ).result()  # fmt: skip
                other.close()
                await rig.until(lambda: "ignored a decision" in capsys.readouterr().err)
                await anyio.sleep(0.3)
                assert second in gw._open and "two" not in calls.results
                await anyio.to_thread.run_sync(rig.decide_hold, ld, second, "deny")
                return await calls.result("two")

    second_result = anyio.run(main)
    assert [args for _, args, _ in fake.calls] == [{"n": 1}]
    assert text(second_result) == NOT_ALLOWED


def test_ignored_decision_line(tmp_path, capsys):
    """The stderr line for a decision with another args_commit, exactly, and only once."""
    fake, specs, pol = setup()
    ld = tmp_path / "ledger"

    async def main():
        async with rig.proxied(ld, specs, policy=pol, **FAST) as (client, gw):
            async with anyio.create_task_group() as tasks:
                calls = Calls(client, tasks)
                calls.start("a")
                [hold] = await rig.holds_created(ld, 1)
                capsys.readouterr()
                other = LedgerWriter.open(ld)
                other.append(
                    "hold.decided",
                    {"hold": hold, "args_commit": "0" * 64, "decision": "allow",
                     "actor": "person", "reason": None},
                ).result()  # fmt: skip
                other.close()
                await anyio.sleep(0.5)
                err = capsys.readouterr().err
                await anyio.to_thread.run_sync(rig.decide_hold, ld, hold, "deny")
                await calls.result("a")
            return hold, err

    hold, err = anyio.run(main)
    line = f"polarizer: ignored a decision for hold {hold} with another args_commit\n"
    assert err.count(line) == 1


def test_decision_is_single_use(tmp_path):
    """A second allow or deny of a decided hold is refused and writes nothing; a hand-appended
    second hold.decided has no effect; a retry of the same call is a new hold."""
    fake, specs, pol = setup()
    ld = tmp_path / "ledger"

    async def main():
        async with rig.proxied(ld, specs, policy=pol, **FAST) as (client, gw):
            async with anyio.create_task_group() as tasks:
                calls = Calls(client, tasks)
                calls.start("a")
                [hold] = await rig.holds_created(ld, 1)
                await anyio.to_thread.run_sync(rig.decide_hold, ld, hold)
                await calls.result("a")
                before = (ld / "ledger.jsonl").read_bytes()
                seq = rig.kinds(ld, "hold.decided")[0]["seq"]
                for decision in ("allow", "deny"):
                    with pytest.raises(Refusal) as refused:
                        await anyio.to_thread.run_sync(rig.decide_hold, ld, hold, decision)
                    assert refused.value.line == (
                        f"polarizer: hold {hold} was already decided at seq {seq}: allow"
                    )
                assert (ld / "ledger.jsonl").read_bytes() == before
                commit = rig.kinds(ld, "hold.created")[0]["data"]["args_commit"]
                other = LedgerWriter.open(ld)
                other.append(
                    "hold.decided",
                    {"hold": hold, "args_commit": commit, "decision": "deny",
                     "actor": "person", "reason": None},
                ).result()  # fmt: skip
                other.close()
                calls.start("retry")
                _, retry = await rig.holds_created(ld, 2)
                await anyio.sleep(0.3)
                assert "retry" not in calls.results  # the old decision doesn't cover it
                await anyio.to_thread.run_sync(rig.decide_hold, ld, retry, "deny")
                await calls.result("retry")
            return hold, retry

    hold, retry = anyio.run(main)
    assert retry != hold
    folded = holds.HoldState()
    verify_bytes((ld / "ledger.jsonl").read_bytes(), None, folded.apply)
    assert folded.get(hold).ending.decision == "allow"
    assert len(fake.calls) == 1


def test_decision_for_another_session_is_ignored(tmp_path):
    """Two gateways on one ledger, each with a held call: allowing the first's hold forwards
    only through the first, and the second's stays held."""
    one, two = FakeUpstream(), FakeUpstream()
    ld = tmp_path / "ledger"
    specs1, specs2 = [rig.spec("f", one.server)], [rig.spec("f", two.server)]

    async def main():
        async with rig.proxied(ld, specs1, policy=rig.held(specs1, echo="egress"), **FAST) as (
            c1,
            g1,
        ):
            async with rig.proxied(ld, specs2, policy=rig.held(specs2, echo="egress"), **FAST) as (
                c2,
                g2,
            ):
                async with anyio.create_task_group() as tasks:
                    calls1, calls2 = Calls(c1, tasks), Calls(c2, tasks)
                    calls1.start("a")
                    calls2.start("b")
                    await rig.holds_created(ld, 2)
                    by_session = {
                        e["data"]["session"]: e["data"]["hold"]
                        for e in rig.kinds(ld, "hold.created")
                    }
                    first, second = by_session[g1.session], by_session[g2.session]
                    await anyio.to_thread.run_sync(rig.decide_hold, ld, first)
                    await calls1.result("a")
                    await anyio.sleep(0.3)
                    assert second in g2._open and "b" not in calls2.results
                    await anyio.to_thread.run_sync(rig.decide_hold, ld, second, "deny")
                    await calls2.result("b")

    anyio.run(main)
    assert len(one.calls) == 1 and two.calls == []


def test_two_holds_decided_out_of_order(tmp_path):
    """Holds A then B; deny B, then allow A: B is refused first, A forwarded after, each with
    one ending and one terminal entry."""
    fake, specs, pol = setup()
    ld = tmp_path / "ledger"

    async def main():
        async with rig.proxied(ld, specs, policy=pol, **FAST) as (client, gw):
            async with anyio.create_task_group() as tasks:
                calls = Calls(client, tasks)
                calls.start("A", arguments={"which": "A"})
                await rig.holds_created(ld, 1)
                calls.start("B", arguments={"which": "B"})
                a, b = await rig.holds_created(ld, 2)
                await anyio.to_thread.run_sync(rig.decide_hold, ld, b, "deny")
                result_b = await calls.result("B")
                assert "A" not in calls.results
                await anyio.to_thread.run_sync(rig.decide_hold, ld, a)
                return a, b, result_b, await calls.result("A")

    a, b, result_b, result_a = anyio.run(main)
    assert text(result_b) == NOT_ALLOWED and result_a.is_error is False
    assert [args for _, args, _ in fake.calls] == [{"which": "A"}]
    assert [k for k, _ in entries_for(ld, a)] == ["hold.created", "hold.decided", "call.sent"]
    assert [k for k, _ in entries_for(ld, b)] == ["hold.created", "hold.decided", "call.refused"]


def test_client_cancel_ends_the_hold(tmp_path):
    fake, specs, pol = setup()
    ld = tmp_path / "ledger"

    async def main():
        async with rig.proxied(ld, specs, policy=pol, **FAST) as (client, gw):
            async with anyio.create_task_group() as tasks:
                calls = Calls(client, tasks)
                calls.start("a")
                [hold] = await rig.holds_created(ld, 1)
                calls.scopes["a"].cancel()
                await rig.until(lambda: rig.kinds(ld, "call.refused"))
                return hold

    hold = anyio.run(main)
    trail = entries_for(ld, hold)
    assert [k for k, _ in trail] == ["hold.created", "hold.expired", "call.refused"]
    assert trail[1][1]["reason"] == "the client cancelled the call"
    assert trail[2][1]["reason"] == f"hold {hold} expired: the client cancelled the call"
    assert fake.calls == []


def test_cancel_after_allow_before_forward(tmp_path):
    fake, specs, pol = setup()
    ld = tmp_path / "ledger"

    async def main():
        async with rig.proxied(ld, specs, policy=pol, **FAST) as (client, gw):
            entered = anyio.Event()

            async def hold_back(hold_id):
                entered.set()
                await anyio.sleep_forever()

            gw.before_forward = hold_back
            async with anyio.create_task_group() as tasks:
                calls = Calls(client, tasks)
                calls.start("a")
                [hold] = await rig.holds_created(ld, 1)
                await anyio.to_thread.run_sync(rig.decide_hold, ld, hold)
                with anyio.fail_after(10):
                    await entered.wait()
                calls.scopes["a"].cancel()
                await rig.until(lambda: rig.kinds(ld, "call.refused"))
                return hold

    hold = anyio.run(main)
    trail = entries_for(ld, hold)
    assert [k for k, _ in trail] == ["hold.created", "hold.decided", "call.refused"]
    assert trail[2][1]["reason"] == "the client cancelled the call before it was forwarded"
    assert fake.calls == []


class HeldFsync(FileOps):
    """Once armed, the next fsync waits for `release` (the deciding writer holds the lock)."""

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


class HeldExpiry(FileOps):
    """Once armed, writing a hold.expired line waits for `release`, under the ledger lock."""

    def __init__(self):
        self.armed = False
        self.holding = threading.Event()
        self.release = threading.Event()

    def write(self, fd, data):
        if self.armed and b'"kind":"hold.expired"' in data:
            self.armed = False
            self.holding.set()
            assert self.release.wait(30)
        super().write(fd, data)


def test_allow_racing_timeout_decision_first(tmp_path):
    """The allow is written, and its fsync held, when the timeout fires: serve's conditional
    append waits for the lock, then finds the allow, so it writes no hold.expired and
    forwards the call."""
    fake, specs, pol = setup()
    ld = tmp_path / "ledger"
    held = HeldFsync()

    def decide(hold_id):
        decider = HoldDecider.open(ld, ops=held)
        try:
            held.armed = True
            decider.decide(hold_id, "allow", None, lambda _: None, lambda _: None, _now())
        finally:
            decider.close()

    async def main():
        async with rig.proxied(ld, specs, policy=pol, hold_timeout=0.5, **FAST) as (client, gw):
            async with anyio.create_task_group() as tasks:
                tried = []
                expire = gw._expire

                async def recorded(waiter, reason):
                    tried.append(reason)
                    return await expire(waiter, reason)

                gw._expire = recorded
                calls = Calls(client, tasks)
                calls.start("a")
                [hold] = await rig.holds_created(ld, 1)
                tasks.start_soon(anyio.to_thread.run_sync, decide, hold)
                await rig.until(held.holding.is_set)
                await anyio.sleep(1.0)  # past the timeout: serve now waits for the lock
                held.release.set()
                result = await calls.result("a")
                # The timeout fired while the allow's fsync was held, and its conditional
                # append found the allow: no hold.expired.
                assert tried == ["timeout after 300 s"]
                return hold, result

    hold, result = anyio.run(main)
    assert result.is_error is False and len(fake.calls) == 1
    assert [k for k, _ in entries_for(ld, hold)] == ["hold.created", "hold.decided", "call.sent"]


def test_allow_racing_timeout_timeout_first(tmp_path):
    """serve's hold.expired is being written, under the lock, when the person allows: the
    allow waits for the lock, then finds the expiry and is refused; the call is refused."""
    fake, specs, pol = setup()
    ld = tmp_path / "ledger"
    serve_ops = HeldExpiry()
    outcome = {}

    def decide(decider, hold_id):
        try:
            decider.decide(hold_id, "allow", None, lambda _: None, lambda _: None, _now())
            outcome["result"] = "allowed"
        except Refusal as e:
            outcome["result"] = e.line
        finally:
            decider.close()

    async def main():
        async with rig.proxied(ld, specs, policy=pol, hold_timeout=0.5, ops=serve_ops, **FAST) as (
            client,
            gw,
        ):
            async with anyio.create_task_group() as tasks:
                calls = Calls(client, tasks)
                serve_ops.armed = True
                calls.start("a")
                [hold] = await rig.holds_created(ld, 1)
                decider = await anyio.to_thread.run_sync(HoldDecider.open, ld)
                await rig.until(serve_ops.holding.is_set)
                tasks.start_soon(anyio.to_thread.run_sync, decide, decider, hold)
                await anyio.sleep(0.5)  # the allow now waits for the lock
                serve_ops.release.set()
                result = await calls.result("a")
                await rig.until(lambda: "result" in outcome)
                return hold, result

    hold, result = anyio.run(main)
    assert text(result) == NOT_ALLOWED and fake.calls == []
    expired = rig.kinds(ld, "hold.expired")[0]
    assert outcome["result"] == (
        f"polarizer: hold {hold} expired at seq {expired['seq']}: timeout after 300 s"
    )
    assert [k for k, _ in entries_for(ld, hold)] == ["hold.created", "hold.expired", "call.refused"]


def _now():
    from datetime import UTC, datetime

    return datetime.now(UTC)


def echo_hash(fake: FakeUpstream) -> str:
    return defhash.definition(fake.tool("echo"))[0]


def test_reroute_after_allow(tmp_path):
    """A tool rejected while its call is held is refused after the allow, with M1a's reason,
    the hold's id, and M1a's client line."""
    fake, specs, pol = setup()
    ld = tmp_path / "ledger"

    def reject():
        decider = Decider.open(ld)
        try:
            decider.reject("f", "echo", echo_hash(fake), "it changed its mind", lambda _: None)
        finally:
            decider.close()

    async def main():
        async with rig.proxied(ld, specs, policy=pol, **FAST) as (client, gw):
            async with anyio.create_task_group() as tasks:
                calls = Calls(client, tasks)
                calls.start("a")
                [hold] = await rig.holds_created(ld, 1)
                await anyio.to_thread.run_sync(reject)
                await anyio.to_thread.run_sync(rig.decide_hold, ld, hold)
                return hold, await calls.result("a")

    hold, result = anyio.run(main)
    assert text(result) == "polarizer: f__echo is not available: rejected"
    trail = entries_for(ld, hold)
    assert [k for k, _ in trail] == ["hold.created", "hold.decided", "call.refused"]
    assert trail[2][1]["reason"] == 'upstream f tool "echo" was rejected'
    assert fake.calls == []


def test_writer_stop_ends_waiting_holds(tmp_path):
    """A line that doesn't chain, appended while two calls are held: both clients get the
    "could not record it" line, nothing is forwarded, and nothing more is written."""
    fake, specs, pol = setup()
    ld = tmp_path / "ledger"

    async def main():
        async with rig.proxied(ld, specs, policy=pol, **FAST) as (client, gw):
            async with anyio.create_task_group() as tasks:
                calls = Calls(client, tasks)
                calls.start("a")
                await rig.holds_created(ld, 1)
                calls.start("b")
                await rig.holds_created(ld, 2)
                with open(ld / "ledger.jsonl", "ab") as f:
                    f.write(b'{"not":"a chained entry"}\n')
                after = (ld / "ledger.jsonl").read_bytes()
                results = [await calls.result("a"), await calls.result("b")]
                return results, after

    results, after = anyio.run(main)
    assert [text(r) for r in results] == [UNRECORDABLE, UNRECORDABLE]
    assert fake.calls == []
    assert (ld / "ledger.jsonl").read_bytes() == after


def test_too_many_holds(tmp_path):
    """With 16 holds open, a 17th call that would be held is refused at once, with no side
    file and no hold.created; a call that would run still runs."""
    fake, specs, pol = setup()
    ld = tmp_path / "ledger"

    async def main():
        async with rig.proxied(ld, specs, policy=pol, **FAST) as (client, gw):
            async with anyio.create_task_group() as tasks:
                calls = Calls(client, tasks)
                for i in range(16):
                    calls.start(i, arguments={"i": i})
                await rig.holds_created(ld, 16)
                side_files = len(os.listdir(ld / "args"))
                seventeenth = await client.call_tool("f__echo", {"i": 16})
                after = len(os.listdir(ld / "args"))
                runs = await client.call_tool("f__fail", {})
                for scope in calls.scopes.values():
                    scope.cancel()
                return side_files, after, seventeenth, runs

    side_files, after, seventeenth, runs = anyio.run(main)
    assert side_files == after == 16
    assert text(seventeenth) == NOT_ALLOWED
    assert len(rig.kinds(ld, "hold.created")) == 16
    refused = [e["data"] for e in rig.kinds(ld, "call.refused") if "hold" not in e["data"]]
    assert refused == [
        {"session": refused[0]["session"], "tool": "f__echo", "reason": proxy.TOO_MANY}
    ]
    assert text(runs) == "it failed" and [n for n, _, _ in fake.calls] == ["fail"]


def test_model_line_has_no_details(tmp_path):
    """For deny, timeout and too-many-holds, the client's text is exactly the one line, with no
    rule, reason, path or deny reason."""
    fake, specs, pol = setup()
    ld = tmp_path / "ledger"
    path_arg = {"path": "/secret/place"}

    async def main():
        texts = []
        async with rig.proxied(ld, specs, policy=pol, hold_timeout=0.3, **FAST) as (client, gw):
            texts.append(text(await client.call_tool("f__echo", path_arg)))  # timeout
            async with anyio.create_task_group() as tasks:
                calls = Calls(client, tasks)
                calls.start("deny", arguments=path_arg)
                [_, hold] = await rig.holds_created(ld, 2)
                await anyio.to_thread.run_sync(rig.decide_hold, ld, hold, "deny", "SECRET-REASON")
                texts.append(text(await calls.result("deny")))
        return texts

    texts = anyio.run(main)
    assert texts == [NOT_ALLOWED, NOT_ALLOWED]

    async def full():
        """The cap (test_too_many_holds runs 16 real holds): a table of 16 open holds."""
        async with rig.proxied(ld, specs, policy=pol, **FAST) as (client, gw):
            gw._open.update({f"{i:016x}": None for i in range(16)})
            result = await client.call_tool("f__echo", path_arg)
            gw._open.clear()
            return text(result)

    assert anyio.run(full) == NOT_ALLOWED
    for line in texts:
        for detail in ("egress", "SECRET", "/secret", "timeout", "denied", "hold"):
            assert detail not in line


def _run_every_ending(ld: Path, fake: FakeUpstream, specs, pol) -> holds.HoldState:
    """One gateway on `ld` producing every ending a live process can produce in stage 6:
    allow and forward, deny, timeout, the client's cancel, the client's cancel after an
    allow, an allow refused by routing, and an allow refused by its side file. Returns the
    gateway's hold fold."""

    async def main():
        state = holds.HoldState()
        async with rig.proxied(ld, specs, policy=pol, holds=state, hold_timeout=3.0, **FAST) as (
            client,
            gw,
        ):
            hooks = {}

            async def before(hold_id):
                if hold_id in hooks:
                    await hooks[hold_id](hold_id)

            gw.before_forward = before
            async with anyio.create_task_group() as tasks:
                calls = Calls(client, tasks)

                async def next_hold(key, **kw):
                    n = len(rig.hold_ids(ld))
                    calls.start(key, **kw)
                    return (await rig.holds_created(ld, n + 1))[-1]

                h = await next_hold("allow")
                await anyio.to_thread.run_sync(rig.decide_hold, ld, h)
                await calls.result("allow")
                h = await next_hold("deny")
                await anyio.to_thread.run_sync(rig.decide_hold, ld, h, "deny")
                await calls.result("deny")
                h = await next_hold("timeout")
                await calls.result("timeout")
                h = await next_hold("cancel")
                calls.scopes["cancel"].cancel()
                await rig.until(lambda: len(rig.kinds(ld, "call.refused")) == 3)
                entered = anyio.Event()

                async def wait_forever(_):
                    entered.set()
                    await anyio.sleep_forever()

                h = await next_hold("late")
                hooks[h] = wait_forever
                await anyio.to_thread.run_sync(rig.decide_hold, ld, h)
                await entered.wait()
                calls.scopes["late"].cancel()
                await rig.until(lambda: len(rig.kinds(ld, "call.refused")) == 4)

                async def alter(hold_id):
                    commit = rig.kinds(ld, "hold.created")[-1]["data"]["args_commit"]
                    path = ld / "args" / f"{commit}.bin"
                    path.write_bytes(path.read_bytes() + b" ")

                h = await next_hold("altered")
                hooks[h] = alter
                await anyio.to_thread.run_sync(rig.decide_hold, ld, h)
                assert text(await calls.result("altered")) == NOT_ALLOWED
                h = await next_hold("rerouted")

                def reject():
                    decider = Decider.open(ld)
                    try:
                        decider.reject("f", "echo", echo_hash(fake), "no", lambda _: None)
                    finally:
                        decider.close()

                await anyio.to_thread.run_sync(reject)
                await anyio.to_thread.run_sync(rig.decide_hold, ld, h)
                await calls.result("rerouted")
        return state

    return anyio.run(main)


def test_one_terminal_entry_per_call(tmp_path):
    """After every ending a live process can produce (stage 6: all but shutdown), a fold of
    the ledger alone finds one ending per hold, one terminal entry per held call, and no
    call.sent for a hold that wasn't allowed."""
    fake, specs, pol = setup()
    ld = tmp_path / "ledger"
    _run_every_ending(ld, fake, specs, pol)
    entries = rig.entries(ld)
    created = [e["data"]["hold"] for e in entries if e["kind"] == "hold.created"]
    assert len(created) == 7
    folded = holds.HoldState()
    verify_bytes((ld / "ledger.jsonl").read_bytes(), None, folded.apply)
    allowed = set()
    for hold in created:
        endings = [
            e for e in entries if e["kind"] in holds.ENDINGS and e["data"].get("hold") == hold
        ]
        assert len(endings) == 1, hold
        assert folded.get(hold).ending.seq == endings[0]["seq"]
        sent = [e for e in entries if e["kind"] == "call.sent" and e["data"].get("hold") == hold]
        refused = [
            e for e in entries if e["kind"] == "call.refused" and e["data"].get("hold") == hold
        ]
        assert len(sent) + len(refused) == 1, hold
        if sent:
            returned = [
                e
                for e in entries
                if e["kind"] == "call.returned" and e["data"]["call_seq"] == sent[0]["seq"]
            ]
            assert len(returned) == 1
            allowed.add(hold)
            assert folded.get(hold).ending.decision == "allow"
    assert len(allowed) == 1
    assert [args for _, args, _ in fake.calls] == [ARGS]


def test_hold_state_from_ledger_matches_live(tmp_path):
    fake, specs, pol = setup()
    ld = tmp_path / "ledger"
    live = _run_every_ending(ld, fake, specs, pol)
    folded = holds.HoldState()
    verify_bytes((ld / "ledger.jsonl").read_bytes(), None, folded.apply)
    assert folded.snapshot() == live.snapshot()
    assert folded.open_holds() == []


def test_forwarded_arguments_come_from_the_side_file(tmp_path):
    """The request's in-memory arguments are changed after hold.created; after the allow the
    upstream receives exactly the side file's arguments."""
    fake, specs, pol = setup()
    ld = tmp_path / "ledger"

    async def main():
        async with rig.proxied(ld, specs, policy=pol, **FAST) as (client, gw):
            async with anyio.create_task_group() as tasks:
                calls = Calls(client, tasks)
                calls.start("a", arguments={"path": "/safe/place", "n": [1, 2.5]})
                [hold] = await rig.holds_created(ld, 1)
                waiting = gw._open[hold].arguments
                waiting["path"] = "/somewhere/else"
                waiting["extra"] = True
                await anyio.to_thread.run_sync(rig.decide_hold, ld, hold)
                return hold, await calls.result("a")

    hold, result = anyio.run(main)
    assert [args for _, args, _ in fake.calls] == [{"path": "/safe/place", "n": [1, 2.5]}]
    sent = rig.kinds(ld, "call.sent")[-1]["data"]
    assert (sent["hold"], sent["allowed_by"]) == (hold, "hold")


@pytest.mark.parametrize(
    "change, problem", [("alter", "does not match its name"), ("remove", "is missing")]
)
def test_allow_with_altered_side_file_is_refused(tmp_path, change, problem):
    fake, specs, pol = setup()
    ld = tmp_path / "ledger"

    async def main():
        async with rig.proxied(ld, specs, policy=pol, **FAST) as (client, gw):

            async def tamper(hold_id):
                commit = rig.kinds(ld, "hold.created")[0]["data"]["args_commit"]
                path = ld / "args" / f"{commit}.bin"
                if change == "alter":
                    path.write_bytes(path.read_bytes() + b" ")
                else:
                    path.unlink()

            gw.before_forward = tamper
            async with anyio.create_task_group() as tasks:
                calls = Calls(client, tasks)
                calls.start("a")
                [hold] = await rig.holds_created(ld, 1)
                await anyio.to_thread.run_sync(rig.decide_hold, ld, hold)
                return hold, await calls.result("a")

    hold, result = anyio.run(main)
    assert text(result) == NOT_ALLOWED and fake.calls == []
    trail = entries_for(ld, hold)
    assert [k for k, _ in trail] == ["hold.created", "hold.decided", "call.refused"]
    assert trail[2][1]["reason"] == f"hold {hold} was allowed, but its side file {problem}"


def test_allowed_by_records_the_rule(tmp_path):
    """call.sent of an unheld call records the rule that let it run."""
    fake = FakeUpstream()
    specs = [rig.spec("f", fake.server)]
    root = tmp_path / "root"
    root.mkdir()
    ld = tmp_path / "ledger"
    read = rig.classified(specs)
    world = rig.held(specs, echo="open-world")
    write = rig.classified(specs, workspace_roots=(os.path.realpath(root),))
    write.upstreams["f"].tools["echo"] = config.ToolRule("local-write", ("path",))
    off = Policy(holds=False)
    for pol in (read, world, write, off):

        async def main(p=pol):
            async with rig.proxied(ld, specs, policy=p, **FAST) as (client, gw):
                result = await client.call_tool("f__echo", {"path": str(root / "f")})
                assert result.is_error is False

        anyio.run(main)
    rules = [e["data"]["allowed_by"] for e in rig.kinds(ld, "call.sent")]
    assert rules == ["local-read", "open-world", "inside-roots", "holds-off"]


def test_fold_gives_every_ending(tmp_path):
    """The hold fold over a generated ledger: the first ending wins; a decision with another
    args_commit, or for an unknown hold, or after the ending, changes nothing."""
    s = "5e55105e55105e55"
    a, b, c, d, e = "a" * 16, "b" * 16, "c" * 16, "d" * 16, "e" * 16

    def created(hold, commit):
        return ("hold.created", {"session": s, "hold": hold, "tool": "f__echo",
                                 "args_commit": commit, "class": "egress",
                                 "class_from": "config", "rule": "egress",
                                 "reason": "class egress is held on every call",
                                 "timeout_seconds": 300})  # fmt: skip

    def decided(hold, commit, decision):
        return ("hold.decided", {"hold": hold, "args_commit": commit, "decision": decision,
                                 "actor": "person", "reason": None})  # fmt: skip

    specs = [
        ("session.started", {"session": s}),
        created(a, "1" * 64),
        created(b, "2" * 64),
        created(c, "3" * 64),
        created(d, "4" * 64),
        decided(a, "9" * 64, "allow"),  # another args_commit: ignored
        decided(a, "1" * 64, "deny"),
        decided(a, "1" * 64, "allow"),  # after the ending: nothing
        ("hold.expired", {"session": s, "hold": b, "reason": "timeout after 300 s"}),
        decided(b, "2" * 64, "allow"),
        ("hold.abandoned", {"session": "f" * 16, "hold": c, "held_by": s}),
        decided(e, "5" * 64, "allow"),  # no such hold
        decided(d, "4" * 64, "maybe"),  # not a decision
    ]
    build_chain(tmp_path, specs)
    folded = holds.HoldState()
    result = verify_bytes((tmp_path / "ledger.jsonl").read_bytes(), None, folded.apply)
    assert result.status == "intact"
    assert folded.get(a).ending == holds.Ending("hold.decided", 7, "deny", None)
    assert folded.get(b).ending == holds.Ending("hold.expired", 9, None, "timeout after 300 s")
    assert folded.get(c).ending == holds.Ending("hold.abandoned", 11)
    assert folded.get(e) is None
    assert [h.hold for h in folded.open_holds()] == [d]
    assert folded.mismatched() == [(6, a)]
    assert folded.session_started(s) == "2026-10-02T15:00:00.001Z"
