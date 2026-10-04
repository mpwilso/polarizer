"""Lock waits (2 s, then refuse with exit 7), and repair, which fixes only a torn tail."""

import hashlib
import json
import os
import sys
import threading
import time

import pytest
from conftest import build_chain, install_fixture

from polarizer import cli
from polarizer.ledger import LEDGER, LOCK, LOCKED_LINE, parse_head, verify_bytes
from polarizer.lock import LedgerLock
from polarizer.writer import FileOps, LedgerError, LedgerWriter, repair

END = "; repair only fixes a torn tail"


def hold_lock(directory):
    lock = LedgerLock.create(directory / LOCK)
    assert lock.acquire(0)
    return lock


def timed(fn):
    start = time.monotonic()
    value = fn()
    return value, time.monotonic() - start


@pytest.mark.parametrize("command", ["verify", "repair"])
def test_commands_wait_two_seconds_then_refuse(tmp_path, capsys, command):
    directory = install_fixture("valid/session", tmp_path / "l")
    lock = hold_lock(directory)
    code, took = timed(lambda: cli.main([command, "--ledger-dir", str(directory)]))
    lock.close()
    assert code == 7
    assert capsys.readouterr().out == LOCKED_LINE + "\n"
    assert 1.9 <= took < 10  # the wait is 2 s; the upper bound is 5 times that


def test_startup_waits_two_seconds_then_refuses(tmp_path):
    directory = install_fixture("valid/session", tmp_path / "l")
    lock = hold_lock(directory)
    with pytest.raises(LedgerError) as raised:
        timed(lambda: LedgerWriter.open(directory))
    lock.close()
    assert raised.value.exit_code == 7
    assert raised.value.line == "polarizer: " + LOCKED_LINE


def test_commands_proceed_when_the_lock_frees_within_the_wait(tmp_path, capsys):
    directory = install_fixture("valid/session", tmp_path / "l")
    lock = hold_lock(directory)
    threading.Timer(0.5, lock.close).start()
    assert cli.main(["verify", "--ledger-dir", str(directory)]) == 0
    assert capsys.readouterr().out.startswith("intact: ")


def test_repair_moves_the_torn_bytes_and_records_it(tmp_path):
    directory = install_fixture("broken/tear_last_line", tmp_path / "l")
    data = (directory / LEDGER).read_bytes()
    complete = data[: data.rfind(b"\n") + 1]
    outcome = repair(directory)
    assert outcome.exit_code == 0
    (line,) = outcome.lines
    assert line.startswith(
        f"repaired: moved {len(data) - len(complete)} bytes to ledger.jsonl.torn-12-"
    )
    assert line.endswith("; appended ledger.repaired at seq 12")
    torn_name = line.split(" to ")[1].split(";")[0]
    assert (directory / torn_name).read_bytes() == data[len(complete) :]
    after = (directory / LEDGER).read_bytes()
    assert after.startswith(complete)
    last = json.loads(after.splitlines()[-1])
    assert last["kind"] == "ledger.repaired" and last["data"]["file"] == torn_name
    assert last["data"]["bytes"] == len(data) - len(complete)
    head = (directory / "ledger.head").read_bytes()
    assert parse_head(head).seq == 12
    assert verify_bytes(after, head).status == "intact"


def test_repair_of_a_torn_genesis_starts_a_new_chain(tmp_path):
    directory = install_fixture("broken/torn_genesis", tmp_path / "l")
    outcome = repair(directory)
    assert outcome.exit_code == 0
    assert outcome.lines[0].endswith("; appended ledger.repaired at seq 1")
    assert ".torn-0-" in outcome.lines[0]
    kinds = [json.loads(x)["kind"] for x in (directory / LEDGER).read_bytes().splitlines()]
    assert kinds == ["ledger.genesis", "ledger.repaired"]
    result = verify_bytes(
        (directory / LEDGER).read_bytes(), (directory / "ledger.head").read_bytes()
    )
    assert result.status == "intact"


def test_repair_rechecks_under_the_lock(tmp_path):
    """A torn tail seen before the lock doesn't count: here the writer finishes its line while
    repair waits, so repair finds an intact ledger."""
    entries = build_chain(tmp_path / "l", [("note", {"i": 1})], head_at=0)
    directory = tmp_path / "l"
    full = (directory / LEDGER).read_bytes()
    cut = full.rfind(b"\n", 0, len(full) - 1) + 1 + 10
    (directory / LEDGER).write_bytes(full[:cut])
    assert verify_bytes(full[:cut], None).status == "torn tail"
    lock = hold_lock(directory)

    def finish():
        (directory / LEDGER).write_bytes(full)
        lock.close()

    threading.Timer(0.5, finish).start()
    outcome = repair(directory)
    assert outcome.lines == ["nothing to repair: ledger is intact"] and outcome.exit_code == 0
    assert (directory / LEDGER).read_bytes() == full
    assert entries


@pytest.mark.parametrize(
    "fixture, code, line",
    [
        ("broken/edit_value", 1, "refused: ledger is tampered at line 7" + END),
        ("broken/insert_float", 3, "refused: ledger is invalid at line 7" + END),
        ("broken/reorder_keys", 4, "refused: ledger is not canonical at line 5" + END),
        ("broken/head_hash_mismatch", 1, "refused: ledger is tampered at ledger.head" + END),
        ("broken/head_wrong_chain", 3, "refused: ledger is invalid at ledger.head" + END),
        (
            "broken/torn_tail_and_truncated",
            6,
            "refused: ledger is truncated (ledger.head records seq 10)" + END,
        ),
        (
            "broken/v0_torn",
            3,
            "refused: ledger is v0 (Parallax's format); Polarizer only writes v1",
        ),
        ("valid/session", 0, "nothing to repair: ledger is intact"),
    ],
)
def test_repair_refuses_everything_but_a_torn_tail(tmp_path, fixture, code, line):
    directory = install_fixture(fixture, tmp_path / "l")
    before = {p.name: p.read_bytes() for p in directory.iterdir() if p.name != LOCK}
    outcome = repair(directory)
    assert (outcome.lines, outcome.exit_code) == ([line], code)
    after = {p.name: p.read_bytes() for p in directory.iterdir() if p.name != LOCK}
    assert after == before


def test_repair_without_a_ledger_creates_nothing(tmp_path):
    outcome = repair(tmp_path / "missing")
    assert (outcome.lines, outcome.exit_code) == ([f"no ledger at {tmp_path / 'missing'}"], 2)
    assert not (tmp_path / "missing").exists()


class Crash(Exception):
    """Stands in for the process dying: nothing after it in repair runs."""


class CrashAfterFsync(FileOps):
    """Completes the nth fsync, then crashes. In repair, fsync 1 is the side file's (the torn
    bytes are saved), fsync 2 the ledger's after the truncate, fsync 3 a new genesis's."""

    def __init__(self, n):
        self.n = n
        self.count = 0

    def fsync(self, fd):
        super().fsync(fd)
        self.count += 1
        if self.count == self.n:
            raise Crash


def torn_names(directory):
    return sorted(p.name for p in directory.iterdir() if ".torn-" in p.name)


def test_crash_after_saving_the_torn_bytes(tmp_path, capsys):
    """The ledger and ledger.head are untouched and the side file holds the torn bytes. A
    second repair picks the same name, finds the bytes equal, and reuses the file as it is
    (same inode, same bytes) to finish the repair."""
    directory = install_fixture("broken/tear_last_line", tmp_path / "l")
    data = (directory / LEDGER).read_bytes()
    head = (directory / "ledger.head").read_bytes()
    torn = data[data.rfind(b"\n") + 1 :]
    with pytest.raises(Crash):
        repair(directory, ops=CrashAfterFsync(1))
    assert (directory / LEDGER).read_bytes() == data
    assert (directory / "ledger.head").read_bytes() == head
    (name,) = torn_names(directory)
    assert name.startswith("ledger.jsonl.torn-12-")
    assert (directory / name).read_bytes() == torn
    saved = os.stat(directory / name)
    capsys.readouterr()
    assert cli.main(["repair", "--ledger-dir", str(directory)]) == 0
    out, err = capsys.readouterr()
    assert err == ""
    assert out == (
        f"repaired: moved {len(torn)} bytes to {name} (an earlier repair had saved them); "
        "appended ledger.repaired at seq 12\n"
    )
    assert torn_names(directory) == [name]
    assert (directory / name).read_bytes() == torn
    assert (os.stat(directory / name).st_ino, os.stat(directory / name).st_mtime_ns) == (
        saved.st_ino,
        saved.st_mtime_ns,
    )
    check_repaired(directory, data[: len(data) - len(torn)], torn, name)


def check_repaired(directory, complete, torn, name):
    """The ledger is complete + ledger.repaired naming the side file, and verifies intact."""
    after = (directory / LEDGER).read_bytes()
    assert after.startswith(complete)
    last = json.loads(after.splitlines()[-1])
    assert last["kind"] == "ledger.repaired"
    assert last["data"] == {
        "bytes": len(torn),
        "sha256": hashlib.sha256(torn).hexdigest(),
        "file": name,
    }
    assert verify_bytes(after, (directory / "ledger.head").read_bytes()).status == "intact"


def side_file_name(data):
    """The side file name repair picks for a ledger's torn tail."""
    torn = data[data.rfind(b"\n") + 1 :]
    seq = data.count(b"\n")
    return f"{LEDGER}.torn-{seq}-{hashlib.sha256(torn).hexdigest()[:12]}", torn


@pytest.mark.parametrize("fixture", ["broken/tear_last_line", "broken/torn_genesis"])
def test_repair_reuses_a_side_file_that_matches(tmp_path, fixture):
    """A side file under repair's name whose bytes equal the torn bytes is reused: not
    created again, not written, and the repair runs to the end."""
    directory = install_fixture(fixture, tmp_path / "l")
    data = (directory / LEDGER).read_bytes()
    name, torn = side_file_name(data)
    (directory / name).write_bytes(torn)
    os.chmod(directory / name, 0o600)
    saved = os.stat(directory / name)
    outcome = repair(directory)
    assert outcome.exit_code == 0
    assert outcome.lines[0].startswith(
        f"repaired: moved {len(torn)} bytes to {name} (an earlier repair had saved them); "
    )
    assert torn_names(directory) == [name]
    assert (directory / name).read_bytes() == torn
    now = os.stat(directory / name)
    assert (now.st_ino, now.st_mtime_ns, now.st_size) == (
        saved.st_ino,
        saved.st_mtime_ns,
        saved.st_size,
    )
    check_repaired(directory, data[: len(data) - len(torn)], torn, name)


DIFFERENT = {
    "a part of the torn bytes": lambda torn: torn[: len(torn) // 2],
    "empty": lambda torn: b"",
    "same length, other bytes": lambda torn: bytes(b ^ 1 for b in torn),
    "the torn bytes and more": lambda torn: torn + b"x",
}


@pytest.mark.parametrize("contents", list(DIFFERENT), ids=list(DIFFERENT))
def test_repair_refuses_a_side_file_that_differs(tmp_path, capsys, contents):
    """A side file under repair's name with other bytes (a crash in the middle of saving them,
    or a change by hand) is refused: one line naming the file, exit 2, nothing changed."""
    directory = install_fixture("broken/tear_last_line", tmp_path / "l")
    data = (directory / LEDGER).read_bytes()
    name, torn = side_file_name(data)
    (directory / name).write_bytes(DIFFERENT[contents](torn))
    before = {p.name: p.read_bytes() for p in directory.iterdir() if p.name != LOCK}
    saved = os.stat(directory / name)
    assert cli.main(["repair", "--ledger-dir", str(directory)]) == 2
    out, err = capsys.readouterr()
    assert out == ""
    assert err == (
        f"polarizer: cannot repair: {directory / name} already exists and its contents differ "
        "from the ledger's torn tail; nothing was changed\n"
    )
    after = {p.name: p.read_bytes() for p in directory.iterdir() if p.name != LOCK}
    assert after == before
    assert os.stat(directory / name).st_mtime_ns == saved.st_mtime_ns


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need privileges on Windows")
def test_repair_does_not_follow_a_side_file_link(tmp_path, capsys):
    """A symlink under repair's name is refused even when its target holds the torn bytes:
    exit 2, the target untouched, nothing changed."""
    directory = install_fixture("broken/tear_last_line", tmp_path / "l")
    data = (directory / LEDGER).read_bytes()
    name, torn = side_file_name(data)
    target = tmp_path / "elsewhere"
    target.write_bytes(torn)
    (directory / name).symlink_to(target)
    before = {p.name: p.read_bytes() for p in directory.iterdir() if p.name != LOCK}
    assert cli.main(["repair", "--ledger-dir", str(directory)]) == 2
    out, err = capsys.readouterr()
    assert out == "" and err.startswith(f"polarizer: cannot repair {directory / name}: ")
    assert len(err.splitlines()) == 1
    assert {p.name: p.read_bytes() for p in directory.iterdir() if p.name != LOCK} == before
    assert target.read_bytes() == torn and (directory / name).is_symlink()


def test_crash_after_the_truncate(tmp_path):
    """The ledger is intact, ending at the last complete line, with no ledger.repaired; the
    side file holds the removed bytes. A second repair finds nothing to do."""
    directory = install_fixture("broken/tear_last_line", tmp_path / "l")
    data = (directory / LEDGER).read_bytes()
    head = (directory / "ledger.head").read_bytes()
    complete = data[: data.rfind(b"\n") + 1]
    with pytest.raises(Crash):
        repair(directory, ops=CrashAfterFsync(2))
    assert (directory / LEDGER).read_bytes() == complete
    assert (directory / "ledger.head").read_bytes() == head
    assert verify_bytes(complete, head).status == "intact"
    (name,) = torn_names(directory)
    assert (directory / name).read_bytes() == data[len(complete) :]
    kinds = [json.loads(x)["kind"] for x in complete.splitlines()]
    assert "ledger.repaired" not in kinds
    outcome = repair(directory)
    assert (outcome.lines, outcome.exit_code) == (["nothing to repair: ledger is intact"], 0)


def test_crash_after_truncating_a_torn_genesis(tmp_path):
    """The ledger is empty: repair says there is no ledger (exit 2), and the next open writes
    a genesis with a new chain id, with no ledger.repaired."""
    directory = install_fixture("broken/torn_genesis", tmp_path / "l")
    data = (directory / LEDGER).read_bytes()
    with pytest.raises(Crash):
        repair(directory, ops=CrashAfterFsync(2))
    assert (directory / LEDGER).read_bytes() == b""
    (name,) = torn_names(directory)
    assert name.startswith("ledger.jsonl.torn-0-") and (directory / name).read_bytes() == data
    outcome = repair(directory)
    assert (outcome.lines, outcome.exit_code) == ([f"no ledger at {directory}"], 2)
    LedgerWriter.open(directory).close()
    lines = (directory / LEDGER).read_bytes().splitlines()
    (genesis,) = [json.loads(x) for x in lines]
    assert genesis["kind"] == "ledger.genesis"
    assert genesis["data"]["chain_id"].encode() not in data


def test_crash_after_the_new_genesis(tmp_path):
    """A torn genesis, crashed after the new genesis is fsynced and before ledger.head is
    written: one intact entry under a new chain id and no ledger.head. repair finds nothing to
    do; the next open rebuilds ledger.head and records ledger.head_rebuilt."""
    directory = install_fixture("broken/torn_genesis", tmp_path / "l")
    data = (directory / LEDGER).read_bytes()
    with pytest.raises(Crash):
        repair(directory, ops=CrashAfterFsync(3))
    (genesis,) = [json.loads(x) for x in (directory / LEDGER).read_bytes().splitlines()]
    assert genesis["kind"] == "ledger.genesis"
    assert genesis["data"]["chain_id"].encode() not in data
    assert not (directory / "ledger.head").exists()
    assert repair(directory).lines == ["nothing to repair: ledger is intact"]
    LedgerWriter.open(directory).close()
    kinds = [json.loads(x)["kind"] for x in (directory / LEDGER).read_bytes().splitlines()]
    assert kinds == ["ledger.genesis", "ledger.head_rebuilt"]


def test_crash_after_ledger_repaired(tmp_path):
    """Crashed after ledger.repaired is fsynced, before ledger.head moves: the ledger is intact
    with the record, and ledger.head lags behind it, which verify accepts."""
    directory = install_fixture("broken/tear_last_line", tmp_path / "l")
    head = (directory / "ledger.head").read_bytes()
    with pytest.raises(Crash):
        repair(directory, ops=CrashAfterFsync(3))
    after = (directory / LEDGER).read_bytes()
    last = json.loads(after.splitlines()[-1])
    assert (last["kind"], last["seq"]) == ("ledger.repaired", 12)
    assert (directory / "ledger.head").read_bytes() == head
    assert parse_head(head).seq < 12
    assert verify_bytes(after, head).status == "intact"
    assert repair(directory).lines == ["nothing to repair: ledger is intact"]
