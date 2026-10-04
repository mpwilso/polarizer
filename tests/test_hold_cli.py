"""The hold commands beyond their golden files (docs/HOLD-SPEC.md, section 8): escaping what the
ledger says, the terminal check, forbidden ledger directories, two allows at once, and the real
CLI as a subprocess."""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import build_chain
from helpers import holdledger as hl

from polarizer import cli

GOLDEN = Path(__file__).parent / "golden"
POSIX = pytest.mark.skipif(sys.platform == "win32", reason="Windows has no pty module")


def run(*argv, terminal=False):
    return cli.main([*argv] if terminal else [*argv, "--allow-no-terminal"])


def _python(*args, **kwargs):
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONUTF8", "PYTHONIOENCODING")}
    env["PYTHONIOENCODING"] = "ascii:strict"  # a hostile default; the CLI must override it
    return subprocess.run([sys.executable, "-m", "polarizer", *args], env=env, timeout=60, **kwargs)


def test_holds_escapes_ledger_text(tmp_path, capsys, monkeypatch):
    """Control characters, DEL and non-ASCII text in a hand-written hold's tool, rule, reason
    and session print as escapes."""
    monkeypatch.setattr(cli, "_now", lambda: hl.NOW)
    directory = tmp_path / "ledger"
    directory.mkdir()
    commit = hl.side_file(directory, {"a": 1}, 1)
    odd = "x" + chr(0x1B) + "[2J" + chr(0x7F) + chr(0xE9) + chr(0x202E) + chr(0x1F600)
    entry = ("hold.created", {"session": "s" + odd, "hold": "h" + odd, "tool": "t" + odd,
                              "args_commit": commit, "class": "c" + odd, "class_from": "config",
                              "rule": "r" + odd, "reason": "why" + odd + "\nnext",
                              "timeout_seconds": 300})  # fmt: skip
    build_chain(directory, [("session.started", {"session": "s" + odd}), entry])
    assert cli.main(["holds", "--ledger-dir", str(directory)]) == 0
    out = capsys.readouterr().out
    shown = "x\\x1b[2J\\x7f\\xe9\\u202e\\ud83d\\ude00"
    lines = out.splitlines()
    assert lines[2] == f"hold h{shown} t{shown} c{shown}"
    assert lines[3] == f"held by r{shown}: why{shown}\\x0anext"
    assert lines[5] == f"session s{shown} started 2026-10-02T15:00:00.001Z, state unknown"
    assert out.isascii() and all(0x20 <= ord(c) < 0x7F for c in out.replace("\n", ""))


def test_allow_needs_terminal(tmp_path, capsys, fake_home, monkeypatch):
    """allow and deny without a terminal refuse with the one line, exit 2, and write nothing;
    with --allow-no-terminal they run."""
    monkeypatch.setattr(cli, "_stdin_is_terminal", lambda: False)
    monkeypatch.setattr(cli, "_now", lambda: hl.NOW)
    directory = hl.mixed(tmp_path / "ledger")
    before = (directory / "ledger.jsonl").read_bytes()
    for command, hold in (("allow", hl.WRITE_PATTERN), ("deny", hl.UNCLASSIFIED)):
        assert run(command, "--ledger-dir", str(directory), hold, terminal=True) == 2
        out, err = capsys.readouterr()
        assert (out, err) == (
            "",
            f"polarizer: {command} needs a terminal; pass --allow-no-terminal if this is a script\n",
        )
        assert (directory / "ledger.jsonl").read_bytes() == before
    for command, hold in (("allow", hl.WRITE_PATTERN), ("deny", hl.UNCLASSIFIED)):
        assert run(command, "--ledger-dir", str(directory), hold) == 0
    capsys.readouterr()


@POSIX
def test_allow_runs_on_a_terminal(tmp_path, fake_home):
    """On a pseudo-terminal, allow runs without the flag."""
    import pty

    directory = hl.mixed(tmp_path / "ledger")
    controller, terminal = pty.openpty()
    try:
        done = _python(
            "allow", "--ledger-dir", str(directory), hl.WRITE_PATTERN,
            stdin=terminal, capture_output=True,
        )  # fmt: skip
    finally:
        os.close(terminal)
        os.close(controller)
    assert done.returncode == 0, done.stderr
    assert done.stdout.splitlines()[-1].startswith(f"allowed hold {hl.WRITE_PATTERN} ".encode())


def test_hold_commands_refuse_forbidden_ledger_dir(tmp_path, capsys, fake_home):
    directory = hl.mixed(fake_home / ".config" / "parallax" / "ledger")
    before = (directory / "ledger.jsonl").read_bytes()
    forbidden = fake_home / ".config" / "parallax"
    line = f"polarizer: ledger_dir {directory} is inside {forbidden}, which Polarizer must not write to\n"
    for argv in (
        ["allow", "--ledger-dir", str(directory), hl.WRITE_PATTERN],
        ["deny", "--ledger-dir", str(directory), hl.WRITE_PATTERN, "--reason", "r"],
    ):
        assert run(*argv) == 2
        out, err = capsys.readouterr()
        assert (out, err) == ("", line)
    assert (directory / "ledger.jsonl").read_bytes() == before
    # holds only reads, like verify and pending, and checks no locations.
    assert cli.main(["holds", "--ledger-dir", str(directory)]) == 0
    assert capsys.readouterr().out.startswith("holds: 3 open\n")


def test_hold_decisions_need_a_ledger(tmp_path, capsys, fake_home):
    missing = tmp_path / "missing"
    for command in ("allow", "deny"):
        assert run(command, "--ledger-dir", str(missing), hl.WRITE_PATTERN) == 2
        assert capsys.readouterr() == ("", f"polarizer: no ledger at {missing}\n")


@pytest.mark.parametrize("decisions", [("allow", "allow"), ("allow", "deny")])
def test_concurrent_allows_write_one_decision(tmp_path, fake_home, decisions):
    """Two decision processes for one hold at once: exactly one hold.decided is written, and
    the other is refused as already decided."""
    directory = hl.mixed(tmp_path / "ledger")
    procs = [
        subprocess.Popen(
            [
                sys.executable,
                "-m",
                "polarizer",
                command,
                "--ledger-dir",
                str(directory),
                hl.WRITE_PATTERN,
                "--allow-no-terminal",
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )  # fmt: skip
        for command in decisions
    ]
    results = [(p.wait(timeout=60), *p.communicate(timeout=60)) for p in procs]
    codes = sorted(code for code, _, _ in results)
    assert codes == [0, 2], results
    decided = [
        json.loads(line)
        for line in (directory / "ledger.jsonl").read_bytes().splitlines()
        if b'"hold.decided"' in line and hl.WRITE_PATTERN.encode() in line
    ]
    assert len(decided) == 1
    loser = next(err for code, _, err in results if code == 2).decode()
    assert re.fullmatch(
        rf"polarizer: hold {hl.WRITE_PATTERN} was already decided at seq {decided[0]['seq']}: "
        rf"{decided[0]['data']['decision']}\n",
        loser,
    )


def test_hold_commands_subprocess(tmp_path, fake_home):
    """The real CLI prints holds_mixed.txt's bytes, its arguments' non-ASCII text, control
    character and DEL as escapes, with a running session's lock held by this process. Its clock
    is the real one, so the age lines are compared by form, and the rest byte for byte."""
    directory = hl.mixed(tmp_path / "ledger")
    lock = hl.hold_running(directory)
    try:
        done = _python("holds", "--ledger-dir", str(directory), capture_output=True)
    finally:
        lock.close()
    assert done.returncode == 0 and done.stderr == b""
    assert b"\r" not in done.stdout
    age = re.compile(rb"waiting about (\d+h\d\dm|\d+m\d\ds); times out after 300 s")
    got = done.stdout.split(b"\n")
    want = (GOLDEN / "holds_mixed.txt").read_bytes().split(b"\n")
    assert len(got) == len(want)
    for g, w in zip(got, want, strict=True):
        if w.startswith(b"waiting about "):
            assert age.fullmatch(g), g
        else:
            assert g == w
    assert b'"content": "caf\\u00e9 \\u001b[31m red\\u007f"' in done.stdout
    # allow through the CLI, then holds again: the allowed hold is gone from the listing.
    done = _python(
        "allow", "--ledger-dir", str(directory), hl.WRITE_PATTERN, "--allow-no-terminal",
        capture_output=True, stdin=subprocess.DEVNULL,
    )  # fmt: skip
    assert done.returncode == 0, done.stderr
    assert done.stderr == (
        b"polarizer: warning: the session that held this call has ended; "
        b"no process will act on this decision\n"
    )
    later = _python("holds", "--ledger-dir", str(directory), capture_output=True)
    assert later.stdout.startswith(b"holds: 2 open\n")
    assert hl.WRITE_PATTERN.encode() not in later.stdout
