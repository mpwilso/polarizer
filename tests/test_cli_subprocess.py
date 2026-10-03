"""The real CLI's raw output bytes: `python -m polarizer verify` in a subprocess prints exactly
the golden bytes on every platform. stdout and stderr are UTF-8 with "\\n" newlines, even when
the environment asks for another encoding, and even when the directory name isn't ASCII."""

import os
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import install_fixture

GOLDEN = Path(__file__).parent / "golden"


def run(*args):
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONUTF8", "PYTHONIOENCODING")}
    env["PYTHONIOENCODING"] = "ascii:strict"  # a hostile default; the CLI must override it
    return subprocess.run(
        [sys.executable, "-m", "polarizer", *args], capture_output=True, env=env, timeout=60
    )


def make_dir(tmp_path, name):
    try:
        path = tmp_path / name
        path.mkdir()
    except (OSError, UnicodeError) as e:
        pytest.skip(f"cannot create a directory named {name!r}: {e}")
    return path


@pytest.mark.parametrize("name", ["ledger", "ledger-caf\xe9-\u65e5\u672c"])
@pytest.mark.parametrize(
    "fixture, golden, code",
    [
        ("valid/session", "verify_intact", 0),
        ("broken/edit_value", "verify_tampered_hash", 1),
        ("broken/non_ascii_key", "verify_invalid_non_ascii_key", 3),
        ("broken/tear_last_line", "verify_torn_tail", 5),
        (None, "verify_no_ledger", 2),
    ],
)
def test_raw_stdout_matches_golden(tmp_path, name, fixture, golden, code):
    directory = make_dir(tmp_path, name)
    if fixture:
        install_fixture(fixture, directory)
    done = run("verify", "--ledger-dir", str(directory))
    assert done.returncode == code, done.stderr
    assert done.stderr == b""
    out = done.stdout.replace(str(directory).encode("utf-8"), b"<dir>")
    assert b"\r" not in done.stdout
    assert out == (GOLDEN / f"{golden}.txt").read_bytes()


def test_raw_stderr_is_one_utf8_line(tmp_path):
    relative = "caf\xe9/ledger"
    done = run("verify", "--ledger-dir", relative)
    assert done.returncode == 2 and done.stdout == b""
    want = f"polarizer: --ledger-dir must be an absolute path, got {relative}\n"
    assert done.stderr == want.encode("utf-8")
