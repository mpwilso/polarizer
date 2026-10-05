"""scripts/hold-check.sh runs the M2a manual check, and scripts/hold_check.py reads the ledger
for it.

The script tests run it on a pseudo-terminal with scripted answers, POSIX only, with no Claude
Code and no model: a raw MCP client running `polarizer serve` stands in for Claude Code, and
makes each call the script asks the person to request; tests/helpers/fs_stub.py stands in for
the Filesystem server. Everything the script writes is in the test's own directory
(POLARIZER_CHECK_TESTING=1), where the long wait and the hold timeout are 1 s."""

import json
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
from helpers.ptyread import finish, read_some
from helpers.raw import RawClient

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "hold-check.sh"
POLARIZER = ROOT / ".venv" / "bin" / "polarizer"
sys.path.insert(0, str(ROOT / "scripts"))
import hold_check  # noqa: E402

POSIX = pytest.mark.skipif(
    sys.platform == "win32", reason="the script needs bash, pgrep and a pty, which Windows lacks"
)
START = (
    'Terminal A: cd ~/code/polarizer && claude --mcp-config "$PWD/manual/mcp.json"'
    " --strict-mcp-config. Type /mcp: polarizer should be connected, with its tools listed."
    " Then run: scripts/hold-check.sh deny"
)
NOT_ALLOWED = "polarizer: fs__write_file was not allowed"
IGNORED = "more bytes typed or pasted after the answer"


def _take_terminal():
    import fcntl
    import termios

    fcntl.ioctl(0, termios.TIOCSCTTY, 0)


def drive(env, command, steps=(), timeout=60):
    """Run `scripts/hold-check.sh <command>` on a pseudo-terminal of its own. steps: (text,
    action) pairs, taken in order once each text has appeared: bytes are typed, a callable is
    called (the stand-in for Claude Code doing something). Returns the exit code and the
    output, with the terminal's echo of what was typed."""
    import pty

    controller, terminal = pty.openpty()
    proc = subprocess.Popen(
        ["bash", str(SCRIPT), command],
        stdin=terminal, stdout=terminal, stderr=terminal, cwd=ROOT, env=env,
        start_new_session=True, preexec_fn=_take_terminal,
    )  # fmt: skip
    os.close(terminal)
    output, cursor, todo = b"", 0, list(steps)
    deadline = time.monotonic() + timeout
    try:
        while time.monotonic() < deadline:
            if todo:
                at = output.find(todo[0][0].encode(), cursor)
                if at >= 0:
                    cursor = at + len(todo[0][0])
                    action = todo.pop(0)[1]
                    if callable(action):
                        action()
                    else:
                        os.write(controller, action)
            chunk = read_some(controller, 0.05)
            if chunk is None:  # the end: everything holding the terminal has closed it
                break
            output += chunk
        else:
            proc.kill()
            pytest.fail(f"hold-check.sh {command} still running after {timeout} s:\n{output!r}")
        code, rest = finish(controller, proc)
        output += rest
    finally:
        os.close(controller)
    assert not todo, f"never saw {todo[0][0]!r}:\n{output.decode()}"
    return code, output.decode("utf-8", "replace").replace("\r\n", "\n")


class Claude:
    """The stand-in for Claude Code and its model: starts `polarizer serve` as Claude Code
    does, and makes a tool call when the person would ask Claude for one."""

    def __init__(self, toml: Path):
        self.toml = toml
        self.results: list[dict] = []
        self.start()

    def start(self):
        self.client = RawClient([str(POLARIZER), "serve", "--config", str(self.toml)])
        self.client.initialize("2025-11-25")

    def call(self, name: str, arguments: dict) -> None:
        def run():
            try:
                answer = self.client.request("tools/call", {"name": name, "arguments": arguments})
            except (EOFError, ValueError):  # serve shut down while the call was held
                answer = {"closed": True}
            self.results.append(answer)

        threading.Thread(target=run, daemon=True).start()

    def last_text(self, n: int, seconds: float = 30) -> str:
        """The text of the n-th answer (1-based), once it has arrived."""
        deadline = time.monotonic() + seconds
        while len(self.results) < n:
            assert time.monotonic() < deadline, "no answer from serve"
            time.sleep(0.05)
        return self.results[n - 1]["result"]["content"][0]["text"]

    def reconnect(self) -> None:
        """Reconnecting polarizer from /mcp: the old serve sees end of input, a new one starts
        and reads polarizer.toml again."""
        assert self.client.close() == 0
        self.start()

    def exit(self) -> None:
        assert self.client.close() == 0


@pytest.fixture
def check(tmp_path, fake_home):
    """A check directory: the Filesystem stand-in as the only upstream, the example toml next
    to it, and the environment that points the script there."""
    base = tmp_path / "check"
    base.mkdir()
    shutil.copy(ROOT / "tests" / "helpers" / "fs_stub.py", base / "fs_stub.py")
    manual = base / "manual"
    (base / "polarizer.example.toml").write_text(
        'ledger_dir = "~/.local/share/polarizer-manual"\n'
        "ledger_forbidden_paths = []\n\n"
        "[policy]\n"
        f'workspace_roots = ["{manual}"]\n'
        "hold_timeout_seconds = 300\n\n"
        "[upstream.fs]\n"
        f'command = "{sys.executable}"\n'
        f'args = ["{base / "fs_stub.py"}", "{manual}"]\n'
        f'env = {{ FS_STUB_LOG = "{base / "fs.log"}" }}\n'
        "connect_timeout_seconds = 5\n\n"
        "[upstream.fs.tools]\n"
        'write_file = { class = "local-write", path_args = ["path"] }\n'
        'move_file = { class = "destructive", path_args = ["source", "destination"] }\n',
        encoding="utf-8",
    )
    env = {
        **os.environ,
        "POLARIZER_CHECK_TESTING": "1",
        "POLARIZER_CHECK_TOML": str(base / "polarizer.toml"),
        "POLARIZER_CHECK_LEDGER_DIR": str(base / "ledgers" / "polarizer-m2a-check"),
    }
    yield base, env
    found = subprocess.run(["pgrep", "-f", str(base)], capture_output=True, text=True).stdout
    assert found == "", "the test left a process running"


def ledger(base) -> list[dict]:
    path = base / "ledgers" / "polarizer-m2a-check" / "ledger.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def status(env) -> str:
    code, out = drive(env, "status")
    assert code == 0, out
    assert out.strip(), f"hold-check.sh status exited {code} and printed nothing"
    return out.strip().splitlines()[-1]


@POSIX
def test_whole_check(check):
    """Every command in order, with a stand-in for Claude Code, status between them, and
    nothing left running."""
    base, env = check
    hooks = base / "manual" / ".git" / "hooks"
    results = base / "hold-check-results.txt"
    assert status(env) == "Next: scripts/hold-check.sh reset"

    code, out = drive(env, "reset", [("Type yes to approve them as group", b"yes\n")])
    assert code == 0, out
    assert out.rstrip().endswith("-- reset: complete\n\n" + START)
    assert "approved 2 definitions as group " in out
    toml = (base / "polarizer.toml").read_text(encoding="utf-8")
    assert f'ledger_dir = "{base}/ledgers/polarizer-m2a-check"' in toml
    assert "hold_timeout_seconds = 300" in toml
    assert (base / "manual" / "note.txt").read_text() == "hello from the manual check\n"
    token = ledger(base)[0]["data"]["chain_id"][:8]
    assert status(env) == "Next: scripts/hold-check.sh deny"

    claude = Claude(base / "polarizer.toml")
    try:
        target = str(hooks / f"hold-check-{token}.txt")
        write = {"path": target, "content": f"M2a hold check {token}"}
        code, out = drive(env, "deny", [
            ("In terminal A, ask Claude:", lambda: claude.call("fs__write_file", write)),
            ("Is hold", b"y\n"),
            # A pasted block: only its first line is the answer, and the rest is thrown away
            # rather than left for the shell to run once the script ends.
            ("What did Claude Code show", b"It said the call was not allowed.\nnot-a-command\n"),
        ])  # fmt: skip
        assert code == 0, out
        assert "do not paste multi-line text" in out
        assert IGNORED in out
        assert f'write the text "M2a hold check {token}" to {target}' in out
        assert "\x07" in out and "held by write-pattern: " in out
        assert "denied hold " in out and "-- deny: complete" in out
        assert "does not exist, as expected after a deny" in out
        assert claude.last_text(1) == NOT_ALLOWED
        assert status(env) == "Next: scripts/hold-check.sh allow"

        code, out = drive(env, "allow", [
            ("In terminal A, ask Claude:", lambda: claude.call("fs__write_file", write)),
            ("Is hold", b"y\n"),
            ("What did Claude Code show", b"It wrote the file.\n"),
        ])  # fmt: skip
        assert code == 0, out
        assert "the forwarded call returned: outcome ok" in out
        assert f"{target} holds: M2a hold check {token}" in out
        assert claude.last_text(2) == f"Successfully wrote to {target}"
        assert status(env) == "Next: scripts/hold-check.sh long-wait"

        long = {"path": str(hooks / f"hold-check-long-{token}.txt"), "content": "long"}
        code, out = drive(env, "long-wait", [
            ("In terminal A, ask Claude:", lambda: claude.call("fs__write_file", long)),
            ("Is hold", b"y\n"),
            # Without the discard, "not tried" would answer the next question.
            ("While it waited", b"a spinner\nnot tried\n"),
            ("Could you type", b"y\n"),
            ("Type yes to allow", b"yes\n"),
            ("once the call finished", b"it said done after a minute\n"),
        ])  # fmt: skip
        assert code == 0, out
        assert "held for 1 s, still open" in out and "outcome ok" in out
        assert "-- long-wait: complete" in out
        assert status(env) == "Next: scripts/hold-check.sh expire"

        source, destination = base / "manual" / "note.txt", base / "manual" / f"note-{token}.txt"
        move = {"source": str(source), "destination": str(destination)}
        code, out = drive(env, "expire", [
            ("choose polarizer and reconnect it", claude.reconnect),
            ("In terminal A, ask Claude:", lambda: claude.call("fs__move_file", move)),
            ("Is hold", b"y\n"),
            ("What did Claude Code show", b"not allowed\n"),
            ("About how long", b"1\n"),
        ])  # fmt: skip
        assert code == 0, out
        assert "polarizer started again with hold_timeout_seconds = 1" in out
        assert "the hold ended: hold.expired at seq " in out
        assert "is still there, as expected" in out and "does not exist, as expected" in out
        assert claude.last_text(4) == "polarizer: fs__move_file was not allowed"
        assert status(env) == (
            "Next: exit Claude Code in terminal A, then run: scripts/hold-check.sh finish"
        )
    finally:
        claude.exit()

    code, out = drive(env, "finish")
    assert code == 0, out
    assert "intact: " in out and "verify exit code: 0" in out
    assert "holds: nothing is held" in out
    assert "probe or Filesystem server processes still running: none" in out
    assert "hold_timeout_seconds = 300" in (base / "polarizer.toml").read_text(encoding="utf-8")
    assert status(env) == f"Next: nothing: the check is finished. Paste this: cat {results}"

    kinds = [e["kind"] for e in ledger(base)]
    assert kinds.count("hold.created") == 4 and kinds.count("hold.decided") == 3
    assert kinds.count("hold.expired") == 1 and kinds.count("call.returned") == 2
    text = results.read_text(encoding="utf-8")
    for marker in ("reset", "deny", "allow", "long-wait", "expire", "finish"):
        assert f"-- {marker}: complete" in text
    assert "answer: It said the call was not allowed.\n" in text
    assert "not-a-command" not in text
    assert "answer: a spinner\n" in text and "(y/n/not tried) answer: y\n" in text


@POSIX
def test_wrong_hold_and_overrides(check):
    """A "n" to "is this the call" stops without deciding; the overrides need
    POLARIZER_CHECK_TESTING=1; a second reset moves the first ledger aside, deleting nothing."""
    base, env = check
    assert drive(env, "reset", [("Type yes", b"yes\n")])[0] == 0
    claude = Claude(base / "polarizer.toml")
    try:
        token = ledger(base)[0]["data"]["chain_id"][:8]
        path = str(base / "manual" / ".git" / "hooks" / f"other-{token}.txt")
        code, out = drive(env, "deny", [
            ("In terminal A, ask Claude:", lambda: claude.call("fs__write_file", {"path": path, "content": "x"})),
            ("Is hold", b"n\n"),
        ])  # fmt: skip
        assert code == 1
        assert "stopped: it was not the expected call; it times out on its own" in out
        assert [e["kind"] for e in ledger(base)].count("hold.decided") == 0
    finally:
        claude.exit()
    for unset in ("POLARIZER_CHECK_TESTING",):
        bare = {k: v for k, v in env.items() if k != unset}
        code, out = drive(bare, "status")
        assert code == 2 and "are for testing" in out
    assert drive(env, "reset", [("Type yes", b"yes\n")])[0] == 0
    aside = [p.name for p in (base / "ledgers").iterdir()]
    assert "polarizer-m2a-check" in aside and any(
        n.startswith("polarizer-m2a-check.before-") for n in aside
    )


# --- scripts/hold_check.py, on every platform


def test_group_reads_a_fresh_first_run(capsys):
    golden = Path(__file__).resolve().parent / "golden"
    several = (golden / "pending_several_waiting.txt").read_text(encoding="utf-8")
    assert hold_check.group(several) == 1  # not everything is in the group
    nothing = (golden / "pending_nothing.txt").read_text(encoding="utf-8")
    assert hold_check.group(nothing) == 3
    classes = (golden / "pending_classes.txt").read_text(encoding="utf-8")
    assert hold_check.group(classes) == 0  # pending --config's class lines and section parse
    assert capsys.readouterr().out.strip() == (
        "02e5789c4040f97a564af9e1a5b16b96d6a70e5db4ad6b8da88fcb5e48050a65"
    )
