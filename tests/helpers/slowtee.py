"""Running a manual-check script as bash 3.2 would, on any bash, to show its output is all
written before it exits.

On macOS's bash 3.2, `wait` on a process substitution's PID returns at once, so a script that
waited that way for its tee could exit before tee wrote (CI run 37249953282: hold-check.sh status
printed nothing). Bash 5 waits, so on Linux the race was hidden, and BASH_COMPAT=32 doesn't bring
it back. Here a BASH_ENV file replaces `wait` with one that waits only for a job started with &,
as bash 3.2 does, and a stand-in tee (this file, run as a program) sleeps before each write. A
script that still waits on a process substitution then exits before the end of its output is
written; one that waits for a real child doesn't.

As a program: `slowtee.py [-a] FILE` copies standard input to standard output and to FILE,
appending with -a, sleeping DELAY seconds before each write."""

import os
import sys
import threading
import time
from pathlib import Path

DELAY = 0.2

# bash 3.2 can wait only for its own jobs; a process substitution is not one.
BASH32_WAIT = """\
wait() {
  local pid
  for pid in "$@"; do
    case " $(jobs -p | tr '\\n' ' ') " in
      *" $pid "*) builtin wait "$pid" || return ;;
      *) echo "wait: pid $pid is not a child of this shell" >&2; return 127 ;;
    esac
  done
}
"""


def slow_env(env: dict, where: Path) -> dict:
    """env with the stand-in tee first on PATH and the bash 3.2 wait in BASH_ENV; their files are
    written under where."""
    bin_dir = where / "slowtee-bin"
    bin_dir.mkdir(parents=True)
    tee = bin_dir / "tee"
    tee.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{__file__}" "$@"\n', encoding="utf-8")
    tee.chmod(0o755)
    bash_env = where / "bash32-wait.sh"
    bash_env.write_text(BASH32_WAIT, encoding="utf-8")
    return {**env, "PATH": f"{bin_dir}:{env.get('PATH', '')}", "BASH_ENV": str(bash_env)}


class AtExit:
    """Reads a file the moment a process exits: call watch(proc) as soon as it has started."""

    def __init__(self, path: Path):
        self.path = path
        self.text: str | None = None
        self._thread: threading.Thread | None = None

    def watch(self, proc) -> None:
        def run():
            proc.wait()
            try:
                self.text = self.path.read_text(encoding="utf-8")
            except FileNotFoundError:
                self.text = ""

        self._thread = threading.Thread(target=run, daemon=True)
        self._thread.start()

    def result(self) -> str:
        assert self._thread is not None, "watch() was never called"
        self._thread.join(timeout=60)
        assert self.text is not None, "the process did not exit"
        return self.text


def main(args: list[str]) -> int:
    append = args[:1] == ["-a"]
    path = args[1:] if append else args
    if len(path) != 1:
        print("usage: slowtee.py [-a] FILE", file=sys.stderr)
        return 2
    with open(path[0], "ab" if append else "wb", buffering=0) as copy:
        while chunk := os.read(0, 65536):
            time.sleep(DELAY)
            copy.write(chunk)
            try:
                rest = memoryview(chunk)
                while rest:
                    rest = rest[os.write(1, rest) :]
            except OSError:  # the terminal is gone; keep writing the file, as tee does
                pass
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
