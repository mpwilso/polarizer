"""scripts/m1a-check.sh runs the M1a manual check, and scripts/m1a_check.py reads its inputs.

The parsing tests run on pending output made by the real CLI, and on the golden files, on every
platform. The script tests run it on a pseudo-terminal with scripted answers, POSIX only, with no
Claude Code: a `serve` run with stdin closed stands in for Claude Code starting Polarizer, and a
second one, with the probe's rug-pull file present, for reconnecting it from /mcp. Everything the
script writes is in the test's own directory (POLARIZER_CHECK_TESTING=1)."""

import json
import os
import platform
import re
import select
import shutil
import subprocess
import sys
import time
import warnings
from pathlib import Path

import mcp_types as types
import pytest
from conftest import build_chain

from polarizer import cli, defhash

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "m1a-check.sh"
GOLDEN = Path(__file__).resolve().parent / "golden"
sys.path.insert(0, str(ROOT / "scripts"))
import m1a_check  # noqa: E402

SESSION = "5e55105e55105e55"
WAIT = types.Tool(
    name="wait",
    description="Wait for a number of seconds.",
    input_schema={"type": "object", "properties": {"seconds": {"type": "number"}}},
)
WAIT_CHANGED = WAIT.model_copy(
    update={"description": "Wait for a number of seconds. Changed after approval."}
)
READ = types.Tool(
    name="read",
    description="Read a file.",
    input_schema={"type": "object"},
    annotations=types.ToolAnnotations(read_only_hint=True, destructive_hint=False),
)
POSIX = pytest.mark.skipif(
    sys.platform == "win32", reason="the script needs bash, pgrep and a pty, which Windows lacks"
)


def hashed(tool):
    return defhash.definition(tool)[0]


def ledger(directory, specs, *tools):
    build_chain(directory, specs, head_at=len(specs))
    for tool in tools:
        defhash.write_copy(directory, *defhash.definition(tool))
    return directory


def seen(upstream, tool):
    data = {"session": SESSION, "upstream": upstream, "tool": tool.name, "def_hash": hashed(tool)}
    return ("tool.seen", data)


def pending(directory, capsys):
    assert cli.main(["pending", "--ledger-dir", str(directory)]) == 0
    return capsys.readouterr().out


def run_helper(capsys, function, *args):
    code = function(*args)
    out = capsys.readouterr()
    return code, out.out, out.err


# --- scripts/m1a_check.py, on every platform


def test_group_of_a_fresh_first_run(tmp_path, capsys):
    """The group id is pending's, and each tool is listed with its annotations."""
    directory = ledger(tmp_path / "l", [seen("fs", READ), seen("probe", WAIT)], READ, WAIT)
    text = pending(directory, capsys)
    code, out, err = run_helper(capsys, m1a_check.group, text, 2)
    assert code == 0, err
    assert f"group {out.strip()} covers the 2 new definitions" in text
    assert "new definitions: 2 (the check expects 2)" in err
    assert "fs__read     read-only: yes, destructive: no" in err
    assert "probe__wait  read-only: not set, destructive: not set" in err
    assert "tools with more than one waiting hash: none" in err


def test_group_refuses_what_a_fresh_run_would_not_list(tmp_path, capsys):
    directory = ledger(tmp_path / "l", [seen("fs", READ), seen("probe", WAIT)], READ, WAIT)
    code, out, err = run_helper(capsys, m1a_check.group, pending(directory, capsys), 21)
    assert (code, out) == (1, "")
    assert "pending lists 2 new, 0 changed and 0 unservable; a fresh first run lists 21" in err
    assert "the group covers 2 definitions, not 21" in err

    mixed = (GOLDEN / "pending_mixed.txt").read_text(encoding="utf-8")
    code, out, err = run_helper(capsys, m1a_check.group, mixed, 4)
    assert (code, out) == (1, "")
    assert "4 new, 1 changed and 1 unservable" in err
    assert "probe__fail: not in a group, because this tool has a decision" in err
    assert "probe__rich: stored copy defs/" in err

    several = (GOLDEN / "pending_several_waiting.txt").read_text(encoding="utf-8")
    code, out, err = run_helper(capsys, m1a_check.group, several, 3)
    assert (code, out) == (1, "")
    assert "tools with more than one waiting hash: probe__echo" in err
    assert "the probe started more than once" not in err

    twice = ledger(
        tmp_path / "t", [seen("probe", WAIT), seen("probe", WAIT_CHANGED)], WAIT, WAIT_CHANGED
    )
    code, out, err = run_helper(capsys, m1a_check.group, pending(twice, capsys), 1)
    assert (code, out) == (1, "")
    assert "tools with more than one waiting hash: probe__wait" in err
    assert "so the probe started more than once before this approval" in err

    nothing = (GOLDEN / "pending_nothing.txt").read_text(encoding="utf-8")
    assert run_helper(capsys, m1a_check.group, nothing, 21)[:2] == (3, "")


def test_changed_probe_wait(tmp_path, capsys):
    """After a rug pull, the new and the approved hash, and the new description."""
    approved = {"upstream": "probe", "tool": "wait", "def_hash": hashed(WAIT), "actor": "person"}
    drift = {"session": SESSION, "upstream": "probe", "tool": "wait",
             "approved_hash": hashed(WAIT), "live_hash": hashed(WAIT_CHANGED)}  # fmt: skip
    specs = [seen("probe", WAIT), ("tool.approved", {**approved, "group": "a" * 64})]
    specs.append(("tool.drift", drift))
    directory = ledger(tmp_path / "l", specs, WAIT, WAIT_CHANGED)
    code, out, err = run_helper(capsys, m1a_check.changed, pending(directory, capsys))
    assert code == 0, err
    assert out == f"{hashed(WAIT_CHANGED)} {hashed(WAIT)}\n"
    assert 'new description: "Wait for a number of seconds. Changed after approval."' in err
    assert m1a_check.count(directory, "drift") == 1
    assert m1a_check.count(directory, "group-approved") == 1
    assert m1a_check.count(directory, "wait-approved-alone") == 0

    mixed = (GOLDEN / "pending_mixed.txt").read_text(encoding="utf-8")
    code, out, err = run_helper(capsys, m1a_check.changed, mixed)
    assert (code, out) == (1, "")
    assert "pending shows 0 changed probe__wait blocks; the check expects 1" in err


def test_ledger_counts_and_last_refusal(tmp_path):
    refused = {"session": SESSION, "tool": "probe__w" + chr(0xE9) + chr(0x1B),
               "reason": 'upstream probe tool "wait" changed after approval'}  # fmt: skip
    drift = {"session": SESSION, "upstream": "probe", "tool": "wait",
             "approved_hash": hashed(WAIT), "live_hash": hashed(WAIT_CHANGED)}  # fmt: skip
    alone = {"upstream": "probe", "tool": "wait", "def_hash": hashed(WAIT_CHANGED),
             "actor": "person", "group": None}  # fmt: skip
    specs = [("tool.drift", drift), ("call.refused", refused), ("tool.approved", alone)]
    directory = ledger(tmp_path / "l", specs)
    assert m1a_check.count(directory, "wait-approved-alone") == 1
    line = m1a_check.last_refused(directory)
    assert line.isascii() and line.startswith("{") and '"kind":"call.refused"' in line
    assert "probe__w\\xe9\\u001b" in line  # the ledger escapes ESC itself
    assert m1a_check.last_refused(tmp_path / "none") == "none"
    assert m1a_check.count(tmp_path / "none", "drift") == 0


def test_toml_values(tmp_path, fake_home):
    toml = tmp_path / "polarizer.toml"
    toml.write_text(
        'ledger_dir = "~/.local/share/polarizer-m1a-check"\n'
        "[upstream.probe]\n"
        'command = "python"\n'
        'env = { PROBE_LOG = "/tmp/x.log", PROBE_RUGPULL = "/tmp/polarizer-rugpull" }\n'
        "[upstream.fs]\n"
        'command = "npx"\n',
        encoding="utf-8",
    )
    ledger_dir = str(fake_home / ".local" / "share" / "polarizer-m1a-check")
    assert m1a_check.toml_value(toml, "ledger_dir") == ledger_dir
    assert m1a_check.toml_value(toml, "rugpull") == "/tmp/polarizer-rugpull"
    assert m1a_check.toml_value(toml, "expected") == "21"
    toml.write_text('[upstream.other]\ncommand = "x"\n', encoding="utf-8")
    assert m1a_check.toml_value(toml, "ledger_dir") == ""
    assert m1a_check.toml_value(toml, "rugpull") == ""
    with pytest.raises(ValueError, match="how many tools other lists"):
        m1a_check.toml_value(toml, "expected")


def test_example_toml_expects_21():
    """The real example lists the probe and the Filesystem server: 7 and 14 tools."""
    assert m1a_check.toml_value(ROOT / "polarizer.example.toml", "expected") == "21"


# --- scripts/m1a-check.sh on a pseudo-terminal, POSIX only


def _take_terminal():
    import fcntl
    import termios

    fcntl.ioctl(0, termios.TIOCSCTTY, 0)


# read(2)'s number in /proc/<pid>/syscall.
READ_SYSCALL = {"x86_64": "0", "aarch64": "63"}
# Where the blocked read can't be seen for sure, how long to look before typing Ctrl+C anyway.
UNSURE_WAIT = 5


def _reading_terminal(pid):
    """Whether pid is asleep in read(2) on its standard input, the terminal: True or False on
    Linux, from /proc/<pid>/syscall; elsewhere True when ps shows the BSD wait channel "ttyin",
    and None otherwise, because that is untested here."""
    if sys.platform == "linux" and platform.machine() in READ_SYSCALL:
        try:
            fields = Path(f"/proc/{pid}/syscall").read_text(encoding="ascii").split()
        except OSError:
            return False
        return fields[:2] == [READ_SYSCALL[platform.machine()], "0x0"]
    found = subprocess.run(["ps", "-o", "wchan=", "-p", str(pid)], capture_output=True, text=True)
    return True if found.stdout.strip() == "ttyin" else None


def drive(env, command, replies=(), timeout=60):
    """Run `scripts/m1a-check.sh <command>` on a pseudo-terminal of its own, as its controlling
    terminal, so Ctrl+C (b"\\x03") reaches it as it would from a keyboard. replies: (text, what
    to type) pairs; each is typed once its text has appeared, in order. Returns the exit code
    and the output, with the terminal's echo of what was typed.

    A Ctrl+C waits, as well, until the script is asleep in its read. bash runs its INT trap
    between commands and when the signal interrupts read(2), but a SIGINT that lands inside the
    read builtin after its last check and before read(2) blocks only sets a flag, and the
    terminal turns ^C into the signal without queuing a byte, so nothing wakes the read: the
    script sits there until the next key (CI run 37221165322, Linux, Python 3.13)."""
    import pty

    controller, terminal = pty.openpty()
    proc = subprocess.Popen(
        ["bash", str(SCRIPT), command],
        stdin=terminal, stdout=terminal, stderr=terminal, cwd=ROOT, env=env,
        start_new_session=True, preexec_fn=_take_terminal,
    )  # fmt: skip
    os.close(terminal)
    output, cursor, todo, asked = b"", 0, list(replies), None
    deadline = time.monotonic() + timeout
    try:
        while time.monotonic() < deadline:
            if todo and asked is None:
                at = output.find(todo[0][0].encode(), cursor)
                if at >= 0:
                    cursor = at + len(todo[0][0])
                    asked = time.monotonic()
            if asked is not None:
                ready = b"\x03" not in todo[0][1] or _reading_terminal(proc.pid)
                if ready is None and time.monotonic() - asked > UNSURE_WAIT:
                    unsure = f"could not see m1a-check.sh asleep in its read on {sys.platform}"
                    warnings.warn(f"{unsure}; typed Ctrl+C anyway", stacklevel=2)
                    ready = True
                if ready:
                    os.write(controller, todo.pop(0)[1])
                    asked = None
            if select.select([controller], [], [], 0.1 if asked is None else 0.01)[0]:
                try:
                    chunk = os.read(controller, 4096)
                except OSError:  # EIO: everything holding the terminal has closed it
                    break
                if not chunk:
                    break
                output += chunk
        else:
            proc.kill()
            waiting = (
                f", never seen asleep in its read after {todo[0][0]!r}" if asked is not None else ""
            )
            pytest.fail(
                f"m1a-check.sh {command} still running after {timeout} s{waiting}:\n{output!r}"
            )
        code = proc.wait(timeout=30)
    finally:
        os.close(controller)
    assert not todo, f"never asked {todo[0][0]!r}:\n{output.decode()}"
    return code, output.decode("utf-8", "replace").replace("\r\n", "\n")


@pytest.fixture
def check(tmp_path, fake_home):
    """A check directory: the probe as the only upstream, the example toml next to it, and the
    environment that points the script there."""
    base = tmp_path / "check"
    base.mkdir()
    shutil.copy(ROOT / "tests" / "helpers" / "probe_server.py", base / "probe_server.py")
    (base / "polarizer.example.toml").write_text(
        'ledger_dir = "~/.local/share/polarizer-manual"\n'
        "ledger_forbidden_paths = []\n\n"
        "[upstream.probe]\n"
        f'command = "{sys.executable}"\n'
        f'args = ["{base / "probe_server.py"}"]\n'
        f'env = {{ PROBE_LOG = "{base / "probe.log"}" }}\n'
        "connect_timeout_seconds = 5\n",
        encoding="utf-8",
    )
    env = {
        **os.environ,
        "POLARIZER_CHECK_TESTING": "1",
        "POLARIZER_CHECK_TOML": str(base / "polarizer.toml"),
        "POLARIZER_CHECK_LEDGER_DIR": str(base / "ledgers" / "polarizer-m1a-check"),
    }
    yield base, env
    found = subprocess.run(["pgrep", "-f", str(base)], capture_output=True, text=True).stdout
    assert found == "", "the test left a process running"


def serve(base):
    """Claude Code starting (or reconnecting) Polarizer, without Claude Code."""
    done = subprocess.run(
        [str(ROOT / ".venv" / "bin" / "polarizer"), "serve", "--config", str(base / "polarizer.toml")],
        stdin=subprocess.DEVNULL, capture_output=True, timeout=60,
    )  # fmt: skip
    assert done.returncode == 0, done.stderr


def kinds(base):
    path = base / "ledgers" / "polarizer-m1a-check" / "ledger.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def status(env):
    code, out = drive(env, "status")
    assert code == 0, out
    return out.strip().splitlines()[-1]


START = (
    'Terminal A: cd ~/code/polarizer && claude --mcp-config "$PWD/manual/mcp.json"'
    " --strict-mcp-config. Type /mcp. polarizer should be connected with no tools."
    " Then run: scripts/m1a-check.sh approve"
)
RELISTED = "Do the tools appear without reconnecting? (y/n/not sure)"


@POSIX
def test_whole_check(check):
    """The five commands in order, with status before each, and nothing left behind."""
    base, env = check
    results = base / "m1a-check-results.txt"
    results.write_text("from an earlier run\n", encoding="utf-8")
    (base / "polarizer-rugpull").write_text("", encoding="utf-8")
    outputs = []

    code, out = drive(env, "reset")
    outputs.append(out)
    assert code == 0, out
    assert out.rstrip().endswith("\n" + START)
    assert not (base / "polarizer-rugpull").exists()
    toml = (base / "polarizer.toml").read_text(encoding="utf-8")
    assert f'ledger_dir = "{base}/ledgers/polarizer-m1a-check"' in toml
    assert f'PROBE_RUGPULL = "{base}/polarizer-rugpull" }}' in toml
    assert results.read_text(encoding="utf-8").startswith("== reset, ")
    assert status(env) == f"Next: start Claude Code. {START}"

    serve(base)  # Claude Code starts Polarizer: the first run
    assert status(env) == "Next: scripts/m1a-check.sh approve"
    code, out = drive(env, "approve", [("Type yes", b"yes\n"), (RELISTED, b"y\n")])
    outputs.append(out)
    assert code == 0, out
    assert "new definitions: 7 (the check expects 7)" in out
    assert "probe__wait     read-only: not set, destructive: not set" in out
    assert "approved 7 definitions as group " in out
    groups = [e for e in kinds(base) if e["kind"] == "tool.approved"]
    assert len(groups) == 7 and all(e["data"]["group"] for e in groups)
    assert status(env).startswith("Next: in terminal A, reconnect polarizer from /mcp.")

    code, out = drive(env, "rugpull")  # before the reconnect: nothing changed yet
    assert code == 1
    assert (
        "stopped: in terminal A, reconnect polarizer from /mcp, then run: scripts/m1a-check.sh rugpull"
        in out
    )

    serve(base)  # the reconnect: the probe finds its rug-pull file and changes wait
    replies = [("the other 6? (y/n/not sure)", b"y\n"), ("press Enter to skip", b"It said no.\n")]
    code, out = drive(env, "rugpull", replies)
    outputs.append(out)
    assert code == 0, out
    assert "pending: 0 new, 1 changed, 0 unservable" in out
    assert 'new description: "Wait for a number of seconds. Changed after approval."' in out
    assert "claude said: It said no." in out
    assert "tool.drift entries in the ledger: 1" in out
    assert "last call.refused entry: none" in out
    assert status(env) == "Next: scripts/m1a-check.sh approve-changed"

    replies = [("Type yes", b"yes\n"), ("listed again without reconnecting?", b"not sure\n")]
    code, out = drive(env, "approve-changed", replies)
    outputs.append(out)
    assert code == 0, out
    last = kinds(base)[-1]
    assert last["kind"] == "tool.approved" and last["data"]["group"] is None
    assert last["data"]["tool"] == "wait"
    assert status(env).startswith("Next: exit Claude Code in terminal A")

    code, out = drive(env, "finish")
    outputs.append(out)
    assert code == 0, out
    assert "intact: " in out and "verify exit code: 0" in out
    assert "probe or Filesystem server processes still running: none" in out
    assert out.rstrip().endswith(f"Paste this: cat {results}")
    example = (base / "polarizer.example.toml").read_text(encoding="utf-8")
    assert (base / "polarizer.toml").read_text(encoding="utf-8") == example
    assert not (base / "polarizer-rugpull").exists()
    assert status(env) == f"Next: nothing: the check is finished. Paste this: cat {results}"

    text = results.read_text(encoding="utf-8")
    assert "from an earlier run" not in text
    for step in ("reset", "approve", "rugpull", "approve-changed", "finish"):
        assert f"\n-- {step}: complete\n" in text
    assert "(y/n/not sure) answer: y\n" in text and "answer: not sure\n" in text
    for out in outputs:  # every command it prints is complete
        assert not re.search(r"<[^>]*>", out), out


@POSIX
def test_ctrl_c_and_blank_answers_stop_cleanly(check):
    base, env = check
    assert drive(env, "reset")[0] == 0
    serve(base)

    code, out = drive(env, "approve", [("Type yes", b"\x03")])
    assert code == 130
    assert "stopped by Ctrl+C: nothing was approved" in out
    assert not [e for e in kinds(base) if e["kind"] == "tool.approved"]

    code, out = drive(env, "approve", [("Type yes", b"\n")])
    assert code == 1
    assert "stopped: the answer was not yes; nothing was approved" in out
    assert not [e for e in kinds(base) if e["kind"] == "tool.approved"]

    replies = [("Type yes", b"yes\n"), (RELISTED, b"maybe\n"), ('Please type one of: "y" "n" "not sure"', b"\n")]  # fmt: skip
    code, out = drive(env, "approve", replies)
    assert code == 1
    assert (
        "stopped: no answer given; the group is approved and the question was not answered; to answer it, run: scripts/m1a-check.sh approve"
        in out
    )
    assert status(env) == "Next: scripts/m1a-check.sh approve"

    replies = [(RELISTED, b"\x03")]
    code, out = drive(env, "approve", replies)
    assert code == 130
    assert "The group is already approved; asking about /mcp now." in out
    assert "stopped by Ctrl+C: the group is approved and the question was not answered" in out
    results = (base / "m1a-check-results.txt").read_text(encoding="utf-8")
    assert "stopped by Ctrl+C: nothing was approved" in results

    replies = [(RELISTED, b"n\n"), ("Do the tools appear after reconnecting? (y/n/not sure)", b"y\n")]  # fmt: skip
    code, out = drive(env, "approve", replies)
    assert code == 0, out
    assert "expect 6 tools, without probe__wait" in out
    assert len([e for e in kinds(base) if e["kind"] == "tool.approved"]) == 7
    code, out = drive(env, "approve")
    assert code == 0 and "approve is done." in out


@POSIX
def test_reset_refusals(check):
    base, env = check
    real = {k: v for k, v in env.items() if k != "POLARIZER_CHECK_TESTING"}
    code, out = drive(real, "reset")
    assert code == 2
    assert (
        "reset runs only on ~/.local/share/polarizer-m1a-check unless POLARIZER_CHECK_TESTING=1 is set"
        in out
    )
    assert not (base / "polarizer.toml").exists()
    assert not (base / "m1a-check-results.txt").exists()

    elsewhere = {**env, "POLARIZER_CHECK_LEDGER_DIR": str(base / "ledger")}
    code, out = drive(elsewhere, "reset")
    assert code == 2 and "must be named polarizer-m1a-check" in out

    probe = subprocess.Popen(
        [sys.executable, str(base / "probe_server.py")],
        stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )  # fmt: skip
    try:
        for command in ("reset", "finish"):
            code, out = drive(env, command)
            assert code == 1
            # The pid and the script path only: on macOS, ps shows the framework Python.app
            # binary that a venv's python execs, not sys.executable.
            listed = rf"^\s*{probe.pid} .+ {re.escape(str(base))}/probe_server\.py$"
            assert re.search(listed, out, re.MULTILINE), out
            assert (
                f"stop them with: kill {probe.pid}. Then run: scripts/m1a-check.sh {command}" in out
            )
        assert not (base / "polarizer.toml").exists()
    finally:
        probe.stdin.close()
        probe.wait(timeout=30)
