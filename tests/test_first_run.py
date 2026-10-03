"""First run: one genesis even when processes start together; a missing ledger.head is
rebuilt and recorded; a v0 ledger or a broken one is refused."""

import json

import pytest
from conftest import build_chain, install_fixture
from test_writer import run_workers

from polarizer.ledger import LEDGER, parse_head, verify_bytes
from polarizer.writer import LedgerError, LedgerWriter


def entries(directory):
    return [json.loads(line) for line in (directory / LEDGER).read_bytes().splitlines()]


def test_genesis_written_once_when_processes_start_together(tmp_path):
    directory = tmp_path / "fresh" / "ledger"
    run_workers(directory, tmp_path / "go", 4, 20)
    kinds = [e["kind"] for e in entries(directory)]
    assert kinds.count("ledger.genesis") == 1 and kinds[0] == "ledger.genesis"
    assert len(kinds) == 81
    result = verify_bytes(
        (directory / LEDGER).read_bytes(), (directory / "ledger.head").read_bytes()
    )
    assert result.status == "intact"


def test_genesis_and_head_on_first_run(tmp_path):
    w = LedgerWriter.open(tmp_path / "l")
    w.close()
    (genesis,) = entries(tmp_path / "l")
    assert genesis["kind"] == "ledger.genesis" and len(genesis["data"]["chain_id"]) == 32
    head = parse_head((tmp_path / "l" / "ledger.head").read_bytes())
    assert (head.chain_id, head.seq, head.hash) == (genesis["data"]["chain_id"], 0, genesis["hash"])


def test_empty_ledger_file_counts_as_missing(tmp_path):
    (tmp_path / "l").mkdir()
    (tmp_path / "l" / LEDGER).write_bytes(b"")
    LedgerWriter.open(tmp_path / "l").close()
    assert [e["kind"] for e in entries(tmp_path / "l")] == ["ledger.genesis"]


def test_missing_head_is_rebuilt_and_recorded(tmp_path):
    built = build_chain(tmp_path / "l", [("note", {"i": 1}), ("note", {"i": 2})])
    LedgerWriter.open(tmp_path / "l").close()
    last = entries(tmp_path / "l")[-1]
    assert last["kind"] == "ledger.head_rebuilt"
    assert last["data"] == {"from_seq": 2, "from_hash": built[-1]["hash"]}
    head = parse_head((tmp_path / "l" / "ledger.head").read_bytes())
    assert (head.seq, head.hash) == (3, last["hash"])
    LedgerWriter.open(tmp_path / "l").close()  # a present head is not rebuilt again
    assert len(entries(tmp_path / "l")) == 4


@pytest.mark.parametrize(
    "fixture, code, line",
    [
        (
            "broken/edit_value",
            1,
            "polarizer: tampered: line 7 (seq 6): hash does not match the "
            "entry; run polarizer verify",
        ),
        ("broken/tear_last_line", 5, "polarizer: torn tail: "),
        (
            "broken/truncated",
            6,
            "polarizer: truncated: ledger ends at seq 7 but ledger.head "
            "records seq 10; run polarizer verify",
        ),
        ("valid/v0-parallax", 3, "polarizer: ledger at "),
    ],
)
def test_startup_refuses_anything_but_an_intact_v1_ledger(tmp_path, fixture, code, line):
    directory = install_fixture(fixture, tmp_path / "l")
    before = (directory / LEDGER).read_bytes()
    with pytest.raises(LedgerError) as raised:
        LedgerWriter.open(directory)
    assert raised.value.exit_code == code
    assert raised.value.line.startswith(line)
    assert (directory / LEDGER).read_bytes() == before
