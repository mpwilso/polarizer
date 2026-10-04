"""Argument side files: exclusive create, matching, missing, tampered and orphaned files."""

import hashlib

import pytest
from conftest import build_chain

from polarizer import cli, sidefiles
from polarizer.sidefiles import write_args


def call(commit, i):
    return ("call.sent", {"session": "s" * 16, "tool": "p__t", "args_commit": commit,
                          "meta_dropped": [], "client_call_id": f"toolu_{i}"})  # fmt: skip


def test_side_file_format_and_commitment(tmp_path):
    commit = write_args(tmp_path, {"path": "caf\xe9", "n": 1.5})
    blob = (tmp_path / "args" / f"{commit}.bin").read_bytes()
    assert hashlib.sha256(blob).hexdigest() == commit
    assert blob[32:] == '{"path":"caf\xe9","n":1.5}'.encode()
    assert write_args(tmp_path, {"path": "caf\xe9", "n": 1.5}) != commit  # salted


def test_side_files_are_created_exclusively(tmp_path, monkeypatch):
    monkeypatch.setattr(sidefiles.secrets, "token_bytes", lambda n: b"\0" * n)
    write_args(tmp_path, {"a": 1})
    with pytest.raises(FileExistsError):
        write_args(tmp_path, {"a": 1})


def run_verify_args(directory, capsys):
    code = cli.main(["verify", "--ledger-dir", str(directory), "--args"])
    return code, capsys.readouterr().out.splitlines()


def test_matching_missing_tampered_and_orphaned(tmp_path, capsys):
    d = tmp_path / "l"
    d.mkdir()
    commits = [write_args(d, {"i": i}) for i in range(4)]
    build_chain(d, [call(c, i) for i, c in enumerate(commits)], head_at=0)
    code, out = run_verify_args(d, capsys)
    assert code == 0 and out[-1] == "args: 4 matching, 0 missing, 0 tampered, 0 orphaned"
    (d / "args" / f"{commits[1]}.bin").unlink()  # how a secret is removed
    code, out = run_verify_args(d, capsys)
    assert code == 0
    assert out[2:] == [
        "args: 3 matching, 1 missing, 0 tampered, 0 orphaned",
        f"missing   seq 2  args/{commits[1]}.bin",
    ]
    path = d / "args" / f"{commits[3]}.bin"
    path.write_bytes(path.read_bytes() + b" ")
    (d / "args" / "stray.bin").write_bytes(b"x")
    code, out = run_verify_args(d, capsys)
    assert code == 8
    assert out[2:] == [
        "args: 2 matching, 1 missing, 1 tampered, 1 orphaned",
        f"missing   seq 2  args/{commits[1]}.bin",
        f"tampered  seq 4  args/{commits[3]}.bin",
        "orphaned           args/stray.bin",
    ]


def test_args_summary_is_skipped_when_the_chain_is_not_intact(tmp_path, capsys):
    d = tmp_path / "l"
    d.mkdir()
    build_chain(d, [call(write_args(d, {}), 0)])
    with open(d / "ledger.jsonl", "ab") as f:
        f.write(b"{")
    code, out = run_verify_args(d, capsys)
    assert code == 5 and len(out) == 1 and out[0].startswith("torn tail: ")


def test_a_malformed_commit_is_missing_never_a_path(tmp_path, capsys):
    d = tmp_path / "l"
    build_chain(d, [call("../../etc/passwd", 0)], head_at=0)
    code, out = run_verify_args(d, capsys)
    assert code == 0 and out[-1] == "missing   seq 1  args/../../etc/passwd.bin"


def test_hold_side_files_are_not_orphaned(tmp_path, capsys):
    """hold.created refers to its side file as call.sent does (HOLD-SPEC.md, section 5): the
    files of a denied and an expired hold are matching, never orphaned; an allowed hold's file,
    referred to by both, is counted once, at its call.sent; a stray file is still orphaned. A
    held file deleted is missing and one altered is tampered, exit 8, as for any call."""
    d = tmp_path / "l"
    d.mkdir()
    plain, denied, expired, allowed = (write_args(d, {"i": i}) for i in range(4))
    session = "s" * 16

    def created(hold, commit):
        return ("hold.created", {"session": session, "hold": hold, "tool": "p__t",
                                 "args_commit": commit, "class": "egress", "class_from": "config",
                                 "rule": "egress", "reason": "r", "timeout_seconds": 300})  # fmt: skip

    def decided(hold, commit, decision):
        return ("hold.decided", {"hold": hold, "args_commit": commit, "decision": decision,
                                 "actor": "person", "reason": None})  # fmt: skip

    def refused(hold, reason):
        return (
            "call.refused",
            {"session": session, "tool": "p__t", "reason": reason, "hold": hold},
        )

    sent = call(allowed, 9)
    sent[1].update({"hold": "c" * 16, "allowed_by": "hold"})
    build_chain(d, [
        call(plain, 0),                                                         # seq 1
        created("a" * 16, denied), decided("a" * 16, denied, "deny"),           # seq 2, 3
        refused("a" * 16, f"hold {'a' * 16} was denied"),                       # seq 4
        created("b" * 16, expired),                                             # seq 5
        ("hold.expired", {"session": session, "hold": "b" * 16, "reason": "timeout after 300 s"}),
        refused("b" * 16, f"hold {'b' * 16} expired: timeout after 300 s"),     # seq 7
        created("c" * 16, allowed), decided("c" * 16, allowed, "allow"),        # seq 8, 9
        sent,                                                                   # seq 10
    ], head_at=0)  # fmt: skip
    code, out = run_verify_args(d, capsys)
    assert code == 0 and out[-1] == "args: 4 matching, 0 missing, 0 tampered, 0 orphaned"
    (d / "args" / "stray.bin").write_bytes(b"x")
    code, out = run_verify_args(d, capsys)
    assert code == 0
    assert out[-2:] == [
        "args: 4 matching, 0 missing, 0 tampered, 1 orphaned",
        "orphaned           args/stray.bin",
    ]
    (d / "args" / f"{denied}.bin").unlink()
    (d / "args" / f"{allowed}.bin").unlink()
    path = d / "args" / f"{expired}.bin"
    path.write_bytes(path.read_bytes() + b" ")
    code, out = run_verify_args(d, capsys)
    assert code == 8
    summary = next(i for i, line in enumerate(out) if line.startswith("args: "))
    assert out[summary:] == [
        "args: 1 matching, 2 missing, 1 tampered, 1 orphaned",
        f"missing   seq 2  args/{denied}.bin",
        f"tampered  seq 5  args/{expired}.bin",
        f"missing   seq 10  args/{allowed}.bin",
        "orphaned           args/stray.bin",
    ]
