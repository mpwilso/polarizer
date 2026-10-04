"""scripts/guard.sh, run on a copy of itself in a sandbox: a fake repo root with its own
.guard-paths naming one throwaway git repo, and HOME pointing into the test's directory, so no
real repo, tool directory or ~/.claude.json is read. POSIX only: the script needs bash, GNU find
and stat."""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
pytestmark = pytest.mark.skipif(
    sys.platform == "win32" or os.geteuid() == 0,
    reason="needs bash and GNU find; as root an unreadable directory is still readable",
)


@pytest.fixture
def guard(tmp_path):
    """run(*args) runs the sandboxed guard.sh and returns (exit code, stdout, stderr)."""
    root, home, repo = tmp_path / "root", tmp_path / "home", tmp_path / "repo"
    (root / "scripts").mkdir(parents=True)
    shutil.copy(ROOT / "scripts" / "guard.sh", root / "scripts" / "guard.sh")
    (home / "isr-notes").mkdir(parents=True)
    git = ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", "-C", str(repo)]
    repo.mkdir()
    subprocess.run([*git, "init", "-q"], check=True)
    subprocess.run([*git, "commit", "-q", "--allow-empty", "-m", "x"], check=True)
    (root / ".guard-paths").write_text(f"# the test repo\n{repo}\n", encoding="utf-8")
    env = {**os.environ, "HOME": str(home)}

    def run(*args):
        done = subprocess.run(
            ["bash", str(root / "scripts" / "guard.sh"), *args],
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
        )
        return done.returncode, done.stdout, done.stderr

    run.root, run.home, run.repo = root, home, repo
    return run


def files(run, pattern):
    return sorted((run.root / ".guard").glob(pattern))


def test_snapshot_then_check_unchanged(guard):
    code, out, err = guard("snapshot")
    assert code == 0, out + err
    [snap] = files(guard, "snapshot-*.txt")
    assert out.splitlines()[-1] == f"snapshot {snap}"
    code, out, err = guard("check")
    assert code == 0, out + err


def test_check_reports_a_change_with_exit_1(guard):
    assert guard("snapshot")[0] == 0
    (guard.repo / "new.txt").write_text("x\n", encoding="utf-8")
    code, out, _ = guard("check")
    assert code == 1
    assert "?? new.txt" in out


def test_root_mcp_json_warns_and_exits_1(guard):
    (guard.root / ".mcp.json").write_text("{}\n", encoding="utf-8")
    code, out, _ = guard("snapshot")
    assert code == 1
    assert "warning:" in out and ".mcp.json exists" in out
    assert len(files(guard, "snapshot-*.txt")) == 1  # still recorded


def test_unreadable_entry_stops_snapshot_with_exit_2(guard):
    """An entry find can't read used to end snapshot with exit 1, no output, and a truncated
    snapshot-*.txt that check would then use as its baseline."""
    locked = guard.home / "isr-notes" / "locked"
    locked.mkdir()
    locked.chmod(0)
    try:
        code, out, err = guard("snapshot")
    finally:
        locked.chmod(0o700)
    assert code == 2
    assert "guard: stopped while reading the state" in err
    assert "no snapshot recorded" in err
    assert files(guard, "snapshot-*.txt") == []
    assert len(files(guard, "partial-*.txt")) == 1
    assert not out.startswith("snapshot ")


def test_unreadable_entry_stops_check_with_exit_2(guard):
    """check stops too, rather than comparing part of the state: exit 2, not 0 or 1."""
    assert guard("snapshot")[0] == 0
    locked = guard.home / "isr-notes" / "locked"
    locked.mkdir()
    locked.chmod(0)
    try:
        code, out, err = guard("check")
    finally:
        locked.chmod(0o700)
    assert code == 2
    assert "guard: stopped while reading the state" in err and "nothing was compared" in err
    assert "no changes" not in out
