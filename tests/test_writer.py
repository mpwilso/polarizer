"""The writer: flat append cost, two processes, catch-up, stopping, and the fsync policy."""

import asyncio
import json
import os
import queue
import subprocess
import sys
import threading
import time
import types
from concurrent.futures import wait
from pathlib import Path

import pytest
from conftest import build_chain

from polarizer import ledger as ledger_mod
from polarizer import writer as writer_mod
from polarizer.canon import EntryRefused
from polarizer.ledger import LEDGER, verify_bytes
from polarizer.writer import Appended, FileOps, LedgerError, LedgerWriter, Stopped


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


WORKER = Path(__file__).parent / "helpers" / "append_worker.py"


def run_workers(directory, go, n_workers, count):
    procs = [
        subprocess.Popen(
            [sys.executable, str(WORKER), str(directory), str(go), str(count), str(k)],
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


# A listener's error text, as an upstream-built exception might carry: ESC, a newline, a C1
# control. The stderr line must show it escaped, on one line.
HOSTILE = "bad" + chr(0x1B) + "[2J\nsecond line" + chr(0x9B)
HOSTILE_SAFE = "bad\\x1b[2J second line\\x9b"


def failing_listener(seen):
    """An on_entry that records each seq it is given and raises on entries marked boom."""

    def listener(entry):
        seen.append(entry["seq"])
        if entry["data"].get("boom"):
            raise RuntimeError(HOSTILE)

    return listener


def ledger_entries(directory):
    return [json.loads(x) for x in (directory / LEDGER).read_bytes().splitlines()]


FOLD_STOPPED = "polarizer: stopped writing the ledger: could not fold seq {} into pin state: "


@pytest.mark.parametrize("kind", ["call.sent", "tool.approved"])
def test_listener_failure_on_append_resolves_then_stops(tmp_path, capsys, kind):
    """The line is written (and fsynced for a security kind) before the listener runs, so the
    append resolves to it. Pin state now lacks that entry, so the writer stops: one escaped
    stderr line, and every later append and catch-up raises Stopped."""
    seen = []
    w = LedgerWriter.open(tmp_path / "l", on_entry=failing_listener(seen))
    capsys.readouterr()
    data = {"upstream": "p", "tool": "t", "def_hash": "0" * 64, "boom": True}
    got = w.append(kind, data).result(timeout=5)
    stopped = FOLD_STOPPED.format(1) + HOSTILE_SAFE
    assert w.stopped == stopped
    for later in (w.append(*note(2)), w.catch_up()):
        with pytest.raises(Stopped) as raised:
            later.result(timeout=5)
        assert raised.value.line == stopped
    w.close()
    entries = ledger_entries(tmp_path / "l")
    assert got == Appended(1, entries[1]["hash"])
    assert entries[1]["kind"] == kind and entries[1]["data"] == data
    assert seen == [1]  # a genesis written at first run is not handed over
    assert capsys.readouterr().err.splitlines() == [stopped]
    result = verify_bytes((tmp_path / "l" / LEDGER).read_bytes(), None)
    assert (result.status, result.state.lines) == ("intact", 2)


@pytest.mark.parametrize("via", ["catch_up", "append"])
def test_listener_failure_on_catch_up_stops_the_writer(tmp_path, capsys, via):
    """Another writer appends three entries, the middle one failing in the listener. All three
    are adopted; the writer stops at the failing one, and the entry after it is not handed
    over. A catch-up still resolves to the count it adopted; an append whose catch-up stopped
    writes nothing and raises Stopped. Nothing is written afterwards."""
    seen = []
    w = LedgerWriter.open(tmp_path / "l", on_entry=failing_listener(seen))
    other = LedgerWriter.open(tmp_path / "l")
    other.append(*note(1)).result(timeout=5)
    other.append("note", {"boom": True}).result(timeout=5)
    other.append(*note(3)).result(timeout=5)
    other.close()
    capsys.readouterr()
    stopped = FOLD_STOPPED.format(2) + HOSTILE_SAFE
    if via == "catch_up":
        assert w.catch_up().result(timeout=5) == 3
    else:
        with pytest.raises(Stopped) as raised:
            w.append(*note(4)).result(timeout=5)
        assert raised.value.line == stopped
    assert seen == [1, 2]
    assert w.stopped == stopped
    with pytest.raises(Stopped):
        w.append(*note(5)).result(timeout=5)
    w.close()
    assert capsys.readouterr().err.splitlines() == [stopped]
    raw = (tmp_path / "l" / LEDGER).read_bytes()
    result = verify_bytes(raw, None)
    assert (result.status, result.state.lines) == ("intact", 4)  # genesis and the three


@pytest.mark.parametrize("head_at", [2, None])
def test_listener_failure_at_open_refuses_to_start(tmp_path, head_at):
    """Pin state could not be folded, so open refuses with one escaped line and exit 1, writes
    nothing (not even a missing ledger.head), and releases the lock."""
    directory = tmp_path / "l"
    build_chain(directory, [note(1), ("note", {"boom": True}), note(3)], head_at=head_at)
    before = {p.name: p.read_bytes() for p in directory.iterdir()}
    seen = []
    with pytest.raises(LedgerError) as raised:
        LedgerWriter.open(directory, on_entry=failing_listener(seen))
    assert raised.value.exit_code == 1
    assert raised.value.line == f"polarizer: could not fold seq 2 into pin state: {HOSTILE_SAFE}"
    assert seen == [0, 1, 2]
    after = {p.name: p.read_bytes() for p in directory.iterdir() if p.name != "ledger.jsonl.lock"}
    assert after == before
    LedgerWriter.open(directory, lock_wait=0).close()  # the lock was released


def test_listener_ledger_error_at_open_is_passed_on(tmp_path):
    """A listener that raises LedgerError at open refuses with its own line and code (serve on
    a drill ledger, a drill on a serve ledger); nothing is written, not even a missing
    ledger.head, and the lock is released. The same ledger opened with a listener that raises
    any other error refuses with the fold line and exit 1, and opened with no listener it opens
    and rebuilds its head, both as before stage 8."""
    directory = tmp_path / "l"
    build_chain(directory, [note(1), ("note", {"refuse": True}), note(3)], head_at=None)
    before = {p.name: p.read_bytes() for p in directory.iterdir()}

    def opened_with(error):
        def listener(entry):
            if entry["data"].get("refuse"):
                raise error

        with pytest.raises(LedgerError) as raised:
            LedgerWriter.open(directory, on_entry=listener)
        after = {
            p.name: p.read_bytes() for p in directory.iterdir() if p.name != "ledger.jsonl.lock"
        }
        assert after == before
        return raised.value

    refused = opened_with(LedgerError("polarizer: not this ledger", 2))
    assert (type(refused), refused.line, refused.exit_code) == (
        LedgerError, "polarizer: not this ledger", 2)  # fmt: skip
    failed = opened_with(ValueError("no"))
    assert (failed.line, failed.exit_code) == (
        "polarizer: could not fold seq 2 into pin state: no",
        1,
    )
    LedgerWriter.open(directory, lock_wait=0).close()  # no listener; the lock was released
    assert ledger_entries(directory)[-1]["kind"] == "ledger.head_rebuilt"


class SlowPutQueue(queue.Queue):
    """A queue whose put pauses before it enqueues work (not the close sentinel), which
    widens the gap between an append's closed check and its put."""

    def put(self, item, block=True, timeout=None):
        if item is not None:
            time.sleep(0.001)
        super().put(item, block, timeout)


@pytest.mark.parametrize("slow_put", [False, True])
def test_close_racing_appenders_leaves_no_future_pending(tmp_path, monkeypatch, slow_put):
    """Eight threads append (one catches up) while close() runs. Every future resolves within
    a few seconds: an append to Appended or Stopped, a catch-up to a count or Stopped."""
    if slow_put:
        monkeypatch.setattr(writer_mod, "queue", types.SimpleNamespace(Queue=SlowPutQueue))
    w = LedgerWriter.open(tmp_path / "l", idle_fsync=False)
    appends, catch_ups, guard = [], [], threading.Lock()
    started = threading.Barrier(9)

    def appender(k):
        started.wait()
        for i in range(100_000):
            f = w.append("note", {"k": k, "i": i}) if k else w.catch_up()
            with guard:
                (appends if k else catch_ups).append(f)
            if f.done() and isinstance(f.exception(), Stopped):
                return

    threads = [threading.Thread(target=appender, args=(k,), daemon=True) for k in range(8)]
    for t in threads:
        t.start()
    started.wait()
    deadline = time.monotonic() + 5
    while len(appends) < 200 and time.monotonic() < deadline:
        time.sleep(0.001)
    w.close()
    for t in threads:
        t.join(timeout=5)
        assert not t.is_alive()
    _, pending = wait(appends + catch_ups, timeout=5)
    assert not pending, f"{len(pending)} futures never resolved"
    written = []
    for f in appends:
        if isinstance(f.exception(), Stopped):
            continue
        assert isinstance(f.result(), Appended)
        written.append(f.result().seq)
    for f in catch_ups:
        assert isinstance(f.exception(), Stopped) or f.result() == 0
    result = verify_bytes((tmp_path / "l" / LEDGER).read_bytes(), None)
    assert result.status == "intact"
    assert sorted(written) == list(range(1, result.state.lines))


# The conditional append (docs/HOLD-SPEC.md, section 6) ------------------------------------

CLAIM_WORKER = Path(__file__).parent / "helpers" / "claim_worker.py"


class _Refused(Exception):
    pass


def test_conditional_append_refuses_and_writes_nothing(tmp_path):
    """A check that raises: nothing is written, the future raises that, and the writer goes
    on. A check that passes: the entry is written as usual, fsynced if its kind requires."""
    directory = tmp_path / "l"
    build_chain(directory, [], head_at=0)
    seen = []
    writer = LedgerWriter.open(directory, on_entry=lambda e: seen.append(e["kind"]))
    try:
        before = (directory / LEDGER).read_bytes()

        def refuse():
            raise _Refused("no")

        with pytest.raises(_Refused):
            writer.append("note", {"n": 1}, check=refuse).result()
        assert (directory / LEDGER).read_bytes() == before
        calls = []
        done = writer.append("hold.decided", {"n": 2}, check=lambda: calls.append(1)).result()
        assert calls == [1] and done.seq == 1
        head = json.loads((directory / "ledger.head").read_text())
        assert head["seq"] == 1  # hold.decided is a security kind: fsynced, head moved
        assert writer.append("note", {"n": 3}).result().seq == 2
    finally:
        writer.close()
    assert seen == ["ledger.genesis", "hold.decided", "note"]


def test_conditional_append_sees_what_another_process_wrote(tmp_path):
    """The check runs after the catch-up, so it sees an entry another writer appended after
    this one opened."""
    directory = tmp_path / "l"
    build_chain(directory, [], head_at=0)
    folded = []
    first = LedgerWriter.open(directory, on_entry=lambda e: folded.append(e["kind"]))
    second = LedgerWriter.open(directory)
    try:
        second.append("claim", {"w": 2}).result()

        def check():
            if "claim" in folded:
                raise _Refused("taken")

        with pytest.raises(_Refused):
            first.append("claim", {"w": 1}, check=check).result()
    finally:
        first.close()
        second.close()
    kinds = [json.loads(line)["kind"] for line in (directory / LEDGER).read_bytes().splitlines()]
    assert kinds.count("claim") == 1


@pytest.mark.parametrize("workers", [2, 4])
def test_conditional_append_two_processes_race(tmp_path, workers):
    """Processes that each opened the ledger before any claim race to write one: exactly one
    claim is written, and every other process names the winner's seq."""
    directory = tmp_path / "l"
    build_chain(directory, [], head_at=0)
    go = tmp_path / "go"
    procs = [
        subprocess.Popen(
            [sys.executable, str(CLAIM_WORKER), str(directory), str(go), str(k)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        for k in range(workers)
    ]
    deadline = time.monotonic() + 60
    while not all(Path(f"{go}.ready-{k}").exists() for k in range(workers)):
        assert time.monotonic() < deadline, "the workers never opened the ledger"
        time.sleep(0.01)
    go.write_text("go")
    lines = []
    for p in procs:
        out, err = p.communicate(timeout=120)
        assert p.returncode == 0, err.decode()
        lines.append(out.decode().strip())
    won = [line for line in lines if line.startswith("won ")]
    assert len(won) == 1, lines
    seq = won[0].split()[1]
    assert sorted(lines) == sorted([won[0]] + [f"lost {seq}"] * (workers - 1))
    entries = [json.loads(line) for line in (directory / LEDGER).read_bytes().splitlines()]
    assert [e["kind"] for e in entries].count("claim") == 1
    result = verify_bytes(
        (directory / LEDGER).read_bytes(), (directory / "ledger.head").read_bytes()
    )
    assert result.status == "intact"
