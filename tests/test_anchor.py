"""Checkpoints and anchors (docs/EXPORT-SPEC.md, section 4): polarizer checkpoint's trailer,
its key and its refusals; verify --anchor with a trailer, a bare value and a git ref, each
result line and exit code against golden files; both anchor checks agreeing on every
conformance case; and neither verify nor checkpoint writing anything but the key. Set
POLARIZER_UPDATE_GOLDEN=1 to rewrite the golden files, then read the diff."""

import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest
import reference_anchor
from conftest import CONFORMANCE, expected, fixture_paths, install_fixture, mask_paths

from polarizer import anchor, cli, ledger

GOLDEN = Path(__file__).parent / "golden"
UPDATE = os.environ.get("POLARIZER_UPDATE_GOLDEN") == "1"
KEY = CONFORMANCE / "anchors" / "checkpoint.key"
CASES = expected()["anchors"]
POSIX = pytest.mark.skipif(sys.platform == "win32", reason="POSIX file modes")


def golden(name: str, text: str) -> None:
    path = GOLDEN / name
    if UPDATE:
        path.write_text(text, encoding="utf-8", newline="\n")
    assert text == path.read_text(encoding="utf-8")


def with_key(name, directory):
    directory = install_fixture(name, directory)
    shutil.copyfile(KEY, directory / anchor.KEY_FILE)
    return directory


def run(capsys, *argv):
    code = cli.main(list(argv))
    out, err = capsys.readouterr()
    return code, out, err


def hash_at(path, seq):
    """The verified chain's hash at seq, read the way cli.verify reads it."""
    found = {}

    def on_entry(entry):
        if entry["seq"] == seq:
            found["hash"] = entry["hash"]

    result = ledger.verify_bytes(path.read_bytes(), None, on_entry)
    assert result.status == "intact"
    return result, found.get("hash")


@pytest.mark.parametrize("case", sorted(CASES))
def test_both_anchor_checks_agree(case):
    want = CASES[case]
    path, _ = fixture_paths(want["ledger"])
    got_ref = reference_anchor.check(path, KEY, want["trailer"])
    cp = anchor.parse_argument(want["trailer"])
    assert cp is not None
    result, at = hash_at(path, cp.seq)
    key = bytes.fromhex(KEY.read_text(encoding="ascii").strip())
    outcome = anchor.check(cp, key, result.state.chain_id, result.seq, at)
    assert got_ref == {"result": want["result"], "seq": want["seq"]}
    assert {"result": outcome.result, "seq": cp.seq} == got_ref


def test_every_result_is_pinned():
    results = {c["result"] for c in CASES.values()}
    assert results == {"extends", "rewound", "rewritten", "other chain", "bad signature"}


def test_reference_anchor_is_small_and_standalone():
    text = (CONFORMANCE / "reference_anchor.py").read_text(encoding="utf-8")
    assert len(text.splitlines()) < 60
    imports = {
        line.split()[1] for line in text.splitlines() if line.startswith(("import ", "from "))
    }
    assert imports == {"hashlib", "hmac", "json", "re", "sys"}


def test_signature_is_the_spec_formula():
    key = bytes.fromhex(KEY.read_text(encoding="ascii").strip())
    import hashlib
    import hmac

    message = b"POLARIZER-CHECKPOINT/1\n" + b"%s:%d:%s" % (b"a" * 32, 7, b"b" * 64)
    want = hmac.new(key, message, hashlib.sha256).hexdigest()
    assert (
        anchor.sign(key, "a" * 32, 7, "b" * 64)
        == want
        == reference_anchor.sign(key, "a" * 32, 7, "b" * 64)
    )


VERIFY = {
    "extends": ("extends", 0),
    "at_head": ("at_head", 0),
    "rewound": ("rewound", 9),
    "rewritten": ("rewritten", 9),
    "other_chain": ("other_chain", 9),
    "bad_signature": ("bad_signature", 10),
    "other_key": ("other_key", 10),
}


@pytest.mark.parametrize("name", sorted(VERIFY))
def test_verify_anchor_golden(tmp_path, capsys, name):
    case, want = VERIFY[name]
    directory = with_key(CASES[case]["ledger"], tmp_path / "ledger")
    trailer = CASES[case]["trailer"]
    code, out, err = run(capsys, "verify", "--ledger-dir", str(directory), "--anchor", trailer)
    assert err == ""
    assert code == want
    golden(f"verify_anchor_{name}.txt", out)


def test_bare_value_and_trailer_read_the_same(tmp_path, capsys):
    directory = with_key("valid/session", tmp_path / "ledger")
    trailer = CASES["extends"]["trailer"]
    value = trailer.split(":", 1)[1].strip()
    first = run(capsys, "verify", "--ledger-dir", str(directory), "--anchor", trailer)
    second = run(capsys, "verify", "--ledger-dir", str(directory), "--anchor", value)
    assert first == second and first[0] == 0


def test_key_problems(tmp_path, capsys):
    directory = install_fixture("valid/session", tmp_path / "ledger")
    trailer = CASES["extends"]["trailer"]
    code, out, _ = run(capsys, "verify", "--ledger-dir", str(directory), "--anchor", trailer)
    assert code == 10
    golden("verify_anchor_no_key.txt", mask_paths(out, dir=directory))
    (directory / anchor.KEY_FILE).write_bytes(b"not a key\n")
    code, out, _ = run(capsys, "verify", "--ledger-dir", str(directory), "--anchor", trailer)
    assert code == 10
    golden("verify_anchor_bad_key.txt", mask_paths(out, dir=directory))


def test_not_intact_is_not_checked(tmp_path, capsys):
    directory = with_key("broken/edit_value", tmp_path / "ledger")
    trailer = CASES["extends"]["trailer"]
    code, out, _ = run(capsys, "verify", "--ledger-dir", str(directory), "--anchor", trailer)
    assert code == 1
    golden("verify_anchor_not_intact.txt", out)


def test_rewound_with_head_still_later_is_truncated(tmp_path, capsys):
    """A ledger rewound while its ledger.head still names a later seq is truncated, before the
    anchor is read."""
    directory = with_key("broken/truncated", tmp_path / "ledger")
    trailer = CASES["extends"]["trailer"]
    code, out, _ = run(capsys, "verify", "--ledger-dir", str(directory), "--anchor", trailer)
    assert code == 6
    assert out.endswith("anchor: not checked; the ledger is not intact\n")


def test_args_and_anchor_exit_code(tmp_path, capsys):
    """With --args, the exit code is the first non-zero of the chain's, the side files' and
    the anchor's."""
    directory = with_key("valid/session", tmp_path / "ledger")
    trailer = CASES["rewound"]["trailer"]
    code, out, _ = run(
        capsys, "verify", "--ledger-dir", str(directory), "--args", "--anchor", trailer
    )
    # valid/session's side files are all missing, which does not fail --args.
    assert code == 0
    assert out.splitlines()[-1].startswith("anchor: intact: the ledger extends")
    (directory / "args").mkdir()
    commit = ledger.verify_bytes((directory / "ledger.jsonl").read_bytes(), None).state
    first = commit.calls_sent[0][1]
    (directory / "args" / f"{first}.bin").write_bytes(b"tampered")
    rewound = with_key("anchors/rewound", tmp_path / "rewound")
    code, _, _ = run(capsys, "verify", "--ledger-dir", str(rewound), "--args", "--anchor", trailer)
    assert code == 9
    code, _, _ = run(
        capsys, "verify", "--ledger-dir", str(directory), "--args", "--anchor", trailer
    )
    assert code == 8


@pytest.mark.parametrize(
    "argument, line",
    [
        ("Polarizer-Checkpoint: abc", "polarizer: --anchor: not a checkpoint; expected "
         "Polarizer-Checkpoint: <chain>:<size>:<head>:<sig>"),
        ("-x", "polarizer: --anchor -x: not a git ref Polarizer will pass to git"),
        ("a\nb", "polarizer: --anchor a\\x0ab: not a git ref Polarizer will pass to git"),
    ],
)  # fmt: skip
def test_bad_arguments(tmp_path, capsys, argument, line):
    directory = with_key("valid/session", tmp_path / "ledger")
    code, out, err = run(capsys, "verify", "--ledger-dir", str(directory), f"--anchor={argument}")
    assert (code, out, err) == (2, "", line + "\n")


def test_size_zero_and_leading_zero_are_not_checkpoints():
    chain, head, sig = "a" * 32, "b" * 64, "c" * 64
    assert anchor.parse_argument(f"{chain}:0:{head}:{sig}") is None
    assert anchor.parse_argument(f"{chain}:07:{head}:{sig}") is None
    assert anchor.parse_argument(f"Polarizer-Checkpoint:{chain}:7:{head}:{sig}").size == 7
    assert anchor.parse_argument(f"{chain.upper()}:7:{head}:{sig}") is None


def git(repo, *args):
    done = subprocess.run(
        ["git", "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
         "-c", "commit.gpgsign=false", *args],
        cwd=repo, capture_output=True, text=True, timeout=60,
    )  # fmt: skip
    assert done.returncode == 0, done.stderr
    return done.stdout.strip()


def make_repo(tmp_path, messages):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    for message in messages:
        git(repo, "commit", "-q", "--allow-empty", "-m", message)
    return repo


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
def test_anchor_from_a_git_ref(tmp_path, capsys, monkeypatch):
    directory = with_key("valid/session", tmp_path / "ledger")
    older = CASES["at_head"]["trailer"]
    newer = CASES["extends"]["trailer"]
    repo = make_repo(
        tmp_path,
        [
            f"First\n\n{older}",
            f"Anchor the ledger\n\nPolarizer-Checkpoint: not-a-value\n{newer}",
            "No trailer here",
        ],
    )
    anchored = git(repo, "rev-parse", "HEAD~1")
    monkeypatch.chdir(repo)
    code, out, err = run(capsys, "verify", "--ledger-dir", str(directory), "--anchor", "HEAD")
    assert (code, err) == (0, "")
    lines = out.splitlines()
    assert lines[-2].endswith(f", from commit {anchored}")
    assert lines[-2].startswith("anchor: checkpoint of chain ")
    assert lines[-1] == "anchor: intact: the ledger extends the checkpoint by 4 entries"
    code, out, _ = run(capsys, "verify", "--ledger-dir", str(directory), "--anchor", "HEAD~2")
    assert code == 0 and out.splitlines()[-1].endswith("by 0 entries")


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
def test_git_refusals(tmp_path, capsys, monkeypatch):
    directory = with_key("valid/session", tmp_path / "ledger")
    repo = make_repo(tmp_path, ["one", "two"])
    monkeypatch.chdir(repo)
    code, out, err = run(capsys, "verify", "--ledger-dir", str(directory), "--anchor", "HEAD")
    assert (code, out) == (2, "")
    assert err == (
        "polarizer: --anchor HEAD: no Polarizer-Checkpoint trailer in the 2 commits from HEAD\n"
    )
    code, out, err = run(capsys, "verify", "--ledger-dir", str(directory), "--anchor", "nope")
    assert (code, out) == (2, "")
    assert err.startswith("polarizer: --anchor nope: git log failed: fatal: ")
    monkeypatch.setattr(anchor, "GIT", "polarizer-no-such-git")
    code, out, err = run(capsys, "verify", "--ledger-dir", str(directory), "--anchor", "HEAD")
    assert (code, out) == (2, "")
    assert err.startswith("polarizer: --anchor HEAD: git is not available: ")


def test_checkpoint_makes_a_trailer_that_verifies(tmp_path, capsys):
    directory = install_fixture("valid/holds", tmp_path / "ledger")
    code, out, err = run(capsys, "checkpoint", "--ledger-dir", str(directory))
    key_path = directory / anchor.KEY_FILE
    assert code == 0
    assert err == (
        f"polarizer: created the checkpoint key {key_path}; keep a copy: verify --anchor needs it\n"
    )
    entries = (directory / "ledger.jsonl").read_bytes().splitlines()
    result = ledger.verify_bytes((directory / "ledger.jsonl").read_bytes(), None)
    key = bytes.fromhex(key_path.read_text(encoding="ascii"))
    sig = reference_anchor.sign(key, result.state.chain_id, len(entries), result.state.last_hash)
    assert out == (
        f"Polarizer-Checkpoint: {result.state.chain_id}:{len(entries)}:"
        f"{result.state.last_hash}:{sig}\n"
    )
    assert key_path.read_bytes()[64:] == b"\n" and len(key_path.read_bytes()) == 65
    if sys.platform != "win32":
        assert stat.S_IMODE(key_path.stat().st_mode) == 0o600
    again = run(capsys, "checkpoint", "--ledger-dir", str(directory))
    assert again == (0, out, "")
    code, verified, _ = run(
        capsys, "verify", "--ledger-dir", str(directory), "--anchor", out.strip()
    )
    assert code == 0
    assert verified.splitlines()[-1] == (
        "anchor: intact: the ledger extends the checkpoint by 0 entries"
    )


def test_checkpoint_refusals(tmp_path, capsys):
    broken = install_fixture("broken/edit_value", tmp_path / "broken")
    code, out, err = run(capsys, "checkpoint", "--ledger-dir", str(broken))
    assert (code, out) == (1, "")
    assert err == (
        "polarizer: tampered: line 7 (seq 6): hash does not match the entry; a checkpoint "
        "needs an intact ledger\n"
    )
    assert not (broken / anchor.KEY_FILE).exists()
    empty = tmp_path / "empty"
    empty.mkdir()
    code, out, err = run(capsys, "checkpoint", "--ledger-dir", str(empty))
    assert (code, out, err) == (2, "", f"polarizer: no ledger at {empty}\n")
    v0 = install_fixture("valid/v0-parallax", tmp_path / "v0")
    code, out, err = run(capsys, "checkpoint", "--ledger-dir", str(v0))
    assert (code, out) == (3, "")
    assert err == f"polarizer: ledger at {v0} is v0 (Parallax's format); a checkpoint needs v1\n"
    bad = install_fixture("valid/session", tmp_path / "bad")
    (bad / anchor.KEY_FILE).write_bytes(b"A" * 64 + b"\n")
    code, out, err = run(capsys, "checkpoint", "--ledger-dir", str(bad))
    assert (code, out) == (2, "")
    assert err == (
        f"polarizer: {bad / anchor.KEY_FILE} is not a checkpoint key (64 lowercase hex "
        "characters and a newline)\n"
    )


@POSIX
def test_checkpoint_refuses_a_key_others_can_use(tmp_path, capsys):
    directory = install_fixture("valid/session", tmp_path / "ledger")
    key_path = directory / anchor.KEY_FILE
    shutil.copyfile(KEY, key_path)
    os.chmod(key_path, 0o640)
    code, out, err = run(capsys, "checkpoint", "--ledger-dir", str(directory))
    assert (code, out) == (2, "")
    assert err == (
        f"polarizer: {key_path} must be readable and writable only by its owner (mode 0600); "
        "it is 0640\n"
    )


def test_checkpoint_refuses_a_forbidden_ledger_dir(tmp_path, capsys, fake_home):
    directory = install_fixture("valid/session", fake_home / ".config" / "parallax" / "x")
    code, out, err = run(capsys, "checkpoint", "--ledger-dir", str(directory))
    assert (code, out) == (2, "")
    assert "which Polarizer must not write to" in err
    assert not (directory / anchor.KEY_FILE).exists()


def listing(root):
    return {
        str(p.relative_to(root)): (p.stat().st_size, p.stat().st_mtime_ns)
        for p in [root, *sorted(root.rglob("*"))]
    }


def test_verify_anchor_and_checkpoint_write_only_the_key(tmp_path, capsys):
    directory = with_key("valid/session", tmp_path / "ledger")
    before = listing(directory)
    for case in ("extends", "rewound", "bad_signature"):
        run(capsys, "verify", "--ledger-dir", str(directory), "--anchor", CASES[case]["trailer"])
    assert listing(directory) == before
    (directory / anchor.KEY_FILE).unlink()
    before = listing(directory)
    assert run(capsys, "checkpoint", "--ledger-dir", str(directory))[0] == 0
    after = listing(directory)
    assert set(after) - set(before) == {anchor.KEY_FILE}
    assert {k: v for k, v in after.items() if k not in (".", anchor.KEY_FILE)} == {
        k: v for k, v in before.items() if k != "."
    }
