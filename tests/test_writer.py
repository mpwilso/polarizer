"""The writer: flat append cost, two processes, catch-up, stopping, and the fsync policy."""

import asyncio
import os
import subprocess
import sys
import textwrap
import time

import pytest
from conftest import build_chain

from polarizer import ledger as ledger_mod
from polarizer.canon import EntryRefused
from polarizer.ledger import LEDGER, verify_bytes
from polarizer.writer import FileOps, LedgerWriter, Stopped


class CountingOps(FileOps):
    """The writer's file layer, counting bytes read and fsyncs."""

    def __init__(self):
        self.bytes_read = 0
        self.fsyncs = 0

    def read_at(self, fd, offset, length):
        data = super().read_at(fd, offset, length)
        self.bytes_read += len(data)
        return data

    def fsync(self, fd):
        self.fsyncs += 1
        super().fsync(fd)


@pytest.fixture
def parse_counter(monkeypatch):
    counts = {"lines": 0}
    original = ledger_mod.ChainState.check_line

    def counting(self, raw):
        counts["lines"] += 1
        return original(self, raw)

    monkeypatch.setattr(ledger_mod.ChainState, "check_line", counting)
    return counts


def note(i):
    return ("note", {"i": i, "text": "an entry of a typical size " * 8})


def append_cost(tmp_path, size, parse_counter, foreign=0):
    """Bytes read and lines parsed by 20 appends to a ledger of `size` entries, after another
    writer appended `foreign` entries."""
    directory = tmp_path / f"ledger-{size}"
    build_chain(directory, [note(i) for i in range(size - 1)], head_at=0)
    ops = CountingOps()
    w = LedgerWriter.open(directory, ops=ops, idle_fsync=False)
    other = LedgerWriter.open(directory, idle_fsync=False)
    before = (directory / LEDGER).stat().st_size
    for i in range(foreign):
        other.append(*note(i)).result()
    foreign_bytes = (directory / LEDGER).stat().st_size - before
    ops.bytes_read, parse_counter["lines"] = 0, 0
    for i in range(20):
        w.append(*note(i)).result()
    cost = (ops.bytes_read, parse_counter["lines"], foreign_bytes)
    other.close()
    w.close()
    data = (directory / LEDGER).read_bytes()
    assert verify_bytes(data, (directory / "ledger.head").read_bytes()).status == "intact"
    return cost


def test_bytes_read_per_append_do_not_grow_with_ledger_size(tmp_path, parse_counter):
    small = append_cost(tmp_path, 100, parse_counter)
    large = append_cost(tmp_path, 10_000, parse_counter)
    assert small == large == (0, 0, 0)


def test_only_another_writers_new_bytes_are_read(tmp_path, parse_counter):
    read_s, parsed_s, foreign_s = append_cost(tmp_path, 100, parse_counter, foreign=3)
    read_l, parsed_l, foreign_l = append_cost(tmp_path, 10_000, parse_counter, foreign=3)
    assert (read_s, parsed_s) == (foreign_s, 3)
    assert (read_l, parsed_l) == (foreign_l, 3)


def test_two_writers_in_one_process_leave_one_chain(tmp_path):
    a = LedgerWriter.open(tmp_path / "l")
    b = LedgerWriter.open(tmp_path / "l")
    for i in range(30):
        (a if i % 3 else b).append(*note(i)).result()
    a.close()
    b.close()
    result = verify_bytes((tmp_path / "l" / LEDGER).read_bytes(), None)
    assert (result.status, result.state.lines) == ("intact", 31)


WORKER = textwrap.dedent(
    """
    import sys, time
    from pathlib import Path
    from polarizer.writer import LedgerWriter
    directory, go, count = Path(sys.argv[1]), Path(sys.argv[2]), int(sys.argv[3])
    while not go.exists():
        time.sleep(0.001)
    w = LedgerWriter.open(directory)
    for i in range(count):
        w.append("note", {"pid": sys.argv[4], "i": i}).result()
    w.close()
    """
)


def run_workers(directory, go, n_workers, count):
    procs = [
        subprocess.Popen(
            [sys.executable, "-c", WORKER, str(directory), str(go), str(count), str(k)],
            stderr=subprocess.PIPE,
        )
        for k in range(n_workers)
    ]
    time.sleep(0.3)  # let every worker start and wait on the flag
    go.write_text("go")
    for p in procs:
        _, err = p.communicate(timeout=120)
        assert p.returncode == 0, err.decode()


def test_two_processes_leave_one_chain(tmp_path):
    directory = tmp_path / "l"
    build_chain(directory, [], head_at=0)
    run_workers(directory, tmp_path / "go", 2, 150)
    result = verify_bytes(
        (directory / LEDGER).read_bytes(), (directory / "ledger.head").read_bytes()
    )
    assert (result.status, result.state.lines) == ("intact", 301)


def test_shrunk_ledger_stops_the_writer(tmp_path, capsys):
    w = LedgerWriter.open(tmp_path / "l")
    w.append(*note(1)).result()
    path = tmp_path / "l" / LEDGER
    data = path.read_bytes()
    path.write_bytes(data[: data.rfind(b"\n", 0, len(data) - 1) + 1])
    with pytest.raises(Stopped):
        w.append(*note(2)).result()
    with pytest.raises(Stopped):
        w.append(*note(3)).result()
    w.close()
    assert "stopped writing the ledger: the ledger is shorter" in capsys.readouterr().err
    assert path.read_bytes() == data[: data.rfind(b"\n", 0, len(data) - 1) + 1]


def test_foreign_line_that_does_not_chain_stops_the_writer(tmp_path, capsys):
    w = LedgerWriter.open(tmp_path / "l")
    with open(tmp_path / "l" / LEDGER, "ab") as f:
        f.write(b'{"v":1,"seq":1}\n')
    with pytest.raises(Stopped):
        w.append(*note(1)).result()
    w.close()
    assert "doesn't chain" in capsys.readouterr().err


def test_partial_foreign_line_stops_the_writer(tmp_path):
    w = LedgerWriter.open(tmp_path / "l")
    with open(tmp_path / "l" / LEDGER, "ab") as f:
        f.write(b'{"v":1,')
    with pytest.raises(Stopped, match="partial line"):
        w.append(*note(1)).result()
    w.close()


def test_refused_entry_does_not_stop_the_writer(tmp_path):
    w = LedgerWriter.open(tmp_path / "l")
    with pytest.raises(EntryRefused):
        w.append("note", {"x": 0.5}).result()
    with pytest.raises(EntryRefused):
        w.append("note", {"x": "y" * 17000}).result()
    assert w.append(*note(1)).result().seq == 1
    w.close()


def test_security_entries_are_fsynced_before_the_future_resolves(tmp_path):
    ops = CountingOps()
    w = LedgerWriter.open(tmp_path / "l", ops=ops, idle_fsync=False)
    start = ops.fsyncs
    w.append(*note(1)).result()
    assert ops.fsyncs == start  # call.sent-like entries are not fsynced inline
    w.append("tool.approved", {"upstream": "p", "tool": "t", "def_hash": "0" * 64}).result()
    assert ops.fsyncs == start + 2  # the entry, then the ledger.head temp file
    head = ledger_mod.parse_head((tmp_path / "l" / "ledger.head").read_bytes())
    assert head.seq == 2
    w.close()


def test_idle_fsync_when_the_queue_empties(tmp_path):
    ops = CountingOps()
    w = LedgerWriter.open(tmp_path / "l", ops=ops)
    start = ops.fsyncs
    w.append(*note(1)).result()
    deadline = time.monotonic() + 5
    while ops.fsyncs == start and time.monotonic() < deadline:
        time.sleep(0.01)
    assert ops.fsyncs == start + 1
    w.close()


def test_async_append(tmp_path):
    w = LedgerWriter.open(tmp_path / "l")

    async def go():
        return await asyncio.gather(*(w.append_async(*note(i)) for i in range(10)))

    results = asyncio.run(go())
    assert sorted(r.seq for r in results) == list(range(1, 11))
    w.close()


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX modes")
def test_modes(tmp_path):
    w = LedgerWriter.open(tmp_path / "new" / "ledger")
    w.close()
    directory = tmp_path / "new" / "ledger"
    assert os.stat(directory).st_mode & 0o777 == 0o700
    for name in ["ledger.jsonl", "ledger.jsonl.lock", "ledger.head"]:
        assert os.stat(directory / name).st_mode & 0o777 == 0o600
