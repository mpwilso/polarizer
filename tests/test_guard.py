"""scripts/dev/guard.sh, run on a copy of itself in a sandbox: a fake repo root with its own
.guard-paths naming one throwaway git repo, and HOME pointing into the test's directory, so no
real repo, tool directory or ~/.claude.json is read. POSIX only: the script needs bash and
python3. It must run on macOS's bash 3.2 and BSD tools, which these tests can't show on Linux;
test_scripts_portable.py keeps out the bash 4 features and GNU-only options that would break it."""

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
pytestmark = pytest.mark.skipif(
    sys.platform == "win32" or os.geteuid() == 0,
    reason="needs bash; as root an unreadable directory is still readable",
)


@pytest.fixture
def guard(tmp_path):
    """run(*args) runs the sandboxed guard.sh and returns (exit code, stdout, stderr)."""
    root, home, repo = tmp_path / "root", tmp_path / "home", tmp_path / "repo"
    (root / "scripts" / "dev").mkdir(parents=True)
    shutil.copy(ROOT / "scripts" / "dev" / "guard.sh", root / "scripts" / "dev" / "guard.sh")
    (home / "isr-notes").mkdir(parents=True)
    git = ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", "-C", str(repo)]
    repo.mkdir()
    subprocess.run([*git, "init", "-q"], check=True)
    subprocess.run([*git, "commit", "-q", "--allow-empty", "-m", "x"], check=True)
    (root / ".guard-paths").write_text(f"# the test repo\n{repo}\n", encoding="utf-8")
    env = {**os.environ, "HOME": str(home)}

    def run(*args):
        done = subprocess.run(
            ["bash", str(root / "scripts" / "dev" / "guard.sh"), *args],
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
    (guard.repo / "old.txt").write_text("x\n", encoding="utf-8")
    code, out, _ = guard("snapshot")
    assert code == 0
    [snap] = files(guard, "snapshot-*.txt")
    # Tab-separated, as written through sed with a literal tab (BSD sed has no backslash-t).
    assert f"repo:{guard.repo}\tstatus\t?? old.txt\n" in snap.read_text(encoding="utf-8")
    assert f"repo {guard.repo}: dirty at snapshot (1 entries" in out
    (guard.repo / "new.txt").write_text("x\n", encoding="utf-8")
    (guard.repo / ".git" / "new-inside-git").write_text("x\n", encoding="utf-8")
    code, out, _ = guard("check")
    assert code == 1
    lines = out.splitlines()
    assert "    > status ?? new.txt" in lines
    # Files modified since the snapshot: anything newer than its epoch, outside .git.
    assert "    " + str(guard.repo / "new.txt") in lines
    assert "new-inside-git" not in out


def test_snapshot_name_and_epoch_agree(guard):
    assert guard("snapshot")[0] == 0
    [snap] = files(guard, "snapshot-*.txt")
    epoch = int(snap.read_text(encoding="utf-8").splitlines()[0].split("\t")[1])
    assert snap.name == time.strftime("snapshot-%Y%m%dT%H%M%SZ.txt", time.gmtime(epoch))


def test_metadata_lines_are_in_gnu_finds_format(guard):
    """Path, size and mtime, as `find -printf "%P\\t%s\\t%T@\\n"` printed them before macOS
    needed them from lstat, so older snapshots still compare. Exact nanosecond mtimes are set,
    so the expected lines are literals; where GNU find is installed it is compared too."""
    notes = guard.home / "isr-notes"
    (notes / "sub" / "deeper").mkdir(parents=True)
    (notes / "empty").mkdir()
    made = [
        ("a.txt", 1790893616_494910943),
        ("sub/b", 1700000000_000000000),
        ("sub/deeper/c d", 1700000000_000000001),
        ("sub/x\ty", 5_123456789),
    ]
    for name, ns in made:
        (notes / name).write_text("hello", encoding="utf-8")
        os.utime(notes / name, ns=(ns, ns))
    (notes / "link").symlink_to("a.txt")
    (notes / "dirlink").symlink_to("sub")
    claude_json = guard.home / ".claude.json"
    claude_json.write_text("{}\n", encoding="utf-8")
    os.utime(claude_json, ns=(1790893616_999999999,) * 2)

    code, out, err = guard("snapshot")
    assert code == 0, out + err
    [snap] = files(guard, "snapshot-*.txt")
    prefix = f"meta:{notes}\t"
    listed = [
        line[len(prefix) :]
        for line in snap.read_text(encoding="utf-8").splitlines()
        if line.startswith(prefix)
    ]
    for name, ns in made:
        assert f"{name}\t5\t{ns // 10**9}.{ns % 10**9:09d}0" in listed
    names = [line.rsplit("\t", 2)[0] for line in listed]
    assert names == sorted(
        [
            "",
            "a.txt",
            "dirlink",
            "empty",
            "link",
            "sub",
            "sub/b",
            "sub/deeper",
            "sub/deeper/c d",
            "sub/x\ty",
        ],
        key=str.encode,
    )
    assert any(line.startswith("link\t5\t") for line in listed)  # the link itself, not followed
    assert f"info {claude_json}: 3 bytes, mtime 2026-10-01T22:26:56Z" in out.splitlines()

    gnu = shutil.which("find")
    probe = subprocess.run([gnu, str(notes), "-maxdepth", "0", "-printf", ""], capture_output=True)
    if probe.returncode == 0:  # GNU find: no -printf in BSD find
        found = subprocess.run(
            [gnu, str(notes), "-printf", "%P\\t%s\\t%T@\\n"],
            capture_output=True, check=True, env={**os.environ, "LC_ALL": "C"},
        ).stdout  # fmt: skip
        assert sorted(found.splitlines()) == sorted(line.encode() for line in listed)


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
