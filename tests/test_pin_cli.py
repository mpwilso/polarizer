"""The pin commands beyond their golden files: binding a group to what was printed, approving
only what serve saw, ledger.head, read-only pending, forbidden ledger directories, the real CLI
in a subprocess, and the terminal check (docs/PIN-SPEC.md, section 7)."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import install_fixture
from helpers import pinledger
from test_verify_readonly import listing

from polarizer import cli
from polarizer.decisions import Decider
from polarizer.ledger import parse_head
from polarizer.writer import LedgerWriter, Stopped

GOLDEN = Path(__file__).parent / "golden"


def entries(directory):
    return [json.loads(line) for line in (directory / "ledger.jsonl").read_bytes().splitlines()]


def run(*argv, terminal=False):
    return cli.main([*argv] if terminal else [*argv, "--allow-no-terminal"])


def group_id(directory, capsys):
    assert cli.main(["pending", "--ledger-dir", str(directory)]) == 0
    line = capsys.readouterr().out.splitlines()[-1]
    return line.split()[1]


def append(directory, kind, data):
    writer = LedgerWriter.open(directory)
    try:
        writer.append(kind, data).result()
    finally:
        writer.close()


def test_group_is_bound_to_what_was_printed(tmp_path, capsys, fake_home):
    """A new tool, or a grouped tool seen with another definition, between pending and approve
    --group refuses the group and writes nothing. A tool with any decision is never in a group,
    so its changed definitions can't move the group either way."""
    for change in ("new tool", "grouped tool changed"):
        directory = pinledger.mixed(tmp_path / change.replace(" ", "-"))
        printed = group_id(directory, capsys)
        if change == "new tool":
            pinledger.store(directory, "echo_v1")
            data = {"upstream": "other", "tool": "echo", "def_hash": pinledger.hashed("echo_v1")}
            append(directory, "tool.seen", {"session": pinledger.SESSION, **data})
        else:
            data = {"upstream": "probe", "tool": "wait", "def_hash": pinledger.hashed("note")}
            append(directory, "tool.seen", {"session": pinledger.SESSION, **data})
        before = (directory / "ledger.jsonl").read_bytes()
        assert run("approve", "--ledger-dir", str(directory), "--group", printed) == 2
        out, err = capsys.readouterr()
        assert out == "" and "does not match what is pending now" in err
        assert (directory / "ledger.jsonl").read_bytes() == before

    directory = pinledger.mixed(tmp_path / "decided")
    decider = Decider.open(directory)
    try:
        members, _ = decider.pending_group()
    finally:
        decider.close()
    # probe/fail (rejected before), every/echo (approved before) and probe/rich (bad copy)
    # are left out.
    assert [(b.upstream, b.tool) for b in members] == [("probe", "note"), ("probe", "wait")]


def test_group_skips_tools_with_several_waiting_hashes(tmp_path, capsys, fake_home):
    """A tool with no decision and two definitions waiting is left out of the group line and
    the group id, each of its blocks says to approve one by name, and approving one by name
    gives it a decision."""
    from polarizer.pins import SEVERAL

    directory = pinledger.several(tmp_path / "ledger")
    assert cli.main(["pending", "--ledger-dir", str(directory)]) == 0
    lines = capsys.readouterr().out.splitlines()
    v1, v2, wait = (pinledger.hashed(k) for k in ("echo_v1", "echo_v2", "wait"))
    for h in (v1, v2):
        header = lines.index(f"new probe__echo {h}")
        assert lines[header + 1] == SEVERAL
    assert lines[lines.index(f"new probe__wait {wait}") + 1] == "{"
    assert lines[-1].endswith(
        "covers the 1 new definitions above for tools with no decision yet and one definition "
        "waiting"
    )
    decider = Decider.open(directory)
    try:
        members, gid = decider.pending_group()
    finally:
        decider.close()
    assert [(b.tool, b.def_hash) for b in members] == [("wait", wait)]
    assert lines[-1].split()[1] == gid
    assert run("approve", "--ledger-dir", str(directory), "--group", gid) == 0
    approved = [e["data"] for e in entries(directory) if e["kind"] == "tool.approved"]
    assert [(d["tool"], d["def_hash"], d["group"]) for d in approved] == [("wait", wait, gid)]
    capsys.readouterr()
    assert cli.main(["pending", "--ledger-dir", str(directory)]) == 0
    out = capsys.readouterr().out
    assert out.startswith("pending: 2 new, 0 changed, 0 unservable\n") and "group " not in out
    assert run("approve", "--ledger-dir", str(directory), "probe", "echo", v2) == 0
    capsys.readouterr()
    assert cli.main(["pending", "--ledger-dir", str(directory)]) == 0
    assert capsys.readouterr().out == "pending: nothing waits for a decision\n"


def test_pending_escapes_problem_text(tmp_path, capsys, monkeypatch):
    """Control characters, DEL and non-ASCII text from the ledger print as escapes: in a
    problem, in the operating system's message of a stored-copy problem, and in names. A
    definition's DEL prints as an escape too."""
    import mcp_types as types
    from conftest import build_chain

    from polarizer import defhash

    bs, esc, delete = chr(92), chr(27), chr(0x7F)
    session = {"session": pinledger.SESSION}
    odd = types.Tool(name="odd", description="a" + delete + "b", input_schema={"type": "object"})
    odd_hash, canon = defhash.definition(odd)
    unreadable = pinledger.hashed("wait")
    problem = "cannot be hashed: " + esc + "[2J" + delete + chr(0xE9) + chr(0x200B) + chr(0x1F600)
    specs = [
        ("tool.seen", {**session, "upstream": "p", "tool": "odd", "def_hash": odd_hash}),
        ("tool.unservable", {**session, "upstream": "p", "tool": "t" + esc, "def_hash": None,
                             "problem": problem}),
        ("tool.unservable", {**session, "upstream": "p", "tool": "w", "def_hash": unreadable,
                             "problem": "stored copy unreadable: then"}),
    ]  # fmt: skip
    directory = tmp_path / "ledger"
    build_chain(directory, specs, head_at=0)
    defhash.write_copy(directory, odd_hash, canon)
    real = defhash.read_copy

    def read_copy(ledger_dir, def_hash):
        if def_hash == unreadable:
            message = "Acc" + chr(0xE8) + "s refus" + chr(0xE9) + esc + "[0m"
            raise defhash.CopyProblem(f"unreadable: {message}")
        return real(ledger_dir, def_hash)

    monkeypatch.setattr(defhash, "read_copy", read_copy)
    assert cli.main(["pending", "--ledger-dir", str(directory)]) == 0
    out = capsys.readouterr().out
    assert all(" " <= ch <= "~" or ch == "\n" for ch in out), out
    lines = out.splitlines()
    assert f'  "description": "a{bs}u007fb",' in lines
    assert (
        f"unservable p__t{bs}x1b -: cannot be hashed: {bs}x1b[2J{bs}x7f{bs}xe9{bs}u200b"
        f"{bs}ud83d{bs}ude00"
    ) in lines
    assert (
        f"unservable p__w {unreadable}: stored copy defs/{unreadable}.json unreadable: "
        f"Acc{bs}xe8s refus{bs}xe9{bs}x1b[0m"
    ) in lines


def test_approve_only_what_serve_saw(tmp_path, capsys, fake_home):
    directory = pinledger.mixed(tmp_path / "ledger")
    pinledger.store(directory, "add")  # a stored copy alone is not enough
    h = pinledger.hashed("add")
    assert run("approve", "--ledger-dir", str(directory), "probe", "wait", h) == 2
    assert capsys.readouterr().err == (
        f"polarizer: Polarizer has not seen probe__wait with definition {h}\n"
    )
    # A hash approved before for that tool may be approved again (re-pinning after drift).
    h1 = pinledger.hashed("echo_v1")
    assert run("approve", "--ledger-dir", str(directory), "every", "echo", h1) == 0
    assert capsys.readouterr().out.splitlines()[-1].startswith(f"approved every__echo {h1} at seq")


def test_approve_updates_head(tmp_path, capsys, fake_home):
    directory = pinledger.mixed(tmp_path / "ledger")
    h = pinledger.hashed("wait")
    assert run("approve", "--ledger-dir", str(directory), "probe", "wait", h) == 0
    last = entries(directory)[-1]
    assert last["kind"] == "tool.approved"
    head = parse_head((directory / "ledger.head").read_bytes())
    assert (head.seq, head.hash) == (last["seq"], last["hash"])


def test_reject_updates_head_and_can_revoke(tmp_path, capsys, fake_home):
    """reject is fsynced with ledger.head updated before it reports success, and may name
    the currently approved hash, which unpins it."""
    directory = pinledger.mixed(tmp_path / "ledger")
    h = pinledger.hashed("add")
    argv = ["reject", "--ledger-dir", str(directory), "every", "add", h, "--reason", "revoked"]
    assert run(*argv) == 0
    last = entries(directory)[-1]
    assert (last["kind"], last["data"]["def_hash"]) == ("tool.rejected", h)
    assert parse_head((directory / "ledger.head").read_bytes()).seq == last["seq"]


def test_pending_writes_nothing(tmp_path, capsys):
    directory = pinledger.mixed(tmp_path / "ledger")
    before = listing(directory)
    for extra in ([], ["--upstream", "every"]):
        assert cli.main(["pending", "--ledger-dir", str(directory), *extra]) == 0
    assert listing(directory) == before
    missing = tmp_path / "missing"
    assert cli.main(["pending", "--ledger-dir", str(missing)]) == 2
    assert not missing.exists()


def test_pin_commands_refuse_forbidden_ledger_dir(tmp_path, capsys, fake_home):
    directory = pinledger.mixed(fake_home / ".config" / "parallax" / "ledger")
    before = (directory / "ledger.jsonl").read_bytes()
    forbidden = fake_home / ".config" / "parallax"
    line = f"polarizer: ledger_dir {directory} is inside {forbidden}, which Polarizer must not write to\n"
    h = pinledger.hashed("wait")
    for argv in (
        ["approve", "--ledger-dir", str(directory), "probe", "wait", h],
        ["reject", "--ledger-dir", str(directory), "probe", "wait", h, "--reason", "r"],
    ):
        assert run(*argv) == 2
        out, err = capsys.readouterr()
        assert (out, err) == ("", line)
    assert (directory / "ledger.jsonl").read_bytes() == before
    # pending only reads, like verify, and checks no locations.
    assert cli.main(["pending", "--ledger-dir", str(directory)]) == 0


def test_decisions_need_a_ledger(tmp_path, capsys, fake_home):
    missing = tmp_path / "missing"
    h = pinledger.hashed("wait")
    for argv in (
        ["approve", "--ledger-dir", str(missing), "probe", "wait", h],
        ["approve", "--ledger-dir", str(missing), "--group", h],
        ["reject", "--ledger-dir", str(missing), "probe", "wait", h, "--reason", "r"],
    ):
        assert run(*argv) == 2
        assert capsys.readouterr().err == f"polarizer: no ledger at {missing}\n"
    assert not missing.exists()


def test_group_writer_fails_partway(tmp_path, capsys, fake_home, monkeypatch):
    """The lines already printed stand, stderr says how many were approved, and the exit code
    is the writer error's own."""
    directory = pinledger.mixed(tmp_path / "ledger")
    printed = group_id(directory, capsys)
    real = Decider._record
    calls = []

    def failing(self, kind, data):
        calls.append(kind)
        if len(calls) == 2:
            raise Stopped("polarizer: stopped writing the ledger: test; run polarizer verify", 1)
        return real(self, kind, data)

    monkeypatch.setattr(Decider, "_record", failing)
    assert run("approve", "--ledger-dir", str(directory), "--group", printed) == 1
    out, err = capsys.readouterr()
    assert out == f"approved probe__note {pinledger.hashed('note')} at seq 16\n"
    assert err == (
        "polarizer: could not record an approval: polarizer: stopped writing the ledger: test; "
        "run polarizer verify; 1 of 2 were approved\n"
    )
    assert [e["kind"] for e in entries(directory)].count("tool.approved") == 3


def _python(*args, **kwargs):
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONUTF8", "PYTHONIOENCODING")}
    env["PYTHONIOENCODING"] = "ascii:strict"  # a hostile default; the CLI must override it
    return subprocess.run([sys.executable, "-m", "polarizer", *args], env=env, timeout=60, **kwargs)


def test_pin_commands_subprocess(tmp_path, fake_home):
    """The real CLI prints the golden bytes, with the non-ASCII description as escapes."""
    directory = pinledger.mixed(tmp_path / "ledger")
    done = _python("pending", "--ledger-dir", str(directory), capture_output=True)
    assert done.returncode == 0 and done.stderr == b""
    assert b"\r" not in done.stdout
    assert done.stdout == (GOLDEN / "pending_mixed.txt").read_bytes()
    assert b"Caf\\u00e9 note,\\u200b" in done.stdout
    h = pinledger.hashed("note")
    done = _python(
        "approve", "--ledger-dir", str(directory), "probe", "note", h, "--allow-no-terminal",
        capture_output=True, stdin=subprocess.DEVNULL,
    )  # fmt: skip
    assert done.returncode == 0, done.stderr
    assert done.stdout.replace(str(directory).encode(), b"<dir>") == (
        (GOLDEN / "approve_one.txt").read_bytes()
    )


def test_approve_needs_terminal(tmp_path, capsys, fake_home, monkeypatch):
    """Without a terminal, approve, approve --group and reject refuse and write nothing; with
    --allow-no-terminal they run."""
    monkeypatch.setattr(cli, "_stdin_is_terminal", lambda: False)
    directory = pinledger.mixed(tmp_path / "ledger")
    printed = group_id(directory, capsys)
    h = pinledger.hashed("note")
    before = (directory / "ledger.jsonl").read_bytes()
    cases = {
        "approve": ["approve", "--ledger-dir", str(directory), "probe", "note", h],
        "approve --group": ["approve", "--ledger-dir", str(directory), "--group", printed],
        "reject": ["reject", "--ledger-dir", str(directory), "probe", "note", h, "--reason", "r"],
    }
    for name, argv in cases.items():
        assert cli.main(argv) == 2, name
        command = name.split()[0]
        assert capsys.readouterr().err == (
            f"polarizer: {command} needs a terminal; pass --allow-no-terminal if this is a script\n"
        )
    assert (directory / "ledger.jsonl").read_bytes() == before
    assert cli.main([*cases["approve --group"], "--allow-no-terminal"]) == 0


@pytest.mark.skipif(sys.platform == "win32", reason="Windows has no pty module")
def test_approve_runs_on_a_terminal(tmp_path, fake_home):
    """On a pseudo-terminal, approve runs without the flag."""
    import pty

    directory = pinledger.mixed(tmp_path / "ledger")
    h = pinledger.hashed("note")
    controller, terminal = pty.openpty()
    try:
        done = _python(
            "approve", "--ledger-dir", str(directory), "probe", "note", h,
            stdin=terminal, capture_output=True,
        )  # fmt: skip
    finally:
        os.close(terminal)
        os.close(controller)
    assert done.returncode == 0, done.stderr
    assert done.stdout.splitlines()[-1].startswith(b"approved probe__note ")


def test_pending_on_a_broken_ledger_prints_verify(tmp_path, capsys):
    directory = install_fixture("broken/edit_value", tmp_path / "ledger")
    assert cli.main(["pending", "--ledger-dir", str(directory)]) == 1
    pending_out = capsys.readouterr().out
    assert cli.main(["verify", "--ledger-dir", str(directory)]) == 1
    assert capsys.readouterr().out == pending_out
