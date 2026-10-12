"""tests/helpers/stuck.py: the report on a script that ran out of time, and its cleanup."""

import os
import subprocess
import sys
import time

import pytest
from helpers.stuck import stuck_report

POSIX = pytest.mark.skipif(sys.platform == "win32", reason="needs bash, ps and POSIX signals")


def gone(pid: int, within: float = 10) -> bool:
    deadline = time.monotonic() + within
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        time.sleep(0.05)
    return False


@POSIX
def test_stuck_script_is_reported_and_killed(tmp_path):
    """The whole output so far, the script's processes (its tree, and a process whose parent
    already exited but whose command line names the test's directory), all killed after."""
    script = tmp_path / "stuck.sh"
    script.write_text(
        "echo before the wait\n"
        "sleep 301 &\n"
        f'echo $! > "{tmp_path}/child"\n'
        f'( sh -c "sleep 302; true {tmp_path}" & echo $! > "{tmp_path}/orphan" )\n'
        "wait\n",
        encoding="utf-8",
    )
    proc = subprocess.Popen(
        ["bash", str(script)], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True,
    )  # fmt: skip
    with pytest.raises(subprocess.TimeoutExpired) as caught:
        proc.communicate(timeout=3)
    report = stuck_report(proc, caught.value, tmp_path)

    assert "did not finish in 3 s. Its output so far:\nbefore the wait\n" in report
    child = int((tmp_path / "child").read_text())
    orphan = int((tmp_path / "orphan").read_text())
    assert f"bash {script}" in report
    assert "sleep 301" in report
    assert f"sleep 302; true {tmp_path}" in report
    assert proc.returncode is not None
    assert gone(child) and gone(orphan)
