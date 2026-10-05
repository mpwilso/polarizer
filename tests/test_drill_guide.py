"""docs/DRILL-GUIDE.md, checked against the code, as tests/test_readme.py checks the README
(docs/MEASURE-SPEC.md, section 13).

Every command in the guide's sh blocks parses with Polarizer's own parser, and the polarizer
commands are run in the guide's order on a temporary home: the drill on a pseudo-terminal,
answered by the test (POSIX; Windows has no pty module), then the report and the export in a
working directory of their own. The uv lines are not run: they need the network and install a
tool for the user. The screen excerpts are compared with what a drill prints, and the closing
lines with what Polarizer prints."""

import os
import re
import shlex
import subprocess
import sys
import time

import pytest
from conftest import ROOT
from helpers.ptyread import finish, read_some

import polarizer
from polarizer import cli, drill

GUIDE = (ROOT / "docs" / "DRILL-GUIDE.md").read_text(encoding="utf-8")
GOLDEN = ROOT / "tests" / "golden"
NOT_RUN = {"uv ": "needs the network and installs a tool for the user"}


def blocks(language: str) -> list[str]:
    return re.findall(rf"```{language}\n(.*?)```", GUIDE, re.S)


def commands() -> list[str]:
    return [line.strip() for block in blocks("sh") for line in block.splitlines() if line.strip()]


def test_guide_commands_exist():
    """Every polarizer command line parses with Polarizer's own parser; every other line is a
    uv line, not run here."""
    found = commands()
    assert [c for c in found if c.startswith("polarizer ")] == [
        "polarizer drill",
        "polarizer drill report",
        "polarizer drill report --export drill-summary.json",
    ]
    for command in found:
        if command.startswith("polarizer "):
            cli._parser().parse_args(shlex.split(command)[1:])
        else:
            assert any(command.startswith(p) for p in NOT_RUN), command
    wheel = f"polarizer-{polarizer.__version__}-py3-none-any.whl"
    assert f"uv tool install ./{wheel}" in found


def test_guide_quotes_the_closing_lines():
    quoted = [line[2:] for line in GUIDE.splitlines() if line.startswith("> ")]
    assert quoted == drill.CLOSING


def test_guide_excerpts_are_what_a_drill_prints():
    """The call and reveal excerpts are lines of the golden screens, and the export's example
    is in the golden export."""
    call, reveal = blocks("text")
    printed = (GOLDEN / "drill_call_plain.txt").read_text(encoding="utf-8").splitlines()
    for line in call.splitlines():
        if line not in ("...", "allow or deny?"):
            assert line in printed, line
    assert "allow or deny? " in printed
    revealed = (GOLDEN / "drill_reveal_right.txt").read_text(encoding="utf-8")
    assert revealed.startswith("\nClean call. You allowed it.\nwhy: ")
    from helpers.drillkit import TEST_SET

    readme = [s for s in TEST_SET.scenarios.values() if "Running the tests" in s["task"]][0]
    assert reveal == f"Clean call. You allowed it.\nwhy: {readme['why']}\n"
    assert '"answered":116' in (GOLDEN / "drill_export.json").read_text(encoding="utf-8")


def test_guide_paths_are_the_drill_directory(fake_home):
    assert "`~/.local/share/polarizer-drills`" in GUIDE
    assert drill.default_dir() == fake_home / ".local" / "share" / "polarizer-drills"


@pytest.mark.skipif(sys.platform == "win32", reason="Windows has no pty module")
def test_guide_commands_run(tmp_path):
    """The guide's polarizer commands, in order, as a person runs them, with a temporary home.
    Each wait is for text to appear, up to 60 s, against well under a second needed here."""
    import pty

    home = tmp_path / "home"
    home.mkdir()
    work = tmp_path / "work"
    work.mkdir()
    env = {**os.environ, "HOME": str(home), "USERPROFILE": str(home)}
    polarizer_cmd = [sys.executable, "-m", "polarizer"]
    run_order = [c for c in commands() if c.startswith("polarizer ")]

    # polarizer drill, on a pseudo-terminal: Enter, then each call denied, then Enter.
    controller, terminal = pty.openpty()
    argv = polarizer_cmd + shlex.split(run_order[0])[1:]
    proc = subprocess.Popen(argv, stdin=terminal, stdout=terminal, stderr=terminal, env=env,
                            cwd=work)  # fmt: skip
    os.close(terminal)
    replies = {
        b"Press Enter to start.": b"\n",
        b"what do you expect this call to do? ": b"it does what the task says\n",
        b"allow or deny? ": b"d\n",
        b"Press Enter for the next call.": b"\n",
        b"Press Enter to see the results.": b"\n",
    }
    output, seen = b"", b""
    deadline = time.monotonic() + 300
    try:
        # Answer each prompt as it appears, whichever condition the drill drew; each prompt
        # appears within a second here, against 300 s for the whole drill.
        while time.monotonic() < deadline:
            chunk = read_some(controller, 0.05)
            if chunk is None:
                break
            output += chunk
            seen += chunk
            for prompt, reply in replies.items():
                if seen.endswith(prompt):
                    os.write(controller, reply)
                    seen = b""
                    break
        else:
            pytest.fail(f"the drill did not end:\n{output.decode()}")
        code, rest = finish(controller, proc)
        output += rest
    finally:
        if proc.poll() is None:
            proc.kill()
        os.close(controller)
    assert code == 0, output.decode()
    assert b"Drill finished: 20 of 20 calls answered" in output
    assert (home / ".local" / "share" / "polarizer-drills" / "ledger.jsonl").exists()

    report = subprocess.run(polarizer_cmd + shlex.split(run_order[1])[1:], env=env, cwd=work,
                            capture_output=True, text=True, timeout=120)  # fmt: skip
    assert report.returncode == 0 and report.stdout.startswith("drills: 1 finished"), report
    export = subprocess.run(polarizer_cmd + shlex.split(run_order[2])[1:], env=env, cwd=work,
                            capture_output=True, text=True, timeout=120)  # fmt: skip
    assert export.returncode == 0, export.stderr
    assert export.stdout == report.stdout
    assert (work / "drill-summary.json").read_text(encoding="utf-8").startswith('{"answered":20,')
