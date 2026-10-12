"""What a script under test was doing when it ran out of time (POSIX).

When proc.communicate(timeout=...) raises TimeoutExpired, pytest shows only a cut repr of the
output read so far, and the script's processes keep running. stuck_report gives the whole output
so far, then `ps` and `lsof` lines for the script's processes, then kills them, so a rare hang
leaves evidence in the CI log and nothing behind (docs/dev/macos-hang.md).

The script's processes are its process tree, plus any process whose command line names the
test's own directory (a child whose parent already exited). Nothing else is signalled.
"""

import os
import signal
import subprocess
from pathlib import Path


def _run(argv: list[str]) -> str:
    try:
        done = subprocess.run(argv, capture_output=True, text=True, errors="replace", timeout=30)
    except (OSError, subprocess.TimeoutExpired) as e:
        return f"{argv[0]}: {e}\n"
    return done.stdout + done.stderr


def processes(root: int, marker: str) -> list[int]:
    """root's process tree and every process whose command line contains marker, root first."""
    rows = []
    for line in _run(["ps", "-ax", "-ww", "-o", "pid=,ppid=,command="]).splitlines():
        parts = line.split(None, 2)
        if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit():
            rows.append((int(parts[0]), int(parts[1]), parts[2] if len(parts) == 3 else ""))
    found, todo = [], [root]
    while todo:
        pid = todo.pop(0)
        if pid not in found:
            found.append(pid)
            todo += [p for p, parent, _ in rows if parent == pid]
    found += [p for p, _, cmd in rows if marker in cmd and p not in found and p != os.getpid()]
    return found


def stuck_report(proc: subprocess.Popen, error: subprocess.TimeoutExpired, where: Path) -> str:
    """The report for a script that timed out; its processes are killed afterwards."""
    output = error.output or ""
    if isinstance(output, bytes):
        output = output.decode("utf-8", "replace")
    pids = processes(proc.pid, str(Path(where).resolve()))
    listed = ",".join(map(str, pids))
    report = [
        f"{error.cmd} did not finish in {error.timeout} s. Its output so far:",
        output,
        f"Its processes ({listed}):",
        _run(["ps", "-ww", "-o", "pid,ppid,pgid,stat,etime,command", "-p", listed]),
        "Their open file descriptors (lsof, numbered ones only):",
        "\n".join(
            line
            for line in _run(["lsof", "-nP", "-p", listed]).splitlines()
            if len(line.split()) > 3 and line.split()[3][:1].isdigit()
        ),
    ]
    for pid in pids:
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass
    proc.wait()
    return "\n".join(report)
