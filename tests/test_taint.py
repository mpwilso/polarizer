"""Session taint (docs/TAINT-SPEC.md, sections 5 to 10): an open-world call taints its session
when its answer comes back, recorded and fsynced before the client gets it; from then on every
egress call is held, even to a listed host, its reason naming the source; taint survives a
restart of serve in the same Claude Code session and not into a new one; holds, the card and
stats show it; and planted calls keep MEASURE-SPEC.md, section 11's rules.

In memory, with FakeUpstream: echo is egress with url_args ["url"] and the domain a.example,
wait and fail are open-world, the other tools local-read. Set POLARIZER_UPDATE_GOLDEN=1 to
rewrite the golden files, then read the diff."""

import json
import os
import re
import subprocess
import sys
import threading
from pathlib import Path

import anyio
import pytest
from conftest import CONFORMANCE, expected
from helpers import holdledger as hl
from helpers import rig
from helpers import taintledger as tl
from helpers.fakes import FakeUpstream

from polarizer import card, cli, holds, paths, policy, stats, taint
from polarizer.config import ToolRule
from polarizer.ledger import parse_head, verify_bytes
from polarizer.writer import FileOps

GOLDEN = Path(__file__).parent / "golden"
UPDATE = os.environ.get("POLARIZER_UPDATE_GOLDEN") == "1"
FAST = {"watch_interval": 0.05, "hold_watch_interval": 0.05, "hold_timeout": 0.3}
LISTED = {"url": "https://a.example/notes", "body": "hello"}
UNLISTED = {"url": "https://evil.example/notes", "body": "hello"}
NOT_ALLOWED = "polarizer: f__echo was not allowed"
CLIENT = "5d6e7f80-1a2b-4c3d-8e9f-0a1b2c3d4e5f"
OTHER = "6e7f8091-2b3c-4d4e-9f0a-1b2c3d4e5f60"
TAINT_TEXT = re.compile(
    r"session tainted by f__wait at (\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z) \(seq (\d+)\)"
)
GRANTS_END = "egress is held for the rest of the session, even to a host in this tool's domains"


def golden(name: str, text: str) -> None:
    path = GOLDEN / name
    if UPDATE:
        path.write_text(text, encoding="utf-8", newline="\n")
    assert text == path.read_text(encoding="utf-8")


def setup(**echo):
    fake = FakeUpstream()
    specs = [rig.spec("f", fake.server)]
    pol = rig.classified(specs)
    echo.setdefault("url_args", ("url",))
    echo.setdefault("domains", ("a.example",))
    pol.upstreams["f"].tools["echo"] = ToolRule("egress", **echo)
    pol.upstreams["f"].tools["wait"] = ToolRule("open-world")
    pol.upstreams["f"].tools["fail"] = ToolRule("open-world")
    return fake, specs, pol


def echo_calls(fake) -> list:
    return [c[1] for c in fake.calls if c[0] == "echo"]


def tainted_entries(ld: Path) -> list[dict]:
    return rig.kinds(ld, "session.tainted")


def test_open_world_taints_and_egress_is_held(tmp_path, capsys):
    fake, specs, pol = setup()
    ld = tmp_path / "ledger"

    async def main():
        async with rig.proxied(ld, specs, policy=pol, client_session=CLIENT, **FAST) as (
            client,
            gw,
        ):
            assert gw.taint is None
            before = await client.call_tool("f__echo", dict(LISTED))
            assert not before.is_error
            capsys.readouterr()
            fetched = await client.call_tool("f__wait", {"seconds": 0})
            assert not fetched.is_error
            err = capsys.readouterr().err
            assert gw.taint is not None
            after = await client.call_tool("f__echo", dict(LISTED))
            assert after.content[0].text == NOT_ALLOWED
            return err

    err = anyio.run(main)
    assert echo_calls(fake) == [LISTED]  # the second never reached the upstream
    sent = rig.kinds(ld, "call.sent")
    assert [e["data"]["tool"] for e in sent] == ["f__echo", "f__wait"]
    assert sent[0]["data"]["allowed_by"] == "domains"
    wait_seq = sent[1]["seq"]
    [tainted] = tainted_entries(ld)
    assert tainted["data"] == {
        "session": sent[1]["data"]["session"],
        "client_session": CLIENT,
        "tool": "f__wait",
        "call_seq": wait_seq,
    }
    [created] = rig.kinds(ld, "hold.created")
    data = created["data"]
    assert (data["rule"], data["class"]) == ("tainted", "egress")
    assert data["reason"] == (
        f"session tainted by f__wait at {tainted['ts']} (seq {wait_seq}); {GRANTS_END}"
    )
    assert err.count("polarizer: session tainted by f__wait") == 1
    assert f"polarizer: session tainted by f__wait (seq {wait_seq}); every egress call is held " \
        "from now on\n" in err  # fmt: skip
    # session.tainted is a security-state entry: fsynced, with ledger.head moved to it.
    assert parse_head((ld / "ledger.head").read_bytes()).seq >= tainted["seq"]


def test_taint_recorded_once(tmp_path):
    fake, specs, pol = setup()
    ld = tmp_path / "ledger"

    async def main():
        async with rig.proxied(ld, specs, policy=pol, **FAST) as (client, _):
            await client.call_tool("f__wait", {"seconds": 0})
            await client.call_tool("f__fail", {})
            await client.call_tool("f__wait", {"seconds": 0})

    anyio.run(main)
    [tainted] = tainted_entries(ld)
    assert tainted["data"]["tool"] == "f__wait" and tainted["data"]["client_session"] is None


@pytest.mark.parametrize("tool", ["fail", "refuse", "closed"])
def test_every_answer_taints(tmp_path, tool):
    """A tool error, a protocol error and a transport error each carry the upstream's answer:
    each taints, and session.tainted precedes call.returned."""
    fake, specs, pol = setup()
    pol.upstreams["f"].tools[tool] = ToolRule("open-world")
    ld = tmp_path / "ledger"

    async def main():
        async with rig.proxied(ld, specs, policy=pol, **FAST) as (client, _):
            try:
                await client.call_tool(f"f__{tool}", {})
            except Exception:
                pass

    anyio.run(main)
    kinds = [e["kind"] for e in rig.entries(ld)]
    assert kinds.count("session.tainted") == 1
    assert kinds.index("session.tainted") < kinds.index("call.returned")


class HeldTaint(FileOps):
    """serve's file layer: the write of session.tainted waits for `release`, so the test can
    see whether the client got the answer before the entry was recorded."""

    def __init__(self):
        self.holding = threading.Event()
        self.release = threading.Event()

    def write(self, fd, data):
        if b'"session.tainted"' in data:
            self.holding.set()
            assert self.release.wait(30)
        super().write(fd, data)


def test_taint_written_before_the_answer(tmp_path):
    fake, specs, pol = setup()
    ld = tmp_path / "ledger"
    ops = HeldTaint()
    got = {}

    async def main():
        async with rig.proxied(ld, specs, policy=pol, ops=ops, **FAST) as (client, _):
            async with anyio.create_task_group() as tasks:

                async def call():
                    got["result"] = await client.call_tool("f__wait", {"seconds": 0})
                    got["entries"] = [e["kind"] for e in rig.entries(ld)]

                tasks.start_soon(call)
                await anyio.to_thread.run_sync(ops.holding.wait, 10)
                await anyio.sleep(0.5)
                assert "result" not in got  # held back while the taint is not written
                ops.release.set()
            assert not got["result"].is_error

    anyio.run(main)
    assert "session.tainted" in got["entries"]
    assert got["entries"].index("session.tainted") < got["entries"].index("call.returned")


def test_taint_before_first_progress(tmp_path):
    fake, specs, pol = setup()
    ld = tmp_path / "ledger"
    seen = []

    async def main():
        async with rig.proxied(ld, specs, policy=pol, **FAST) as (client, _):

            async def progress(value, total, message):
                seen.append([e["kind"] for e in rig.entries(ld)])

            await client.call_tool("f__wait", {"seconds": 1.2}, progress_callback=progress)

    anyio.run(main)
    assert seen, "no progress reached the client"
    assert "session.tainted" in seen[0]


class FailTaint(FileOps):
    """serve's file layer: the write of session.tainted fails."""

    def write(self, fd, data):
        if b'"session.tainted"' in data:
            raise OSError(5, "Input/output error")
        super().write(fd, data)


def test_taint_withheld_when_unrecordable(tmp_path):
    fake, specs, pol = setup()
    ld = tmp_path / "ledger"

    async def main():
        async with rig.proxied(ld, specs, policy=pol, ops=FailTaint(), **FAST) as (client, gw):
            result = await client.call_tool("f__wait", {"seconds": 0})
            assert result.is_error
            assert result.content[0].text == (
                "polarizer: the answer from f__wait was withheld: the ledger could not record "
                "that it taints this session"
            )
            assert gw.taint is not None  # tainted in memory
            held = await client.call_tool("f__echo", dict(LISTED))
            assert held.content[0].text == NOT_ALLOWED

    anyio.run(main)
    assert tainted_entries(ld) == []
    [created] = rig.kinds(ld, "hold.created")
    assert created["data"]["rule"] == "tainted"
    assert TAINT_TEXT.match(created["data"]["reason"])
    assert echo_calls(fake) == []


def test_taint_reason_prefix(tmp_path):
    """In a tainted session, egress holds by rows 4, 4a and 4b keep their rule and start with
    the taint; stats.pattern_of still reads the path rule's pattern."""
    fake, specs, pol = setup(path_args=("file",))
    home = Path(os.path.realpath(tmp_path)) / "home"
    pol = policy.Policy(upstreams=pol.upstreams, home=paths.split(str(home)))
    pol.upstreams["f"].tools["ask"] = ToolRule("egress")
    secret = str(home / ".ssh" / "id_ed25519")
    fine = str(Path(os.path.realpath(tmp_path)) / "notes.txt")
    ld = tmp_path / "ledger"

    async def main():
        async with rig.proxied(ld, specs, policy=pol, **FAST) as (client, _):
            await client.call_tool("f__wait", {"seconds": 0})
            for args in (
                {"url": "https://evil.example/", "file": fine},  # 4a
                {"url": "https://a.example/", "file": secret},  # 4b
                {"url": "https://a.example/", "file": fine},  # 4c
            ):
                assert (await client.call_tool("f__echo", args)).content[0].text == NOT_ALLOWED
            post = await client.call_tool("f__ask", {})  # 4: no domains
            assert post.content[0].text == "polarizer: f__ask was not allowed"

    anyio.run(main)
    created = [e["data"] for e in rig.kinds(ld, "hold.created")]
    assert [c["rule"] for c in created] == ["egress", "egress", "tainted", "egress"]
    for c in created:
        assert TAINT_TEXT.match(c["reason"]), c["reason"]
    prefix = TAINT_TEXT.match(created[0]["reason"]).group(0)
    assert created[0]["reason"] == (
        f'{prefix}; argument "url": host evil.example is not in this tool\'s domains'
    )
    assert created[1]["reason"].startswith(f'{prefix}; argument "file": ')
    assert stats.pattern_of(created[1]["reason"]) == "~/.ssh/**"
    assert card.recommend(created[1]["rule"], created[1]["reason"])[0] == "deny"
    assert created[3]["reason"] == f"{prefix}; class egress is held on every call"


def test_what_does_not_taint(tmp_path):
    """A held-and-denied open-world call, one refused by the deny tier, one cancelled before
    any answer, a local-read call, and any call under --no-holds: none taints."""
    fake, specs, pol = setup()
    pol.upstreams["f"].tools["fail"] = ToolRule("open-world", deny_values=(("q", ("x",)),))
    pol.upstreams["f"].tools["wait"] = ToolRule("open-world", ("path",))
    ld = tmp_path / "ledger"

    async def main():
        async with rig.proxied(ld, specs, policy=pol, **FAST) as (client, gw):
            # Held by path-missing, then expired: never forwarded.
            held = await client.call_tool("f__wait", {"seconds": 0})
            assert held.content[0].text == "polarizer: f__wait was not allowed"
            refused = await client.call_tool("f__fail", {"q": "x"})
            assert refused.content[0].text == "polarizer: f__fail was not allowed"
            await client.call_tool("f__change", {})  # local-read
            with anyio.move_on_after(0.3):  # cancelled before any answer or progress
                await client.call_tool("f__wait", {"seconds": 0.8, "path": str(tmp_path)})
            await anyio.sleep(1.0)
            assert gw.taint is None

    anyio.run(main)
    assert tainted_entries(ld) == []
    off = policy.Policy(upstreams=pol.upstreams, holds=False)
    ld2 = tmp_path / "ledger2"

    async def unguarded():
        async with rig.proxied(ld2, specs, policy=off, **FAST) as (client, gw):
            await client.call_tool("f__wait", {"seconds": 0, "path": str(tmp_path)})
            assert gw.taint is None

    anyio.run(unguarded)
    assert tainted_entries(ld2) == []


def _restart(ld, specs, pol, client_session, capsys=None):
    """One gateway on the ledger: (its taint at start, its stderr at start, whether the egress
    call to a listed host ran)."""
    out = {}

    async def main():
        async with rig.proxied(ld, specs, policy=pol, client_session=client_session, **FAST) as (
            client,
            gw,
        ):
            out["taint"] = gw.taint
            out["err"] = capsys.readouterr().err if capsys else ""
            result = await client.call_tool("f__echo", dict(LISTED))
            out["ran"] = not result.is_error

    anyio.run(main)
    return out


def test_taint_survives_restart_same_session(tmp_path, capsys):
    fake, specs, pol = setup()
    ld = tmp_path / "ledger"

    async def first():
        async with rig.proxied(ld, specs, policy=pol, client_session=CLIENT, **FAST) as (
            client,
            _,
        ):
            await client.call_tool("f__wait", {"seconds": 0})

    anyio.run(first)
    [tainted] = tainted_entries(ld)
    capsys.readouterr()
    again = _restart(ld, specs, pol, CLIENT, capsys)
    assert again["taint"] is not None and again["taint"].call_seq == tainted["data"]["call_seq"]
    assert not again["ran"]
    assert (
        f"polarizer: this session was tainted by f__wait at {tainted['ts']} "
        f"(seq {tainted['data']['call_seq']}); every egress call is held\n"
    ) in again["err"]
    # A new Claude Code session starts clean, and so does a serve with no client session.
    assert _restart(ld, specs, pol, OTHER)["ran"]
    assert _restart(ld, specs, pol, None)["ran"]
    # And the restarted serve's hold names the original source.
    reasons = [e["data"]["reason"] for e in rig.kinds(ld, "hold.created")]
    assert len(reasons) == 1
    assert reasons[0].startswith(
        f"session tainted by f__wait at {tainted['ts']} (seq {tainted['data']['call_seq']}); "
    )
    loaded = [e["data"]["client_session"] for e in rig.kinds(ld, "policy.loaded")]
    assert loaded == [CLIENT, CLIENT, OTHER, None]


def test_taint_without_client_session(tmp_path, capsys):
    """With no client session, taint holds for this process only; the start-up warning appears
    only when some tool has domains."""
    fake, specs, pol = setup()
    ld = tmp_path / "ledger"
    warning = "polarizer: warning: CLAUDE_CODE_SESSION_ID is not set; taint lasts only while " \
        "this process runs\n"  # fmt: skip

    async def main():
        async with rig.proxied(
            ld, specs, policy=pol, client_session_warning=warning.rstrip("\n"), **FAST
        ) as (client, gw):
            assert warning in capsys.readouterr().err
            await client.call_tool("f__wait", {"seconds": 0})
            result = await client.call_tool("f__echo", dict(LISTED))
            assert result.content[0].text == NOT_ALLOWED

    anyio.run(main)
    [tainted] = tainted_entries(ld)
    assert tainted["data"]["client_session"] is None
    assert _restart(ld, specs, pol, None)["ran"]

    plain = rig.classified(specs)
    plain.upstreams["f"].tools["wait"] = ToolRule("open-world")

    async def no_domains():
        async with rig.gateway(
            tmp_path / "other", specs, policy=plain, client_session_warning=warning.rstrip("\n")
        ):
            pass

    capsys.readouterr()
    anyio.run(no_domains)
    assert "CLAUDE_CODE_SESSION_ID" not in capsys.readouterr().err


def test_client_session_from_environment(tmp_path, monkeypatch):
    monkeypatch.delenv(taint.ENV, raising=False)
    assert taint.client_session({}) == (
        None,
        "polarizer: warning: CLAUDE_CODE_SESSION_ID is not set; taint lasts only while this "
        "process runs",
    )
    assert taint.client_session({taint.ENV: CLIENT}) == (CLIENT, None)
    assert taint.client_session({taint.ENV: "a.b_c:d-1"}) == ("a.b_c:d-1", None)
    bad = (
        None,
        "polarizer: warning: CLAUDE_CODE_SESSION_ID is not a session id Polarizer reads; taint "
        "lasts only while this process runs",
    )
    for value in ("", "a b", "x" * 129, "caf" + chr(0xE9), "a/b", "a\nb"):
        assert taint.client_session({taint.ENV: value}) == bad, value
    # serve records it in policy.loaded.
    ld = tmp_path / "ledger"
    toml = rig.serve_config(
        tmp_path, f"[upstream.p]\ncommand = {rig.toml_str(sys.executable)}\n"
        f"args = [{rig.toml_str(rig.PROBE)}]\n", ld
    )  # fmt: skip
    for value, want in ((CLIENT, CLIENT), ("not ok!", None), (None, None)):
        env = {k: v for k, v in os.environ.items() if k != taint.ENV}
        if value is not None:
            env[taint.ENV] = value
        done = subprocess.run(
            [sys.executable, "-m", "polarizer", "serve", "--config", str(toml)],
            stdin=subprocess.DEVNULL, capture_output=True, timeout=120, env=env,
        )  # fmt: skip
        assert done.returncode == 0, done.stderr
        assert rig.kinds(ld, "policy.loaded")[-1]["data"]["client_session"] == want
        # No tool has domains here, so no warning.
        assert b"CLAUDE_CODE_SESSION_ID" not in done.stderr


def test_two_serves_share_taint(tmp_path):
    fake, specs, pol = setup()
    fake2 = FakeUpstream()
    specs2 = [rig.spec("f", fake2.server)]
    ld = tmp_path / "ledger"

    async def main():
        async with rig.proxied(ld, specs, policy=pol, client_session=CLIENT, **FAST) as (
            one,
            _,
        ):
            async with rig.proxied(ld, specs2, policy=pol, client_session=CLIENT, **FAST) as (
                two,
                gw2,
            ):
                await one.call_tool("f__wait", {"seconds": 0})
                result = await two.call_tool("f__echo", dict(LISTED))
                assert result.content[0].text == NOT_ALLOWED
                assert gw2.taint is not None

    anyio.run(main)
    assert echo_calls(fake2) == []
    assert [e["data"]["rule"] for e in rig.kinds(ld, "hold.created")] == ["tainted"]


# What a person sees (section 9) -----------------------------------------------------------


@pytest.fixture
def taint_ledger(tmp_path):
    """taintledger's ledger with every session's lock held, so each reads as running."""
    locks = []

    def build(with_decision=False):
        directory = tl.ledger(tmp_path / "ledger", with_decision)
        for session in tl.SESSIONS:
            locks.append(hl.hold_running(directory, session))
        return directory

    yield build
    for held in locks:
        held.close()


def test_holds_shows_taint(tmp_path, capsys, monkeypatch, taint_ledger):
    monkeypatch.setattr(cli, "_now", lambda: hl.NOW)
    directory = taint_ledger()
    code = cli.main(["holds", "--ledger-dir", str(directory)])
    out, err = capsys.readouterr()
    assert (code, err) == (0, "")
    golden("holds_tainted.txt", out)
    # allow prints the same block, the taint line included.
    code = cli.main(["allow", "--ledger-dir", str(directory), tl.BY_TAINT, "--allow-no-terminal"])
    out, _ = capsys.readouterr()
    assert code == 0 and tl.TAINT in out.split("\n")


def test_card_shows_taint(taint_ledger):
    directory = taint_ledger()
    shown = card.Card(directory, now=lambda: hl.NOW).state()
    cards = {c["hold"]: c for c in shown["open"]}
    row = ["Session taint", [
        "tainted by web__fetch at 2026-10-02T15:00:00.004Z (seq 3): every egress call of this "
        "session is held"
    ]]  # fmt: skip
    for hold in (tl.BY_TAINT, tl.UNLISTED):
        assert row in cards[hold]["rows"]
        labels = [label for label, _ in cards[hold]["rows"]]
        assert labels.index("Session taint") == labels.index("Session") + 1
        assert tl.TAINT in cards[hold]["block"]
    clean = cards[tl.CLEAN_HOLD]
    assert "Session taint" not in [label for label, _ in clean["rows"]]
    assert tl.TAINT not in clean["block"]
    assert cards[tl.BY_TAINT]["recommendation"] == {
        "decision": "deny",
        "text": "Deny, unless you asked for exactly this: the agent read content from outside "
        "this machine earlier in this session, and this call would send data out. Text it read "
        "can ask it to send what it knows.",
    }
    assert cards[tl.UNLISTED]["recommendation"]["decision"] is None


def test_stats_counts_taint(tmp_path, capsys, taint_ledger):
    directory = taint_ledger(with_decision=True)
    code = cli.main(["stats", "--ledger-dir", str(directory)])
    out, err = capsys.readouterr()
    assert (code, err) == (0, "")
    golden("stats_tainted.txt", out)
    # Rule tainted never gets a suggestion, however often it is allowed.
    counted = [
        stats.Counted(
            holds.Hold(f"{i:016x}", i, "2026-10-02T15:00:00.000Z", "s", "web__post", "c", "egress",
                       "config", "tainted", "r", 300),
            "allowed", i, 1, False,
        )
        for i in range(25)
    ]  # fmt: skip
    assert stats.suggestions(counted) == []


# Planted calls (section 10) ---------------------------------------------------------------


def test_planted_calls_with_taint(tmp_path):
    """Planting on in a tainted session: a call refused by the deny tier is no trigger, a
    planted call the deny tier would refuse is dropped, and once allowed, a planted hold is
    a write-pattern hold of the trigger's own tool, never egress or tainted, with the taint
    line in its block like every hold of the session."""
    fake = FakeUpstream()
    specs = [rig.spec("f", fake.server)]
    root = Path(os.path.realpath(tmp_path)) / "work"
    root.mkdir()
    pol = rig.classified(specs, workspace_roots=(str(root),))
    pol.upstreams["f"].tools["wait"] = ToolRule("open-world")
    pol.upstreams["f"].tools["refuse"] = ToolRule("egress", url_args=("url",),
                                                  domains=("a.example",))  # fmt: skip
    # Refused: content "forbidden"; planted targets under .git are refused too.
    deny = (("content", ("forbidden",)), ("path", ("[.]git",)))
    pol.upstreams["f"].tools["echo"] = ToolRule("local-write", ("path",), deny_values=deny)
    pol.planted_calls_per_session = 1
    ld = tmp_path / "ledger"

    def write(i, content="note"):
        return {"path": str(root / f"n{i}.md"), "content": content}

    async def main():
        async with rig.proxied(ld, specs, policy=pol, **FAST) as (client, gw):
            gw.planter.chance = lambda: True
            gw.planter.delay = lambda: 0
            gw.planter.choose = lambda options: options[0]  # .git/hooks/pre-commit
            gw.planter.home = str(tmp_path / "home")
            await client.call_tool("f__wait", {"seconds": 0})
            assert gw.taint is not None
            for i in range(6):  # refused by the deny tier: counted, never triggers
                result = await client.call_tool("f__echo", write(i, "forbidden"))
                assert result.content[0].text == "polarizer: f__echo was not allowed"
            await anyio.sleep(0.3)
            assert rig.kinds(ld, "hold.created") == []
            for i in range(3):  # triggers, but the planted target is refused: dropped
                assert not (await client.call_tool("f__echo", write(10 + i))).is_error
            await anyio.sleep(0.5)
            assert rig.kinds(ld, "hold.created") == [] and gw.planter.issued == 0
            pol.upstreams["f"].tools["echo"] = ToolRule(
                "local-write", ("path",), deny_values=deny[:1]
            )
            assert not (await client.call_tool("f__echo", write(20))).is_error
            await rig.until(lambda: rig.kinds(ld, "hold.created"), 10)

    anyio.run(main)
    [created] = rig.kinds(ld, "hold.created")
    data = created["data"]
    assert (data["class"], data["rule"]) == ("local-write", "write-pattern")
    assert "session tainted" not in data["reason"]
    folded = holds.HoldState()
    verify_bytes((ld / "ledger.jsonl").read_bytes(), None, folded.apply)
    hold = folded.get(data["hold"])
    block = holds.block(hold, folded, ld, hl.NOW, "running")
    assert any(line.startswith("session tainted by f__wait at ") for line in block)
    revealed = [e["data"]["label"] for e in rig.kinds(ld, "hold.revealed")]
    assert revealed in ([], ["planted"])  # revealed once it ends; it may still be open


# The fixture (section 8) ------------------------------------------------------------------


def test_taint_fixture_verifies():
    """valid/taint is intact in both verifiers (test_conformance.py checks every fixture the
    same way), and holds what section 8 says it does."""
    import reference_verify

    path = CONFORMANCE / "valid" / "taint.jsonl"
    head = CONFORMANCE / "valid" / "taint.head"
    assert expected()["fixtures"]["valid/taint"]["status"] == "intact"
    assert reference_verify.verify(path, head)["status"] == "intact"
    data = path.read_bytes()
    folded = holds.HoldState()
    result = verify_bytes(data, head.read_bytes(), folded.apply)
    assert result.status == "intact"
    entries = [json.loads(line) for line in data.splitlines()]
    kinds = [e["kind"] for e in entries]
    assert kinds.count("session.tainted") == 1
    refused = [e["data"] for e in entries if e["kind"] == "call.refused"]
    assert [r.get("rule") for r in refused] == ["deny-value"]
    sent = [e["data"]["allowed_by"] for e in entries if e["kind"] == "call.sent"]
    assert "domains" in sent
    sessions = [e["data"]["session"] for e in entries if e["kind"] == "policy.loaded"]
    assert len(sessions) == 2 and all(folded.taint_of(s) is not None for s in sessions)
    rules = [e["data"]["rule"] for e in entries if e["kind"] == "hold.created"]
    assert "tainted" in rules
