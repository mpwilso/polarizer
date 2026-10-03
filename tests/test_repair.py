"""Lock waits (2 s, then refuse with exit 7), and repair, which fixes only a torn tail."""

import json
import threading
import time

import pytest
from conftest import build_chain, install_fixture

from polarizer import cli
from polarizer.ledger import LEDGER, LOCK, LOCKED_LINE, parse_head, verify_bytes
from polarizer.lock import LedgerLock
from polarizer.writer import LedgerError, LedgerWriter, repair

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
    assert 1.9 <= took < 4


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
