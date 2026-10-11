"""ci-scratch only: what was alive when a script under test timed out."""

import os
import signal
import subprocess
import time
from pathlib import Path


def _run(argv: list[str]) -> str:
    try:
        done = subprocess.run(argv, capture_output=True, text=True, errors="replace", timeout=60)
        return done.stdout + done.stderr
    except Exception as e:  # noqa: BLE001
        return f"{argv[0]} failed: {e!r}\n"


def _procs() -> list[tuple[int, int, str]]:
    out = _run(["ps", "-axww", "-o", "pid=,ppid=,command="])
    rows = []
    for line in out.splitlines():
        parts = line.split(None, 2)
        if len(parts) >= 2 and parts[0].isdigit():
            rows.append((int(parts[0]), int(parts[1]), parts[2] if len(parts) > 2 else ""))
    return rows


def interesting(root_pid: int, marker: str) -> tuple[list[int], list[int]]:
    """(pids to list, pids to signal)."""
    rows = _procs()
    children: dict[int, list[int]] = {}
    for pid, ppid, _ in rows:
        children.setdefault(ppid, []).append(pid)
    tree, todo = set(), [root_pid]
    while todo:
        p = todo.pop()
        if p in tree:
            continue
        tree.add(p)
        todo += children.get(p, [])
    tree.discard(root_pid)
    kill = set(tree)
    shown = set(tree)
    for pid, _, cmd in rows:
        if pid == os.getpid():
            continue
        # Signal only this test's processes: its tree, or a command naming its own tmp_path.
        if marker in cmd:
            kill.add(pid)
        if pid in kill or "polarizer" in cmd or "probe_server" in cmd or "tee" in cmd:
            shown.add(pid)
    return sorted(shown | {root_pid}), sorted(kill)


def report(root_pid: int, marker: str, base: Path, output: str) -> str:
    out = [f"=== HANGDIAG at {time.strftime('%H:%M:%S')} root pid {root_pid}"]
    out.append("--- output so far:\n" + output)
    pids, kill = interesting(root_pid, marker)
    out.append("--- ps:")
    out.append(
        _run(["ps", "-ww", "-o", "pid,ppid,pgid,stat,etime,wchan,command", "-p", ",".join(map(str, pids))])
    )
    out.append("--- lsof:")
    out.append(_run(["lsof", "-nP", "-p", ",".join(map(str, pids))]))
    out.append("--- files under " + str(base))
    for path in sorted(base.rglob("*")):
        if path.is_file() and path.suffix in (".err", ".out", ".code", ".json", ".jsonl", ".log", ".txt", ""):
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError as e:
                text = repr(e)
            if path.name == "ledger.jsonl":
                text = f"{len(text.splitlines())} lines; last: {text.splitlines()[-1:]!r}"
            elif len(text) > 4000:
                text = text[:1000] + "\n...\n" + text[-3000:]
            out.append(f"## {path} ({path.stat().st_size} bytes)\n{text}")
        elif path.is_fifo():
            out.append(f"## FIFO {path}")
    # Python stacks: SIGABRT with PYTHONFAULTHANDLER=1 dumps each Python thread to stderr.
    for p in kill:
        out.append(f"--- sample {p}:")
        out.append(_run(["sample", str(p), "1", "-mayDie"])[-6000:])
    for p in kill:
        try:
            os.kill(p, signal.SIGABRT)
        except OSError:
            pass
    time.sleep(3)
    out.append("--- err files after SIGABRT:")
    for path in sorted(base.rglob("*.err")):
        text = path.read_text(encoding="utf-8", errors="replace")
        out.append(f"## {path}\n{text[-6000:]}")
    for p in kill + [root_pid]:
        try:
            os.kill(p, signal.SIGKILL)
        except OSError:
            pass
    return "\n".join(out)
