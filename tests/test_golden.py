"""Every verify, verify --args and repair row in PROXY-SPEC.md, Command line: exact stdout,
compared byte for byte with tests/golden/<case>.txt, and the exit code. The temporary directory
is shown as <dir>. Set POLARIZER_UPDATE_GOLDEN=1 to rewrite the files, then read the diff."""

import json
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
            "polarizer: serve needs --config <absolute path to polarizer.toml>",
        ),
        (
            ["serve", "--config", "polarizer.toml"],
            "polarizer: --config must be an absolute path, got polarizer.toml",
        ),
        (["serve", "--ledger-dir", "/x"], "polarizer: unrecognized arguments: --ledger-dir /x"),
        (
            ["bogus"],
            "polarizer: argument command: invalid choice: 'bogus' (choose from ",
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


# Pin commands (docs/PIN-SPEC.md, section 7) --------------------------------------------------

from helpers import pinledger  # noqa: E402


def pin_main(argv, terminal=False):
    """cli.main for approve and reject: --allow-no-terminal unless the case is about it."""
    return cli.main(argv if terminal else [*argv, "--allow-no-terminal"])


PENDING = {
    "pending_nothing": (pinledger.quiet, [], 0),
    "pending_mixed": (pinledger.mixed, [], 0),
    "pending_one_upstream": (pinledger.mixed, ["--upstream", "probe"], 0),
    "pending_capped": (pinledger.capped, [], 0),
    "pending_several_waiting": (pinledger.several, [], 0),
    "pending_tampered": (fixture("broken/edit_value"), [], 1),
    "pending_invalid": (fixture("broken/insert_float"), [], 3),
    "pending_torn_tail": (fixture("broken/tear_last_line"), [], 5),
    "pending_truncated": (fixture("broken/truncated"), [], 6),
    "pending_no_ledger": (lambda d: d, [], 2),
}


@pytest.mark.parametrize("case", sorted(PENDING))
def test_pending(case, tmp_path, capsys):
    setup, extra, want = PENDING[case]
    directory = setup(tmp_path / "ledger")
    code = cli.main(["pending", "--ledger-dir", str(directory), *extra])
    out, err = capsys.readouterr()
    assert err == ""
    check(case, out, code, want, tmp_path)


def test_pending_locked(tmp_path, capsys):
    directory = pinledger.quiet(tmp_path / "ledger")
    lock = LedgerLock.create(directory / LOCK)
    assert lock.acquire(0)
    try:
        code = cli.main(["pending", "--ledger-dir", str(directory)])
    finally:
        lock.close()
    check("pending_locked", capsys.readouterr().out, code, 7, tmp_path)


def _group_id(directory, capsys, upstream=()):
    cli.main(["pending", "--ledger-dir", str(directory), *upstream])
    line = capsys.readouterr().out.splitlines()[-1]
    assert line.startswith("group ")
    return line.split()[1]


def test_approve_one(tmp_path, capsys, fake_home):
    directory = pinledger.mixed(tmp_path / "ledger")
    h = pinledger.hashed("note")
    code = pin_main(["approve", "--ledger-dir", str(directory), "probe", "note", h])
    out, err = capsys.readouterr()
    assert err == ""
    check("approve_one", out, code, 0, tmp_path)


def test_approve_already(tmp_path, capsys, fake_home):
    directory = pinledger.mixed(tmp_path / "ledger")
    h = pinledger.hashed("add")
    code = pin_main(["approve", "--ledger-dir", str(directory), "every", "add", h])
    out, err = capsys.readouterr()
    assert err == ""
    check("approve_already", out, code, 0, tmp_path)


def test_approve_group(tmp_path, capsys, fake_home):
    directory = pinledger.mixed(tmp_path / "ledger")
    group_id = _group_id(directory, capsys)
    code = pin_main(["approve", "--ledger-dir", str(directory), "--group", group_id])
    out, err = capsys.readouterr()
    assert err == ""
    check("approve_group", out, code, 0, tmp_path)


def _refusal_setups():
    note = pinledger.hashed("note")
    rich = pinledger.hashed("rich")
    return {
        "bad_hash": (pinledger.mixed, ["probe", "note", "ABC"], 2),
        "not_seen": (pinledger.mixed, ["probe", "note", pinledger.hashed("echo_v2")], 2),
        "stored_copy_altered": (pinledger.mixed, ["probe", "rich", rich], 2),
        "group_mismatch": (pinledger.mixed, ["--group", "0" * 64], 2),
        "broken_ledger": (fixture("broken/edit_value"), ["probe", "note", note], 1),
        "no_terminal": (pinledger.mixed, ["probe", "note", note], 2),
    }


@pytest.mark.parametrize("case", sorted(_refusal_setups()))
def test_approve_refused(case, tmp_path, capsys, fake_home, monkeypatch):
    setup, extra, want = _refusal_setups()[case]
    directory = setup(tmp_path / "ledger")
    before = (directory / "ledger.jsonl").read_bytes()
    monkeypatch.setattr(cli, "_stdin_is_terminal", lambda: False)
    argv = ["approve", "--ledger-dir", str(directory), *extra]
    code = pin_main(argv, terminal=case == "no_terminal")
    out, err = capsys.readouterr()
    assert out == ""
    assert err.count("\n") == 1
    check(f"approve_refused_{case}", err, code, want, tmp_path)
    assert (directory / "ledger.jsonl").read_bytes() == before  # nothing written


def test_reject_one(tmp_path, capsys, fake_home):
    directory = pinledger.mixed(tmp_path / "ledger")
    h = pinledger.hashed("echo_v2")
    argv = ["reject", "--ledger-dir", str(directory), "every", "echo", h, "--reason", "exfil"]
    code = pin_main(argv)
    out, err = capsys.readouterr()
    assert err == ""
    check("reject_one", out, code, 0, tmp_path)


def test_reject_already(tmp_path, capsys, fake_home):
    directory = pinledger.mixed(tmp_path / "ledger")
    h = pinledger.hashed("fail_v1")
    argv = ["reject", "--ledger-dir", str(directory), "probe", "fail", h, "--reason", "again"]
    code = pin_main(argv)
    out, err = capsys.readouterr()
    assert err == ""
    check("reject_already", out, code, 0, tmp_path)


@pytest.mark.parametrize(
    "case, extra, terminal",
    [
        ("reject_no_reason", [], False),
        ("reject_no_reason", ["--reason", " \t\n "], False),
        ("reject_refused_no_terminal", ["--reason", "why"], True),
    ],
)
def test_reject_refused(case, extra, terminal, tmp_path, capsys, fake_home, monkeypatch):
    directory = pinledger.mixed(tmp_path / "ledger")
    before = (directory / "ledger.jsonl").read_bytes()
    monkeypatch.setattr(cli, "_stdin_is_terminal", lambda: False)
    h = pinledger.hashed("echo_v2")
    argv = ["reject", "--ledger-dir", str(directory), "every", "echo", h, *extra]
    code = pin_main(argv, terminal=terminal)
    out, err = capsys.readouterr()
    assert out == ""
    check(case, err, code, 2, tmp_path)
    assert (directory / "ledger.jsonl").read_bytes() == before


PIN_USAGE = [
    ["pending"],
    ["pending", "--config", "/a.toml", "--ledger-dir", "/b"],
    ["pending", "--ledger-dir", "relative"],
    ["pending", "--ledger-dir", "/x", "extra"],
    ["approve", "--allow-no-terminal"],
    ["approve", "--ledger-dir", "/x", "--allow-no-terminal"],
    ["approve", "--ledger-dir", "/x", "p", "t", "--allow-no-terminal"],
    ["approve", "--ledger-dir", "/x", "p", "t", "h", "--group", "g", "--allow-no-terminal"],
    ["approve", "--ledger-dir", "/x", "p", "t", "h", "--upstream", "p", "--allow-no-terminal"],
    ["approve", "--ledger-dir", "/x", "--group", "g"],
    ["reject", "--ledger-dir", "/x", "p", "t", "--reason", "r", "--allow-no-terminal"],
    ["reject", "--ledger-dir", "/x", "p", "t", "h", "--allow-no-terminal"],
    ["reject", "--ledger-dir", "/x", "p", "t", "h", "--reason", "r"],
    ["reject", "--config", "polarizer.toml", "p", "t", "h", "--reason", "r"],
    ["approve", "--ledger-dir", "/x", "--bogus"],
]


def test_usage_pins(tmp_path, capsys, monkeypatch):
    """Each usage error: one line on stderr, nothing on stdout, exit 2."""
    monkeypatch.setattr(cli, "_stdin_is_terminal", lambda: False)
    lines = []
    for argv in PIN_USAGE:
        assert cli.main(argv) == 2, argv
        out, err = capsys.readouterr()
        assert out == "" and err.count("\n") == 1, (argv, err)
        lines.append(err)
    check("usage_pins", "".join(lines), 2, 2, tmp_path)


# Hold commands (docs/HOLD-SPEC.md, section 8) -------------------------------------------------

from helpers import holdledger as hl  # noqa: E402


@pytest.fixture
def held_clock(monkeypatch):
    """holds' clock, fixed at holdledger.NOW."""
    monkeypatch.setattr(cli, "_now", lambda: hl.NOW)


@pytest.fixture
def running_session():
    """Holds the running session's lock, as its serve would, for the length of a test."""
    held = []

    def take(directory):
        held.append(hl.hold_running(directory))
        return directory

    yield take
    for lock in held:
        lock.close()


HOLDS = {
    "holds_nothing": (hl.nothing, 0),
    "holds_mixed": (hl.mixed, 0),
    "holds_state_unknown": (hl.state_unknown, 0),
    "holds_side_file_missing": (hl.side_file_problem("missing"), 0),
    "holds_side_file_altered": (hl.side_file_problem("altered"), 0),
    "holds_side_file_not_json": (hl.side_file_problem("not_json"), 0),
    "holds_tampered": (fixture("broken/edit_value"), 1),
    "holds_invalid": (fixture("broken/insert_float"), 3),
    "holds_torn_tail": (fixture("broken/tear_last_line"), 5),
    "holds_truncated": (fixture("broken/truncated"), 6),
    "holds_no_ledger": (lambda d: d, 2),
}


@pytest.mark.parametrize("case", sorted(HOLDS))
def test_holds(case, tmp_path, capsys, held_clock, running_session):
    setup, want = HOLDS[case]
    directory = setup(tmp_path / "ledger")
    if (directory / "sessions" / f"{hl.RUNNING}.lock").exists():
        running_session(directory)
    code = cli.main(["holds", "--ledger-dir", str(directory)])
    out, err = capsys.readouterr()
    assert err == ""
    check(case, out, code, want, tmp_path)


def test_holds_locked(tmp_path, capsys):
    directory = hl.nothing(tmp_path / "ledger")
    lock = LedgerLock.create(directory / LOCK)
    assert lock.acquire(0)
    try:
        code = cli.main(["holds", "--ledger-dir", str(directory)])
    finally:
        lock.close()
    check("holds_locked", capsys.readouterr().out, code, 7, tmp_path)


def hold_main(argv, terminal=False):
    """cli.main for allow and deny: --allow-no-terminal unless the case is about it."""
    return cli.main(argv if terminal else [*argv, "--allow-no-terminal"])


def test_allow_one(tmp_path, capsys, held_clock, running_session, fake_home):
    directory = running_session(hl.mixed(tmp_path / "ledger"))
    code = hold_main(["allow", "--ledger-dir", str(directory), hl.WRITE_PATTERN])
    out, err = capsys.readouterr()
    assert err == ""
    check("allow_one", out, code, 0, tmp_path)


def test_allow_ended_session(tmp_path, capsys, held_clock, running_session, fake_home):
    directory = running_session(hl.mixed(tmp_path / "ledger"))
    code = hold_main(["allow", "--ledger-dir", str(directory), hl.FROM_ANNOTATIONS])
    out, err = capsys.readouterr()
    assert err == (
        "polarizer: warning: the session that held this call has ended; "
        "no process will act on this decision\n"
    )
    check("allow_ended_session", out, code, 0, tmp_path)


def _hold_refusals():
    return {
        "bad_id": (hl.endings, ["ABC"], 2),
        "no_such_hold": (hl.endings, ["0123456789abcdef"], 2),
        "already_decided": (hl.endings, [hl.DENIED], 2),
        "expired": (hl.endings, [hl.EXPIRED], 2),
        "abandoned": (hl.endings, [hl.ABANDONED], 2),
        "side_file_altered": (hl.endings, [hl.ALTERED], 2),
        "broken_ledger": (fixture("broken/edit_value"), [hl.WRITE_PATTERN], 1),
        "no_terminal": (hl.endings, [hl.WRITE_PATTERN], 2),
    }


@pytest.mark.parametrize("case", sorted(_hold_refusals()))
def test_allow_refused(case, tmp_path, capsys, fake_home, monkeypatch, held_clock):
    setup, extra, want = _hold_refusals()[case]
    directory = setup(tmp_path / "ledger")
    before = (directory / "ledger.jsonl").read_bytes()
    monkeypatch.setattr(cli, "_stdin_is_terminal", lambda: False)
    code = hold_main(
        ["allow", "--ledger-dir", str(directory), *extra], terminal=case == "no_terminal"
    )
    out, err = capsys.readouterr()
    assert out == ""
    assert err.count("\n") == 1
    check(f"allow_refused_{case}", err, code, want, tmp_path)
    assert (directory / "ledger.jsonl").read_bytes() == before  # nothing written


@pytest.mark.parametrize(
    "case, extra",
    [("deny_one", []), ("deny_with_reason", ["--reason", "  not\n  now  "])],
)
def test_deny(case, extra, tmp_path, capsys, held_clock, running_session, fake_home):
    directory = running_session(hl.mixed(tmp_path / "ledger"))
    code = hold_main(["deny", "--ledger-dir", str(directory), hl.UNCLASSIFIED, *extra])
    out, err = capsys.readouterr()
    assert err == ""
    check(case, out, code, 0, tmp_path)
    decided = [
        line
        for line in (directory / "ledger.jsonl").read_bytes().splitlines()
        if b"hold.decided" in line
    ]
    want = "not now" if extra else None
    last = json.loads(decided[-1])["data"]  # the mixed ledger already holds one deny
    assert (last["hold"], last["decision"], last["reason"]) == (hl.UNCLASSIFIED, "deny", want)


@pytest.mark.parametrize(
    "case, extra, terminal",
    [
        ("deny_refused_empty_reason", [hl.WRITE_PATTERN, "--reason", " \t\n "], False),
        ("deny_refused_already_decided", [hl.DENIED], False),
        ("deny_refused_no_terminal", [hl.WRITE_PATTERN], True),
    ],
)
def test_deny_refused(case, extra, terminal, tmp_path, capsys, fake_home, monkeypatch, held_clock):
    directory = hl.endings(tmp_path / "ledger")
    before = (directory / "ledger.jsonl").read_bytes()
    monkeypatch.setattr(cli, "_stdin_is_terminal", lambda: False)
    code = hold_main(["deny", "--ledger-dir", str(directory), *extra], terminal=terminal)
    out, err = capsys.readouterr()
    assert out == ""
    check(case, err, code, 2, tmp_path)
    assert (directory / "ledger.jsonl").read_bytes() == before


HOLD_USAGE = [
    ["holds"],
    ["holds", "--config", "/a.toml", "--ledger-dir", "/b"],
    ["holds", "--ledger-dir", "relative"],
    ["holds", "--ledger-dir", "/x", "extra"],
    ["allow", "--ledger-dir", "/x", "--allow-no-terminal"],
    ["allow", "--ledger-dir", "/x", "0123456789abcdef", "0123456789abcdef", "--allow-no-terminal"],
    ["allow", "--ledger-dir", "/x", "0123456789abcdef", "--reason", "r", "--allow-no-terminal"],
    ["deny", "--ledger-dir", "/x", "--allow-no-terminal"],
    ["deny", "--ledger-dir", "/x", "0123456789abcdef", "--reason", "  ", "--allow-no-terminal"],
    ["deny", "--ledger-dir", "/x", "0123456789abcdef"],
    ["allow", "--config", "polarizer.toml", "0123456789abcdef"],
    ["holds", "--ledger-dir", "/x", "--no-holds"],
    ["verify", "--ledger-dir", "/x", "--no-holds"],
    ["allow", "--ledger-dir", "/x", "0123456789abcdef", "--no-holds", "--allow-no-terminal"],
    ["serve", "--no-holds"],
]


def test_usage_holds(tmp_path, capsys, monkeypatch):
    """Each usage error: one line on stderr, nothing on stdout, exit 2."""
    monkeypatch.setattr(cli, "_stdin_is_terminal", lambda: False)
    lines = []
    for argv in HOLD_USAGE:
        assert cli.main(argv) == 2, argv
        out, err = capsys.readouterr()
        assert out == "" and err.count("\n") == 1, (argv, err)
        lines.append(err)
    check("usage_holds", "".join(lines), 2, 2, tmp_path)
