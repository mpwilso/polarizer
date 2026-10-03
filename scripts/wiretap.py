"""Relay a stdio MCP server and log every line in both directions, with Unix timestamps.

Usage: wiretap.py <log file> <command> [args...]

scripts/live-check.sh puts it between Claude Code and `polarizer serve`, so the log shows
when Claude Code itself sent notifications/cancelled. Each log line is
`<time.time()> <c2s|s2c> <the line as sent>`. Standard library only.
"""

import signal
import subprocess
import sys
import threading
import time


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print("usage: wiretap.py <log file> <command> [args...]", file=sys.stderr)
        return 2
    with open(argv[1], "a", encoding="utf-8") as log:
        return relay(log, argv[2:])


def relay(log, command: list[str]) -> int:
    lock = threading.Lock()

    def record(direction: str, line: bytes) -> None:
        text = line.rstrip(b"\r\n").decode("utf-8", "backslashreplace")
        with lock:
            log.write(f"{time.time():.3f} {direction} {text}\n")
            log.flush()

    child = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE)

    def client_to_server() -> None:
        for line in sys.stdin.buffer:
            record("c2s", line)
            try:
                child.stdin.write(line)
                child.stdin.flush()
            except OSError:
                break
        record("c2s", b"<end of input>")
        try:
            child.stdin.close()
        except OSError:
            pass

    def server_to_client() -> None:
        for line in child.stdout:
            record("s2c", line)
            sys.stdout.buffer.write(line)
            sys.stdout.buffer.flush()

    def stop(signum, frame) -> None:
        record("c2s", f"<signal {signum}>".encode())
        child.terminate()

    signal.signal(signal.SIGTERM, stop)
    threading.Thread(target=client_to_server, daemon=True).start()
    relay = threading.Thread(target=server_to_client, daemon=True)
    relay.start()
    code = child.wait()
    relay.join(timeout=5)
    record("s2c", f"<server exited {code}>".encode())
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv))
