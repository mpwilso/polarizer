"""Live planted calls inside serve (docs/MEASURE-SPEC.md, sections 11 and 12; stage 10).

In memory, with FakeUpstream's echo classed local-write with path_args ["path"] and a workspace
root, so a write inside the root is forwarded (a trigger) and the planted target under
.git/hooks is held by the real rule function. The planter's draws are forced: the chance to 1,
the delay to 0, the target to the first of the list, unless a test says otherwise. Set
POLARIZER_UPDATE_GOLDEN=1 to rewrite the stage 10 golden files, then read the diff.
"""

import os
import re
from dataclasses import replace
from pathlib import Path

import anyio
import pytest
from conftest import build_chain
from helpers import holdledger as hl
from helpers import plantledger as pl
from helpers import rig
from helpers.fakes import FakeUpstream

from polarizer import cli, config, decisions, holds, planted, policy
from polarizer.config import ToolRule
from polarizer.ledger import verify_bytes

GOLDEN = Path(__file__).parent / "golden"
UPDATE = os.environ.get("POLARIZER_UPDATE_GOLDEN") == "1"
FAST = {"watch_interval": 0.05, "hold_watch_interval": 0.05}
HELD_LINE = re.compile(
    r"polarizer: held f__echo as hold [0-9a-f]{16} \(([a-z-]+)\); run polarizer holds"
)


def golden(name: str, text: str) -> None:
    path = GOLDEN / name
    if UPDATE:
        path.write_text(text, encoding="utf-8", newline="\n")
    assert text == path.read_text(encoding="utf-8")


def setup(tmp_path: Path, n: int = 1, cls: str = "local-write"):
    """A fake upstream "f" whose echo has class `cls` and path_args ["path"], a workspace root
    `work`, and planting at `n` per session."""
    fake = FakeUpstream()
    specs = [rig.spec("f", fake.server)]
    root = tmp_path / "work"
    root.mkdir(parents=True, exist_ok=True)
    pol = rig.classified(specs, workspace_roots=(os.path.realpath(root),))
    for upstream in pol.upstreams.values():
        upstream.tools["echo"] = ToolRule(cls, ("path",))
    pol.planted_calls_per_session = n
    return fake, specs, pol, Path(os.path.realpath(root))


def force(gw, home: Path, choose=None, chance=True):
    gw.planter.chance = lambda: chance
    gw.planter.delay = lambda: 0
    gw.planter.choose = choose or (lambda options: options[0])
    gw.planter.home = str(home)


def outside(name) -> str:
    """An absolute path outside every workspace root, on any platform: held by outside-roots."""
    return os.path.join(os.path.abspath(os.sep), "elsewhere", str(name))


def inside(root: Path, i: int) -> dict:
    return {"path": str(root / f"notes{i}.md"), "content": f"note {i}"}


def planted_ids(ld: Path) -> list[str]:
    """Holds revealed as planted, in seq order."""
    return [e["data"]["hold"] for e in rig.kinds(ld, "hold.revealed")
            if e["data"]["label"] == "planted"]  # fmt: skip


def holds_with_path(ld: Path, part: str) -> list[str]:
    """Hold ids whose reason names a path containing `part` (the planted target)."""
    return [e["data"]["hold"] for e in rig.kinds(ld, "hold.created")
            if part in e["data"]["reason"]]  # fmt: skip


def call_entries(ld: Path, hold: str) -> list[str]:
    return [e["kind"] for e in rig.entries(ld)
            if e["kind"].startswith("call.") and e["data"].get("hold") == hold]  # fmt: skip


async def calls(client, root: Path, n: int, start: int = 0) -> None:
    for i in range(start, start + n):
        result = await client.call_tool("f__echo", inside(root, i))
        assert result.is_error is False


# Never forwarded -----------------------------------------------------------------------------


@pytest.mark.parametrize("ending", ["allow", "deny", "expire", "shutdown"])
def test_planted_call_is_never_forwarded(tmp_path, ending):
    fake, specs, pol, root = setup(tmp_path)
    ld = tmp_path / "ledger"
    timeout = 1.0 if ending == "expire" else None
    printed = {}

    async def main():
        async with rig.proxied(ld, specs, policy=pol, hold_timeout=timeout, **FAST) as (client, gw):
            force(gw, tmp_path / "home")
            await calls(client, root, 5)
            await rig.until(lambda: holds_with_path(ld, ".git"), 10)
            [hold] = holds_with_path(ld, ".git")
            assert len(fake.calls) == 5
            await anyio.sleep(0.5)  # ten watch intervals
            assert len(fake.calls) == 5
            if ending in ("allow", "deny"):
                out, err = await anyio.to_thread.run_sync(rig.decide_hold, ld, hold, ending)
                printed["out"], printed["err"] = out, err
            if ending != "shutdown":
                await rig.until(lambda: planted_ids(ld) == [hold], 10)
            return hold

    hold = anyio.run(main)
    assert len(fake.calls) == 5 and all(args["path"].endswith(".md") for _, args, _ in fake.calls)
    assert call_entries(ld, hold) == []
    assert planted_ids(ld) == [hold]
    [reveal] = [e for e in rig.kinds(ld, "hold.revealed") if e["data"]["hold"] == hold]
    sent = rig.kinds(ld, "call.sent")
    assert reveal["data"]["derived_from"] == sent[4]["seq"]
    ending_kind = {"allow": "hold.decided", "deny": "hold.decided"}.get(ending, "hold.expired")
    [end] = [e for e in rig.entries(ld) if e["kind"] in holds.ENDINGS and e["data"]["hold"] == hold]
    assert end["kind"] == ending_kind and end["seq"] < reveal["seq"]
    if ending == "shutdown":
        assert end["data"]["reason"] == "polarizer shut down while the call was held"
    if ending == "allow":
        assert printed["out"][-1] == "You allowed it: missed." and printed["err"] == []
    if ending == "deny":
        assert printed["out"][-1] == "You denied it: caught."
    assert "where git runs code." in reveal["data"]["why"]


# Opt-in, the cap, activity ----------------------------------------------------------------


@pytest.mark.parametrize("how", ["absent", "zero", "no-holds"])
def test_planting_is_opt_in(tmp_path, how):
    fake, specs, pol, root = setup(tmp_path, n=0 if how != "no-holds" else 1)
    if how == "no-holds":
        pol.holds = False
    ld = tmp_path / "ledger"

    async def main():
        async with rig.proxied(ld, specs, policy=pol, **FAST) as (client, gw):
            force(gw, tmp_path / "home")
            assert not gw.planter.on
            await calls(client, root, 500)
            await anyio.sleep(0.2)

    anyio.run(main)
    assert len(fake.calls) == 500
    assert rig.kinds(ld, "hold.created") == [] and rig.kinds(ld, "hold.revealed") == []
    [loaded] = rig.kinds(ld, "policy.loaded")
    assert loaded["data"]["planted_calls_per_session"] == 0 or how == "no-holds"


def test_no_holds_records_zero(tmp_path):
    path = tmp_path / "polarizer.toml"
    path.write_text(
        '[policy]\nplanted_calls_per_session = 2\n[upstream.f]\ncommand = "x"\n', encoding="utf-8"
    )
    cfg = config.load(path)
    assert policy.build(cfg).loaded("s")["planted_calls_per_session"] == 2
    assert policy.build(cfg, holds=False).loaded("s")["planted_calls_per_session"] == 0


def test_the_key_is_checked(tmp_path):
    for value in ("4", "-1", "true", "1.0", '"1"'):
        path = tmp_path / "polarizer.toml"
        path.write_text(
            f'[policy]\nplanted_calls_per_session = {value}\n[upstream.f]\ncommand = "x"\n',
            encoding="utf-8",
        )
        with pytest.raises(config.ConfigError) as caught:
            config.load(path)
        assert str(caught.value) == (
            "polarizer.toml: [policy] planted_calls_per_session must be an integer from 0 to 3"
        )


def test_policy_hash_unchanged_without_the_key(tmp_path):
    from test_policy_config import FIXED, FIXED_SHA, write

    assert policy.build(config.load(write(tmp_path, FIXED))).sha256 == FIXED_SHA
    zero = FIXED.replace("[policy]\n", "[policy]\nplanted_calls_per_session = 0\n")
    assert policy.build(config.load(write(tmp_path, zero, "zero.toml"))).sha256 == FIXED_SHA
    one = FIXED.replace("[policy]\n", "[policy]\nplanted_calls_per_session = 1\n")
    assert policy.build(config.load(write(tmp_path, one, "one.toml"))).sha256 != FIXED_SHA


def test_planted_cap_per_session(tmp_path):
    fake, specs, pol, root = setup(tmp_path, n=1)
    ld = tmp_path / "ledger"

    async def main():
        async with rig.proxied(ld, specs, policy=pol, **FAST) as (client, gw):
            force(gw, tmp_path / "home")
            await calls(client, root, 200)
            await anyio.sleep(0.3)
            assert gw.planter.issued == 1

    anyio.run(main)
    assert len(holds_with_path(ld, ".git")) == 1 and len(fake.calls) == 200


def test_none_near_the_cap(tmp_path):
    """With 12 holds open no plan is made; and a plan whose delay ends with 12 open is
    dropped."""
    fake, specs, pol, root = setup(tmp_path, n=1)
    ld = tmp_path / "ledger"

    async def main():
        async with rig.proxied(ld, specs, policy=pol, hold_timeout=60.0, **FAST) as (client, gw):
            force(gw, tmp_path / "home", chance=False)
            gate = anyio.Event()

            async def held_sleep(_seconds):
                await gate.wait()

            gw.planter.sleep = held_sleep
            async with anyio.create_task_group() as tasks:
                await calls(client, root, 4)
                gw.planter.chance = lambda: True
                await calls(client, root, 1, start=4)  # the trigger; its plan waits on the gate
                assert gw.planter.pending == 1
                gw.planter.chance = lambda: False
                for i in range(12):  # outside every root: held
                    tasks.start_soon(client.call_tool, "f__echo", {"path": outside(i)})
                await rig.until(lambda: len(gw._open) == 12, 10)
                gw.planter.chance = lambda: True
                await calls(client, root, 3, start=10)  # 12 open: no plan
                assert gw.planter.pending == 1
                gate.set()  # the delay ends with 12 open: dropped
                await rig.until(lambda: gw.planter.pending == 0, 10)
                await anyio.sleep(0.2)
                assert gw.planter.issued == 0
                tasks.cancel_scope.cancel()

    anyio.run(main)
    assert holds_with_path(ld, ".git") == []


def test_planted_only_while_active(tmp_path):
    fake, specs, pol, root = setup(tmp_path, n=3)
    ld = tmp_path / "ledger"

    async def main():
        async with rig.proxied(ld, specs, policy=pol, **FAST) as (client, gw):
            force(gw, tmp_path / "home")
            now = [0.0]
            gw.planter.clock = lambda: now[0]
            await calls(client, root, 4)
            assert gw.planter.pending + gw.planter.issued == 0  # fewer than 5 calls
            now[0] = 100.0
            await calls(client, root, 1, start=4)  # 5 calls, but 1 in the last 60 s
            assert gw.planter.pending + gw.planter.issued == 0
            now[0] = 101.0
            await calls(client, root, 1, start=5)  # 2 in the last 60 s
            await rig.until(lambda: gw.planter.issued == 1, 10)

    anyio.run(main)


def test_planted_hold_uses_the_rule_function(tmp_path):
    fake, specs, pol, root = setup(tmp_path, n=2)
    ld = tmp_path / "ledger"
    plain = (str(root / "plain.txt"), planted.HOOK)

    async def main():
        async with rig.proxied(ld, specs, policy=pol, **FAST) as (client, gw):
            force(gw, tmp_path / "home", choose=lambda options: plain)
            await calls(client, root, 5)
            await rig.until(lambda: gw.planter.pending == 0, 10)
            assert gw.planter.issued == 0  # evaluate allows a write inside the root: dropped
            gw.planter.choose = lambda options: options[0]
            await calls(client, root, 1, start=5)
            await rig.until(lambda: holds_with_path(ld, ".git"), 10)

    anyio.run(main)
    [hold] = holds_with_path(ld, ".git")
    [created] = [e["data"] for e in rig.kinds(ld, "hold.created") if e["data"]["hold"] == hold]
    target = os.path.join(str(root), ".git", "hooks", "pre-commit")
    verdict = policy.evaluate(pol, "f", "echo", {"path": target, "content": "note 5"}, None)
    assert (created["class"], created["rule"], created["reason"]) == (
        verdict.cls,
        verdict.rule,
        verdict.reason,
    )
    assert created["rule"] == "write-pattern"


# The commitment and the reveal --------------------------------------------------------------


def _run_mixed(ld, specs, pol, root, home, decide=True):
    """A real held call allowed, a real held call denied, and a planted hold denied."""

    async def main():
        async with rig.proxied(ld, specs, policy=pol, **FAST) as (client, gw):
            force(gw, home)
            async with anyio.create_task_group() as tasks:
                for key in ("a", "b"):
                    tasks.start_soon(client.call_tool, "f__echo", {"path": outside(key)})
                await rig.holds_created(ld, 2)
                if pol.planted_calls_per_session:
                    await calls(client, root, 5)
                    await rig.until(lambda: holds_with_path(ld, ".git"), 10)
                ids = rig.hold_ids(ld)
                for hold, how in zip(ids, ("allow", "deny", "deny"), strict=False):
                    await anyio.to_thread.run_sync(rig.decide_hold, ld, hold, how)
                await rig.until(lambda: len(rig.kinds(ld, "hold.decided")) == len(ids), 10)
            if pol.planted_calls_per_session:
                await rig.until(lambda: len(rig.kinds(ld, "hold.revealed")) == len(ids), 10)

    anyio.run(main)


def test_every_hold_commits_when_on(tmp_path):
    fake, specs, pol, root = setup(tmp_path, n=1)
    ld = tmp_path / "ledger"
    _run_mixed(ld, specs, pol, root, tmp_path / "home")
    entries = rig.entries(ld)
    created = [e for e in entries if e["kind"] == "hold.created"]
    assert len(created) == 3 and all("plant_commit" in e["data"] for e in created)
    folded = holds.HoldState()
    verify_bytes((ld / "ledger.jsonl").read_bytes(), None, folded.apply)
    for e in created:
        hold = e["data"]["hold"]
        reveals = [r for r in entries if r["kind"] == "hold.revealed" and r["data"]["hold"] == hold]
        assert len(reveals) == 1
        assert reveals[0]["seq"] > folded.get(hold).ending.seq
        assert holds.reveal_checks(folded.get(hold), folded.revealed(hold))
    labels = sorted(r["data"]["label"] for r in entries if r["kind"] == "hold.revealed")
    assert labels == ["planted", "real", "real"]
    # Off: no commitment and no reveal.
    fake, specs, pol, root = setup(tmp_path / "off", n=0)
    off = tmp_path / "off-ledger"
    _run_mixed(off, specs, pol, root, tmp_path / "home")
    assert all("plant_commit" not in e["data"] for e in rig.kinds(off, "hold.created"))
    assert rig.kinds(off, "hold.revealed") == []


def test_salt_never_written_before_reveal(tmp_path):
    fake, specs, pol, root = setup(tmp_path, n=1)
    ld = tmp_path / "ledger"
    seen = {}

    async def main():
        async with rig.proxied(ld, specs, policy=pol, **FAST) as (client, gw):
            force(gw, tmp_path / "home")
            async with anyio.create_task_group() as tasks:
                tasks.start_soon(client.call_tool, "f__echo", {"path": outside("x")})
                [hold] = await rig.holds_created(ld, 1)
                for path in tmp_path.rglob("*"):
                    if not path.is_file():
                        continue
                    try:
                        seen[str(path)] = path.read_bytes()
                    except PermissionError:
                        # Windows: a lock file whose byte 0 a process holds. Lock files are
                        # empty; one that isn't would fail here.
                        assert path.suffix == ".lock" and path.stat().st_size == 0, path
                await anyio.to_thread.run_sync(rig.decide_hold, ld, hold, "deny")
                await rig.until(lambda: rig.kinds(ld, "hold.revealed"), 10)

    anyio.run(main)
    [reveal] = rig.kinds(ld, "hold.revealed")
    salt = bytes.fromhex(reveal["data"]["salt"])
    assert seen and len(salt) == 32
    for name, data in seen.items():
        assert salt not in data and salt.hex().encode() not in data, name


def _two_holds(ledger_dir: Path, commit: bool) -> Path:
    """The planted and the real hold of plantledger, open, with no reveal; with or without
    plant_commit."""
    ledger_dir.mkdir(parents=True)
    specs = [hl.started(hl.RUNNING), pl.policy_loaded(hl.RUNNING, 1)]
    for kind, data in [*pl.planted_hold(ledger_dir)[:1], *pl.real_hold(ledger_dir, False)]:
        keep = {k: v for k, v in data.items() if commit or k != "plant_commit"}
        specs.append((kind, keep))
    build_chain(ledger_dir, specs, head_at=0)
    hl.lock_files(ledger_dir, hl.RUNNING)
    return ledger_dir


def test_plant_commit_changes_no_reader(tmp_path, capsys, monkeypatch):
    """holds, allow and deny print the same stdout for holds with and without plant_commit, and
    hold.revealed changes no hold or ending in the fold."""
    monkeypatch.setattr(cli, "_now", lambda: hl.NOW)
    monkeypatch.setattr(decisions, "REVEAL_WAIT", 0.1)
    printed = {}
    for commit in (True, False):
        d = _two_holds(tmp_path / str(commit), commit)
        held = hl.hold_running(d)
        try:
            assert cli.main(["holds", "--ledger-dir", str(d)]) == 0
            out = [capsys.readouterr().out]
            for command, hold in (("allow", pl.REAL), ("deny", pl.PLANTED)):
                argv = [command, "--ledger-dir", str(d), hold, "--allow-no-terminal"]
                assert cli.main(argv) == 0
                out.append(capsys.readouterr().out)
        finally:
            held.close()
        printed[commit] = out
    assert printed[True] == printed[False]
    without, revealed = holds.HoldState(), holds.HoldState()
    d = _two_holds(tmp_path / "unrevealed", True)
    verify_bytes((d / "ledger.jsonl").read_bytes(), None, without.apply)
    d = pl.decide_ledger(tmp_path / "revealed")
    verify_bytes((d / "ledger.jsonl").read_bytes(), None, revealed.apply)

    def same(fold):
        return {h: replace(x, seq=0, ts="") for h, x in fold.snapshot().items()}

    assert same(without) == same(revealed)
    assert revealed.revealed(pl.PLANTED).label == "planted" and without.reveals() == {}


def test_allow_and_deny_print_the_reveal(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(cli, "_now", lambda: hl.NOW)
    cases = [
        ("allow_planted.txt", "allow", pl.PLANTED, True),
        ("deny_planted.txt", "deny", pl.PLANTED, True),
        ("allow_real_planting_on.txt", "allow", pl.REAL, True),
    ]
    for i, (name, command, hold, with_reveal) in enumerate(cases):
        d = pl.decide_ledger(tmp_path / f"l{i}", with_reveal)
        held = hl.hold_running(d)
        try:
            assert cli.main([command, "--ledger-dir", str(d), hold, "--allow-no-terminal"]) == 0
        finally:
            held.close()
        out, err = capsys.readouterr()
        assert err == ""
        golden(name, out)
    # No reveal within the wait: the stderr line after any session warning, exit 0.
    monkeypatch.setattr(decisions, "REVEAL_WAIT", 0.2)
    d = pl.decide_ledger(tmp_path / "late", with_reveal=False)
    assert cli.main(["allow", "--ledger-dir", str(d), pl.REAL, "--allow-no-terminal"]) == 0
    out, err = capsys.readouterr()
    assert out.splitlines()[-1].startswith(f"allowed hold {pl.REAL} at seq ")
    assert err.splitlines() == [holds.ENDED_WARNING, decisions.NO_REVEAL]


def test_planted_stderr_line_matches_real(tmp_path, capfd):
    fake, specs, pol, root = setup(tmp_path, n=1)
    ld = tmp_path / "ledger"
    _run_mixed(ld, specs, pol, root, tmp_path / "home")
    err = capfd.readouterr().err
    found = HELD_LINE.findall(err)
    assert sorted(found) == ["outside-roots", "outside-roots", "write-pattern"]
    assert planted.DISCLOSURE.format(n=1) in err


def test_stats_counts_planted(tmp_path, capsys):
    d = pl.stats_ledger(tmp_path / "ledger")
    assert cli.main(["stats", "--ledger-dir", str(d)]) == 0
    out = capsys.readouterr().out
    golden("stats_planted.txt", out)
    assert "stats: 3 holds; 2 sessions with holds, 2 without" in out
    assert "  approval: allowed 2 of 3 decided" in out
    bad = pl.stats_ledger(tmp_path / "bad", bad_reveal=True)
    assert cli.main(["stats", "--ledger-dir", str(bad)]) == 0
    out = capsys.readouterr().out
    assert (
        "  planted 1: denied 0 (caught), allowed 1 (missed), expired 0, not revealed 1, "
        "did not check 1\n" in out
    )


def test_one_terminal_entry_per_call_with_planted(tmp_path):
    """test_holds.py::test_one_terminal_entry_per_call with a planted hold in the run: every
    real held call still has one terminal entry, and no planted hold has a call.* entry."""
    fake, specs, pol, root = setup(tmp_path, n=1)
    ld = tmp_path / "ledger"
    _run_mixed(ld, specs, pol, root, tmp_path / "home")
    entries = rig.entries(ld)
    plants = set(planted_ids(ld))
    assert len(plants) == 1
    for e in entries:
        if e["kind"] != "hold.created":
            continue
        hold = e["data"]["hold"]
        sent = [x for x in entries if x["kind"] == "call.sent" and x["data"].get("hold") == hold]
        refused = [
            x for x in entries if x["kind"] == "call.refused" and x["data"].get("hold") == hold
        ]
        if hold in plants:
            assert sent == [] and refused == []
            continue
        assert len(sent) + len(refused) == 1, hold
        if sent:
            assert [x for x in entries if x["kind"] == "call.returned"
                    and x["data"]["call_seq"] == sent[0]["seq"]]  # fmt: skip


def test_plan_reads_the_trigger_and_never_the_client():
    """The structure the spec names: a Planter is built from the writer, folds, policy, session,
    open holds and two callbacks, and its module never calls the MCP SDK."""
    import inspect

    source = inspect.getsource(planted)
    assert "import mcp" not in source and "from mcp" not in source
    assert "client" not in inspect.signature(planted.Planter.__init__).parameters
    assert "session" not in str(inspect.signature(planted.Planter.issue))


def test_card_shows_a_planted_hold_like_a_real_one(tmp_path):
    """Until it is decided, the card shows a planted hold with exactly the fields and labels of
    a real one, and nothing that says planted; after a deny, the answer and the ended list carry
    the reveal, and the real hold's history leaves the planted one out."""
    import json as json_mod
    import threading

    from test_card import Client

    from polarizer import card

    d = pl.decide_ledger(tmp_path / "ledger")
    held = hl.hold_running(d)
    the_card = card.Card(d, now=lambda: hl.NOW)
    server = card.make_server(the_card, 0)
    threading.Thread(
        target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True
    ).start()
    try:
        client = Client(the_card, server)
        client.open_page()
        planted_card, real_card = client.state()["open"]
        assert planted_card["hold"] == pl.PLANTED and real_card["hold"] == pl.REAL
        assert set(planted_card) == set(real_card)
        assert [label for label, _ in planted_card["rows"]] == [
            label for label, _ in real_card["rows"]
        ]
        assert "plant" not in json_mod.dumps(planted_card).lower()
        status, answer = client.decide(pl.PLANTED, "deny")
        assert status == 200, answer
        assert answer["reveal"][-1] == "You denied it: caught."
        assert answer["reveal"][0].startswith("Planted call:")
        state = client.state()
        [ended] = state["ended"]
        assert ended["hold"] == pl.PLANTED and ended["reveal"][-1] == "You denied it: caught."
        [real_now] = state["open"]
        rows = dict((label, lines) for label, lines in real_now["rows"])
        assert rows["Earlier holds"] == [
            "none: this is the first hold of this tool by outside-roots"
        ]
    finally:
        server.shutdown()
        server.server_close()
        held.close()
