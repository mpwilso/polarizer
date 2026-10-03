"""Every verify, verify --args and repair row in PROXY-SPEC.md, Command line: exact stdout,
compared byte for byte with tests/golden/<case>.txt, and the exit code. The temporary directory
is shown as <dir>. Set POLARIZER_UPDATE_GOLDEN=1 to rewrite the files, then read the diff."""

import os
from pathlib import Path

import pytest
from conftest import build_chain, install_fixture

from polarizer import cli, sidefiles
from polarizer.ledger import LOCK
from polarizer.lock import LedgerLock

GOLDEN = Path(__file__).parent / "golden"


def fixture(name):
    return lambda d: install_fixture(name, d)


def empty_ledger(d):
    d.mkdir(parents=True)
    (d / "ledger.jsonl").write_bytes(b"")
    return d


def with_side_files(tampered):
    def setup(d, monkeypatch):
        d.mkdir(parents=True)
        salts = iter(bytes([i]) * 32 for i in range(1, 10))
        monkeypatch.setattr(sidefiles.secrets, "token_bytes", lambda n: next(salts))
        commits = [sidefiles.write_args(d, {"path": f"/tmp/f{i}"}) for i in range(4)]
        call = {"session": "0" * 16, "tool": "probe__wait", "meta_dropped": []}
        specs = [("session.started", {"session": "0" * 16})]
        specs += [
            ("call.sent", {**call, "args_commit": c, "client_call_id": None}) for c in commits
        ]
        build_chain(d, specs, head_at=0)
        (d / "args" / f"{commits[1]}.bin").unlink()
        (d / "args" / "left-behind.bin").write_bytes(b"orphan")
        if tampered:
            path = d / "args" / f"{commits[2]}.bin"
            path.write_bytes(path.read_bytes() + b" ")
        return d

    return setup


VERIFY = {
    # intact
    "verify_intact": (fixture("valid/session"), 0),
    "verify_intact_head_missing": (fixture("valid/no_head"), 0),
    "verify_intact_v0": (fixture("valid/v0-parallax"), 0),
    # tampered
    "verify_tampered_hash": (fixture("broken/edit_value"), 1),
    "verify_tampered_seq": (fixture("broken/delete_middle_line"), 1),
    "verify_tampered_prev": (fixture("broken/bad_prev"), 1),
    "verify_tampered_genesis_prev": (fixture("broken/bad_genesis_prev"), 1),
    "verify_tampered_head": (fixture("broken/head_hash_mismatch"), 1),
    "verify_tampered_v0": (fixture("broken/edit_v0_entry"), 1),
    # invalid lines, one per rule
    "verify_invalid_utf8": (fixture("broken/bad_utf8"), 3),
    "verify_invalid_json": (fixture("broken/garbage_line"), 3),
    "verify_invalid_not_object": (fixture("broken/not_object"), 3),
    "verify_invalid_mixed": (fixture("broken/mix_v0_v1"), 3),
    "verify_invalid_mixed_in_v0": (fixture("broken/v0_then_v1"), 3),
    "verify_invalid_oversize": (fixture("broken/oversize_line"), 3),
    "verify_invalid_unknown_key": (fixture("broken/extra_top_level_key"), 3),
    "verify_invalid_missing_key": (fixture("broken/missing_key"), 3),
    "verify_invalid_wrong_type": (fixture("broken/wrong_type"), 3),
    "verify_invalid_v": (fixture("broken/v_not_1"), 3),
    "verify_invalid_float": (fixture("broken/insert_float"), 3),
    "verify_invalid_integer": (fixture("broken/big_integer"), 3),
    "verify_invalid_non_ascii_key": (fixture("broken/non_ascii_key"), 3),
    "verify_invalid_lone_surrogate": (fixture("broken/lone_surrogate"), 3),
    "verify_invalid_first_not_genesis": (fixture("broken/delete_first_line"), 3),
    "verify_invalid_second_genesis": (fixture("broken/second_genesis"), 3),
    "verify_invalid_chain_id": (fixture("broken/bad_chain_id"), 3),
    # invalid head
    "verify_invalid_head_form": (fixture("broken/head_not_canonical"), 3),
    "verify_invalid_head_chain": (fixture("broken/head_wrong_chain"), 3),
    "verify_invalid_head_v0": (fixture("broken/v0_with_head"), 3),
    # not canonical
    "verify_not_canonical": (fixture("broken/reorder_keys"), 4),
    "verify_not_canonical_v0": (fixture("broken/v0_unsorted_line"), 4),
    # torn tail
    "verify_torn_tail": (fixture("broken/tear_last_line"), 5),
    "verify_torn_tail_genesis": (fixture("broken/torn_genesis"), 5),
    "verify_torn_tail_v0": (fixture("broken/v0_torn"), 5),
    # truncated
    "verify_truncated": (fixture("broken/truncated"), 6),
    "verify_truncated_no_entries": (fixture("broken/torn_genesis_with_head"), 6),
    # no ledger
    "verify_no_ledger": (lambda d: d, 2),
    "verify_empty_ledger": (empty_ledger, 2),
}

REPAIR = {
    "repair_repaired": (fixture("broken/tear_last_line"), 0),
    "repair_repaired_genesis": (fixture("broken/torn_genesis"), 0),
    "repair_intact": (fixture("valid/session"), 0),
    "repair_refused_line": (fixture("broken/edit_value"), 1),
    "repair_refused_head": (fixture("broken/head_hash_mismatch"), 1),
    "repair_refused_invalid_head": (fixture("broken/head_wrong_chain"), 3),
    "repair_refused_truncated": (fixture("broken/torn_tail_and_truncated"), 6),
    "repair_refused_v0": (fixture("broken/v0_torn"), 3),
    "repair_no_ledger": (lambda d: d, 2),
}


def check(case, out, code, want_code, tmp_path):
    text = out.replace(str(tmp_path / "ledger"), "<dir>")
    path = GOLDEN / f"{case}.txt"
    if os.environ.get("POLARIZER_UPDATE_GOLDEN"):
        path.write_text(text, encoding="utf-8", newline="\n")
    assert text == path.read_text(encoding="utf-8")
    assert code == want_code


@pytest.mark.parametrize("case", sorted(VERIFY))
def test_verify(case, tmp_path, capsys):
    setup, want = VERIFY[case]
    directory = setup(tmp_path / "ledger")
    code = cli.main(["verify", "--ledger-dir", str(directory)])
    out, err = capsys.readouterr()
    assert err == ""
    check(case, out, code, want, tmp_path)


@pytest.mark.parametrize("case", sorted(REPAIR))
def test_repair(case, tmp_path, capsys, fake_home):
    setup, want = REPAIR[case]
    directory = setup(tmp_path / "ledger")
    code = cli.main(["repair", "--ledger-dir", str(directory)])
    out, err = capsys.readouterr()
    assert err == ""
    check(case, out, code, want, tmp_path)


@pytest.mark.parametrize(
    "case, tampered, want", [("verify_args", False, 0), ("verify_args_tampered", True, 8)]
)
def test_verify_args(case, tampered, want, tmp_path, capsys, monkeypatch):
    directory = with_side_files(tampered)(tmp_path / "ledger", monkeypatch)
    code = cli.main(["verify", "--ledger-dir", str(directory), "--args"])
    check(case, capsys.readouterr().out, code, want, tmp_path)


def test_verify_args_when_chain_is_not_intact(tmp_path, capsys):
    directory = install_fixture("broken/edit_value", tmp_path / "ledger")
    code = cli.main(["verify", "--ledger-dir", str(directory), "--args"])
    check("verify_args_chain_broken", capsys.readouterr().out, code, 1, tmp_path)


@pytest.mark.parametrize("command", ["verify", "repair"])
def test_locked(command, tmp_path, capsys):
    directory = install_fixture("valid/session", tmp_path / "ledger")
    lock = LedgerLock.create(directory / LOCK)
    assert lock.acquire(0)
    try:
        code = cli.main([command, "--ledger-dir", str(directory)])
    finally:
        lock.close()
    check(f"{command}_locked", capsys.readouterr().out, code, 7, tmp_path)


@pytest.mark.parametrize(
    "argv, line",
    [
        (
            ["verify"],
            "polarizer: verify needs exactly one of --config <absolute path> or --ledger-dir <absolute path>",
        ),
        (
            ["repair"],
            "polarizer: repair needs exactly one of --config <absolute path> or --ledger-dir <absolute path>",
        ),
        (
            ["verify", "--config", "/a.toml", "--ledger-dir", "/b"],
            "polarizer: verify needs exactly one of --config <absolute path> or --ledger-dir <absolute path>",
        ),
        (
            ["verify", "--ledger-dir", "relative/dir"],
            "polarizer: --ledger-dir must be an absolute path, got relative/dir",
        ),
        (
            ["repair", "--config", "polarizer.toml"],
            "polarizer: --config must be an absolute path, got polarizer.toml",
        ),
        (
            ["verify", "--ledger-dir", "~/x"],
            "polarizer: --ledger-dir must be an absolute path, got ~/x",
        ),
        (["verify", "--bogus"], "polarizer: unrecognized arguments: --bogus"),
        ([], "polarizer: the following arguments are required: command"),
        (
            ["serve"],
            "polarizer: argument command: invalid choice: 'serve' (choose from ",
        ),
    ],
)
def test_usage_errors_are_one_line_on_stderr(argv, line, capsys):
    assert cli.main(argv) == 2
    out, err = capsys.readouterr()
    assert out == "" and err.count("\n") == 1 and err.endswith("\n")
    if line.endswith("(choose from "):  # the list's quoting differs between Python versions
        assert err.startswith(line)
    else:
        assert err == line + "\n"
