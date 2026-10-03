"""ledger.head: its format, moving only forward, truncation, and the Windows replace retry."""

import os
import sys

import pytest
from conftest import build_chain

from polarizer import writer
from polarizer.ledger import LEDGER, head_bytes, parse_head, verify_bytes
from polarizer.writer import LedgerWriter

REAL_REPLACE = os.replace
APPROVE = {"upstream": "p", "tool": "t", "def_hash": "0" * 64, "actor": "test"}


def read_head(directory):
    return parse_head((directory / "ledger.head").read_bytes())


def test_head_is_canonical_json_of_three_keys(tmp_path):
    LedgerWriter.open(tmp_path / "l").close()
    raw = (tmp_path / "l" / "ledger.head").read_bytes()
    assert raw.startswith(b'{"chain_id":"') and raw.endswith(b"}\n") and raw.count(b"\n") == 1
    assert parse_head(raw) is not None


@pytest.mark.parametrize(
    "raw",
    [
        b'{"chain_id": "%s", "hash": "%s", "seq": 1}\n',
        b'{"chain_id":"%s","hash":"%s","seq":1}',
        b'{"chain_id":"%s","hash":"%s","seq":1}\n\n',
        b'{"chain_id":"%s","hash":"%s","seq":-1}\n',
        b'{"chain_id":"%s","hash":"%s","seq":true}\n',
        b'{"chain_id":"%s","hash":"%s","seq":1.0}\n',
        b'{"chain_id":"%s","hash":"%s","seq":1,"x":0}\n',
    ],
)
def test_malformed_heads_are_invalid(raw):
    assert parse_head(raw % (b"a" * 32, b"b" * 64)) is None
    assert parse_head(head_bytes("a" * 32, "b" * 64, 1)) is not None
    assert parse_head(head_bytes("A" * 32, "b" * 64, 1)) is None


def test_head_only_moves_forward(tmp_path):
    directory = tmp_path / "l"
    a = LedgerWriter.open(directory)
    b = LedgerWriter.open(directory)
    a.append("tool.approved", APPROVE).result()  # seq 1
    b.append("tool.approved", APPROVE).result()  # seq 2
    assert read_head(directory).seq == 2
    # A head written ahead by someone else is not moved back.
    ahead = head_bytes(a.chain_id, "c" * 64, 50)
    (directory / "ledger.head").write_bytes(ahead)
    a.append("tool.approved", APPROVE).result()
    assert (directory / "ledger.head").read_bytes() == ahead
    a.close()
    b.close()


def test_plain_entries_do_not_move_the_head(tmp_path):
    w = LedgerWriter.open(tmp_path / "l")
    w.append("call.sent", {"x": 1}).result()
    w.close()
    assert read_head(tmp_path / "l").seq == 0


def test_lost_lines_are_detected_as_truncated(tmp_path):
    directory = tmp_path / "l"
    w = LedgerWriter.open(directory)
    w.append("note", {"i": 1}).result()
    w.append("tool.approved", APPROVE).result()  # head at seq 2
    w.close()
    data = (directory / LEDGER).read_bytes()
    restored = data[: data.rfind(b"\n", 0, len(data) - 1) + 1]  # an older backup
    result = verify_bytes(restored, (directory / "ledger.head").read_bytes())
    assert (result.status, result.exit_code) == ("truncated", 6)
    assert result.message == "truncated: ledger ends at seq 1 but ledger.head records seq 2"


def test_head_of_another_chain_is_invalid(tmp_path):
    entries = build_chain(tmp_path / "l", [("note", {})])
    other = head_bytes("f" * 32, entries[1]["hash"], 1)
    result = verify_bytes((tmp_path / "l" / LEDGER).read_bytes(), other)
    assert (result.status, result.exit_code) == ("invalid", 3)
    assert result.message == "invalid: ledger.head: chain_id does not match the ledger"


class FlakyReplace:
    def __init__(self, failures):
        self.failures = failures
        self.calls = 0

    def __call__(self, src, dst):
        self.calls += 1
        if self.calls <= self.failures:
            raise PermissionError(13, "the file is open without delete sharing")
        return REAL_REPLACE(src, dst)


def test_replace_retry_succeeds_within_five_retries(tmp_path, monkeypatch):
    w = LedgerWriter.open(tmp_path / "l")
    flaky = FlakyReplace(failures=5)
    monkeypatch.setattr(writer.os, "replace", flaky)
    monkeypatch.setattr(writer, "REPLACE_PAUSE", 0.001)
    w.append("tool.approved", APPROVE).result()
    w.close()
    assert flaky.calls == 6
    assert read_head(tmp_path / "l").seq == 1


def test_replace_failing_every_time_leaves_temp_and_carries_on(tmp_path, monkeypatch, capsys):
    directory = tmp_path / "l"
    w = LedgerWriter.open(directory)
    flaky = FlakyReplace(failures=6)
    monkeypatch.setattr(writer.os, "replace", flaky)
    monkeypatch.setattr(writer, "REPLACE_PAUSE", 0.001)
    w.append("tool.approved", APPROVE).result()  # seq 1: head can't be replaced
    assert flaky.calls == 6
    assert "could not update ledger.head" in capsys.readouterr().err
    assert (directory / f"ledger.head.tmp-{os.getpid()}").exists()
    assert read_head(directory).seq == 0  # lagging, never ahead: fails safe
    data = (directory / LEDGER).read_bytes()
    assert verify_bytes(data, (directory / "ledger.head").read_bytes()).status == "intact"
    w.append("tool.approved", APPROVE).result()  # the next security entry catches up
    w.close()
    assert read_head(directory).seq == 2


@pytest.mark.skipif(sys.platform != "win32", reason="Windows sharing semantics; runs on CI only")
def test_windows_replace_while_head_is_held_open(tmp_path, monkeypatch):
    """Hold ledger.head open from a second handle (Python's open() shares read and write but
    not delete), trigger an update, and check that the writer neither crashes nor corrupts."""
    monkeypatch.setattr(writer, "REPLACE_PAUSE", 0.01)
    directory = tmp_path / "l"
    w = LedgerWriter.open(directory)
    holder = open(directory / "ledger.head", "rb")  # noqa: SIM115
    try:
        w.append("tool.approved", APPROVE).result()
        assert read_head(directory).seq == 0
    finally:
        holder.close()
    data = (directory / LEDGER).read_bytes()
    assert verify_bytes(data, (directory / "ledger.head").read_bytes()).status == "intact"
    w.append("tool.approved", APPROVE).result()
    w.close()
    assert read_head(directory).seq == 2
