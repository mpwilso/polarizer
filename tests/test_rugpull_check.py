"""scripts/rugpull-check.sh and scripts/rugpull_check.py, with no Claude Code and no model, and
the probe's PROBE_PHASE, which the script relies on.

The script tests run it end to end with a stub claude (tests/helpers/fake_claude.py, named by
CLAUDE_BIN under POLARIZER_CHECK_TESTING=1). The stub starts the Polarizer its --mcp-config
names and calls probe__wait only if it is listed, as Claude Code can only call a listed tool;
STUB_MODE makes it go wrong in the ways the checks must catch. Everything is written under the
test's own directory: TMPDIR, HOME and the results file all point there. POSIX only: the script
needs bash and pgrep."""

import json
import os
import re
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest
from helpers.raw import RawClient

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "rugpull-check.sh"
PROBE = ROOT / "tests" / "helpers" / "probe_server.py"
STUB = ROOT / "tests" / "helpers" / "fake_claude.py"
sys.path.insert(0, str(ROOT / "scripts"))
import rugpull_check  # noqa: E402

ORIGINAL = "Wait for a number of seconds."
CHANGED = "Wait for a number of seconds. Changed after approval."
POSIX = pytest.mark.skipif(
    sys.platform == "win32", reason="the script needs bash and pgrep, which Windows lacks"
)


# --- the probe's PROBE_PHASE


def probe_env(**extra) -> dict:
    env = {k: v for k, v in os.environ.items() if k not in ("PROBE_PHASE", "PROBE_RUGPULL")}
    return {**env, **extra}


def wait_description(env: dict) -> str:
    with RawClient([sys.executable, str(PROBE)], env=env) as client:
        client.initialize()
        tools = client.request("tools/list", {})["result"]["tools"]
    return next(t["description"] for t in tools if t["name"] == "wait")


def test_probe_phase_serves_original_or_changed():
    for _ in range(2):  # the same each start: nothing depends on how often it started
        assert wait_description(probe_env(PROBE_PHASE="original")) == ORIGINAL
        assert wait_description(probe_env(PROBE_PHASE="changed")) == CHANGED


def test_probe_phase_takes_precedence_over_rugpull(tmp_path):
    flag = tmp_path / "rugpull"
    env = probe_env(PROBE_PHASE="changed", PROBE_RUGPULL=str(flag))
    assert wait_description(env) == CHANGED
    assert not flag.exists()  # PROBE_RUGPULL ignored: no file created
    flag.touch()
    env = probe_env(PROBE_PHASE="original", PROBE_RUGPULL=str(flag))
    assert wait_description(env) == ORIGINAL


def test_probe_rugpull_alone_is_unchanged(tmp_path):
    flag = tmp_path / "rugpull"
    env = probe_env(PROBE_RUGPULL=str(flag))
    assert wait_description(env) == ORIGINAL
    assert flag.exists()
    assert wait_description(env) == CHANGED
    assert wait_description(probe_env()) == ORIGINAL


def test_probe_phase_refuses_other_values():
    done = subprocess.run(
        [sys.executable, str(PROBE)],
        env=probe_env(PROBE_PHASE="later"),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert done.returncode == 2
    assert "PROBE_PHASE must be one of original, changed" in done.stderr
    assert done.stdout == ""


# --- scripts/rugpull_check.py


def test_setup_configs_differ_only_in_phase(tmp_path):
    rugpull_check.setup(tmp_path, ROOT)
    toml = tomllib.loads((tmp_path / "polarizer.toml").read_text(encoding="utf-8"))
    assert toml["ledger_dir"] == (tmp_path / "ledger").as_posix()
    assert list(toml["upstream"]) == ["probe"]
    env = toml["upstream"]["probe"]["env"]
    assert env == {"PROBE_LOG": str(tmp_path / "probe.log"), "PROBE_PHASE": "${PROBE_PHASE}"}
    configs = {
        phase: json.loads((tmp_path / f"mcp-{phase}.json").read_text(encoding="utf-8"))
        for phase in ("original", "changed")
    }
    for phase, config in configs.items():
        assert config["mcpServers"]["pz"]["env"] == {"PROBE_PHASE": phase}
        config["mcpServers"]["pz"]["env"] = {}
    assert configs["original"] == configs["changed"]
    args = configs["original"]["mcpServers"]["pz"]["args"]
    assert args[-2:] == ["--config", str(tmp_path / "polarizer.toml")]


def test_b_without_polarizer_proves_nothing():
    """A run B in which Polarizer never started has no call, but that passes nothing."""
    marks = {f"{r}.{e}": {"ledger": 0, "probe": 0} for r in "ABC" for e in ("start", "end")}
    results = rugpull_check.evaluate([], [], marks, {}, (True, "intact"))
    by_name = {name: (ok, detail) for name, ok, detail in results}
    ok, detail = by_name["B: the probe received no tools/call wait"]
    assert (
        not ok
        and detail
        == "Polarizer did not start during run B, so the absence of a call proves nothing"
    )
    assert [ok for ok, _ in by_name.values()] == [False] * 8 + [True]


# --- scripts/rugpull-check.sh with the stub claude


@pytest.fixture
def check(tmp_path):
    """run(mode) runs the script with the stub, and returns (exit code, output, the test's dirs)."""
    stub = tmp_path / "claude"
    stub.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{STUB}" "$@"\n', encoding="utf-8")
    stub.chmod(0o755)

    def run(mode="", testing=True):
        base = tmp_path / (mode or "normal")
        dirs = {name: base / name for name in ("tmp", "home", "stub")}
        for d in dirs.values():
            d.mkdir(parents=True)
        dirs["results"] = base / "results.txt"
        env = {k: v for k, v in os.environ.items() if not k.startswith(("STUB_", "PROBE_"))}
        env.update(
            HOME=str(dirs["home"]),
            TMPDIR=str(dirs["tmp"]),
            STUB_DIR=str(dirs["stub"]),
            STUB_MODE=mode,
            MCP_TOOL_TIMEOUT="5000",  # the script must unset it
            CLAUDE_BIN=str(stub),
            POLARIZER_CHECK_RESULTS=str(dirs["results"]),
        )
        if testing:
            env["POLARIZER_CHECK_TESTING"] = "1"
        done = subprocess.run(
            ["bash", str(SCRIPT)],
            env=env,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=300,
        )
        return done.returncode, done.stdout + done.stderr, dirs

    return run


def lines(out: str, prefix: str) -> list[str]:
    return [line for line in out.splitlines() if line.startswith(prefix)]


def failed(out: str) -> list[str]:
    """The names of the checks that failed, in order."""
    names = [name for name, _, _ in rugpull_check.evaluate([], [], {}, {}, (True, ""))]
    return [name for name in names if f"\nFAIL  {name}: " in f"\n{out}"]


def kept(out: str, dirs: dict) -> Path:
    """The temp directory the script says it kept, which must still exist under TMPDIR."""
    found = re.search(r"^the temp directory is kept: (.+)$", out, re.M)
    assert found, out
    path = Path(found.group(1))
    assert path.parent == dirs["tmp"].resolve() and path.is_dir()
    return path


def runs(dirs: dict) -> list[dict]:
    count = int((dirs["stub"] / "count").read_text(encoding="utf-8"))
    return [
        json.loads((dirs["stub"] / f"run-{n}.json").read_text(encoding="utf-8"))
        for n in range(1, count + 1)
    ]


@POSIX
def test_all_checks_pass(check):
    code, out, dirs = check()
    assert code == 0, out
    assert len(lines(out, "PASS  ")) == 9 and not lines(out, "FAIL  "), out
    assert "rugpull check passed" in out
    assert dirs["results"].read_text(encoding="utf-8") == out  # everything is in the file
    assert out.rstrip().endswith(f"Paste the output of: cat {dirs['results']}")
    assert list(dirs["tmp"].iterdir()) == []  # the temp directory was removed
    workdir = re.search(r"^working directory: (.+)$", out, re.M).group(1)
    assert f"removed the temp directory {workdir}" in out

    # What claude was given, as the stub recorded it.
    recorded = runs(dirs)
    assert len(recorded) == 3
    for run, phase in zip(recorded, ("original", "changed", "changed"), strict=True):
        argv = run["argv"]
        assert argv[argv.index("--mcp-config") + 1] == f"{workdir}/mcp-{phase}.json"
        for flag in ("--strict-mcp-config", "--no-session-persistence"):
            assert flag in argv
        assert argv[argv.index("--model") + 1] == "haiku"
        assert argv[argv.index("--permission-mode") + 1] == "default"
        assert "--allowedTools=mcp__pz__probe__wait" in argv
        assert "seconds set to 1" in argv[argv.index("-p") + 1]
        assert run["MCP_TOOL_TIMEOUT"] is None
        assert run["stdin_is_devnull"]
        assert run["cwd"] == workdir

    # The printout.
    assert "claude version: 0.0.0 (fake claude for tests)" in out
    assert "it unsets MCP_TOOL_TIMEOUT (was set)" in out
    assert "MCP_TOOL_TIMEOUT=<unset>" in out
    assert len(lines(out, "run A arguments: -p ")) == 1
    assert "total: 0.0036 USD" in out
    assert "~/.claude.json bookkeeping: before absent; after absent" in out
    assert out.count("serve processes still running for this check: none") == 3
    assert "run B: model reply (information only, not used for pass or fail): The tool" in out


@POSIX
def test_b_calling_the_tool_fails(check):
    code, out, dirs = check("b-calls")
    assert code == 1
    assert failed(out) == [
        "B: the probe received no tools/call wait",
        "B: the ledger has no call.sent for probe__wait in B's session",
    ]
    assert "rugpull check FAILED: 2 of 9 checks failed" in out
    assert (kept(out, dirs) / "ledger" / "ledger.jsonl").exists()


@POSIX
def test_no_drift_fails(check):
    code, out, dirs = check("no-drift")
    assert code == 1
    assert (
        "FAIL  B: the ledger has a tool.drift for probe wait whose approved_hash and live_hash"
        " differ: none during run B; tool.drift entries in the whole ledger: 0"
    ) in out
    assert "FAIL  approval: the ledger has tool.approved" in out
    assert "rugpull check FAILED" in out
    kept(out, dirs)


@POSIX
def test_tampered_ledger_fails(check):
    code, out, dirs = check("tamper")
    assert code == 1
    assert failed(out) == ["the ledger verifies as intact"]
    assert "polarizer verify exit code 1: tampered: line 2 (seq 1)" in out
    assert "rugpull check FAILED: 1 of 9 checks failed" in out
    kept(out, dirs)


@POSIX
def test_claude_exiting_nonzero_fails(check):
    code, out, dirs = check("fail-b")
    assert code == 1
    assert "run B: claude exit code 1" in out
    b = lines(out, "FAIL  B: ")
    assert len(b) == 3
    for line in b:
        assert line.endswith(
            "run B: claude exited 1; first lines: stderr: Error: Invalid MCP configuration:"
            " | stderr: mcpServers.pz: the stub rejected this config"
        )
    assert "rugpull check FAILED" in out
    kept(out, dirs)


@POSIX
def test_testing_needs_both_overrides(tmp_path):
    """With POLARIZER_CHECK_TESTING=1, CLAUDE_BIN and POLARIZER_CHECK_RESULTS are both required,
    checked before anything is written."""
    env = {
        k: v for k, v in os.environ.items() if k not in ("CLAUDE_BIN", "POLARIZER_CHECK_RESULTS")
    }
    env.update(POLARIZER_CHECK_TESTING="1", TMPDIR=str(tmp_path), HOME=str(tmp_path))
    done = subprocess.run(
        ["bash", str(SCRIPT)], env=env, stdin=subprocess.DEVNULL, capture_output=True,
        text=True, timeout=60,
    )  # fmt: skip
    assert done.returncode == 2
    assert "needs CLAUDE_BIN and POLARIZER_CHECK_RESULTS" in done.stderr
    assert list(tmp_path.iterdir()) == []
