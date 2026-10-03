"""verify creates, deletes and modifies nothing: a listing of every path, size and mtime is the
same before and after, on a writable directory and on a read-only one."""

import os
import stat
import sys

import pytest
from conftest import install_fixture

from polarizer import cli
from polarizer.ledger import LOCK
from polarizer.lock import LedgerLock

CASES = [
    "valid/session",  # intact
    "valid/no_head",  # intact, ledger.head missing: verify must not rebuild it
    "valid/v0-parallax",
    "broken/tear_last_line",  # torn tail: verify must not repair it
    "broken/truncated",
    "broken/edit_value",
]


def listing(root):
    """Every path under root (and root itself), with its size, mtime and mode."""
    out = {}
    for path in [root, *sorted(root.rglob("*"))]:
        st = path.lstat()
        out[str(path.relative_to(root))] = (st.st_size, st.st_mtime_ns, stat.S_IMODE(st.st_mode))
    return out


def make_read_only(root):
    for path in sorted(root.rglob("*"), reverse=True):
        os.chmod(path, 0o555 if path.is_dir() else 0o444)
    os.chmod(root, 0o555)


def make_writable(root):
    os.chmod(root, 0o755)
    for path in root.rglob("*"):
        os.chmod(path, 0o755 if path.is_dir() else 0o644)


def run_verify(directory, capsys, *extra):
    code = cli.main(["verify", "--ledger-dir", str(directory), *extra])
    return code, capsys.readouterr()


@pytest.mark.parametrize("read_only", [False, True])
@pytest.mark.parametrize("with_lock_file", [False, True])
@pytest.mark.parametrize("fixture", CASES)
def test_verify_changes_nothing(tmp_path, capsys, fixture, with_lock_file, read_only):
    directory = install_fixture(fixture, tmp_path / "ledger")
    (directory / "args").mkdir()
    (directory / "args" / "orphan.bin").write_bytes(b"x")
    if with_lock_file:
        LedgerLock.create(directory / LOCK).close()
    if read_only:
        if sys.platform == "win32":
            pytest.skip("POSIX permissions")
        make_read_only(directory)
    try:
        before = listing(directory)
        for extra in [(), ("--args",)]:
            code, _ = run_verify(directory, capsys, *extra)
            assert code in (0, 1, 5, 6)
        after = listing(directory)
    finally:
        if read_only:
            make_writable(directory)
    assert after == before
    assert (directory / LOCK).exists() == with_lock_file


def test_verify_of_a_missing_directory_creates_nothing(tmp_path, capsys):
    code, out = run_verify(tmp_path / "nowhere", capsys)
    assert (code, out.out) == (2, f"no ledger at {tmp_path / 'nowhere'}\n")
    assert not (tmp_path / "nowhere").exists()


def test_verify_opens_files_read_only(tmp_path, capsys, monkeypatch):
    directory = install_fixture("valid/session", tmp_path / "ledger")
    LedgerLock.create(directory / LOCK).close()
    real_open, flags_seen = os.open, []

    def spy(path, flags, *args, **kwargs):
        flags_seen.append((str(path), flags))
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", spy)
    assert run_verify(directory, capsys)[0] == 0
    write_flags = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_APPEND | os.O_TRUNC
    assert flags_seen and all(flags & write_flags == 0 for _, flags in flags_seen)
