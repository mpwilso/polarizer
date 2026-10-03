"""A raw JSON-RPC client over stdio, with no SDK in between: it sees exactly the bytes a
non-SDK client such as Claude Code would."""

import json
import subprocess


class RawClient:
    """One stdio server process. Requests are sent one at a time; anything without the
    awaited id (notifications, requests from the server) is kept in `other`."""

    def __init__(self, argv, *, stderr=subprocess.DEVNULL, env=None):
        self.proc = subprocess.Popen(
            argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=stderr, env=env
        )
        self.next_id = 1
        self.other: list[dict] = []

    def send(self, message: dict) -> None:
        self.proc.stdin.write(json.dumps(message).encode("utf-8") + b"\n")
        self.proc.stdin.flush()

    def request(self, method: str, params: dict | None = None) -> dict:
        """The whole response message: it has "result" or "error"."""
        rid = self.next_id
        self.next_id += 1
        message = {"jsonrpc": "2.0", "id": rid, "method": method}
        if params is not None:
            message["params"] = params
        self.send(message)
        while True:
            line = self.proc.stdout.readline()
            if not line:
                raise EOFError(f"{method}: the server closed its stdout")
            answer = json.loads(line)
            if answer.get("id") == rid and "method" not in answer:
                return answer
            self.other.append(answer)

    def initialize(self, version: str = "2025-11-25") -> dict:
        hello = {
            "protocolVersion": version,
            "capabilities": {},
            "clientInfo": {"name": "raw-test", "version": "1"},
        }
        answer = self.request("initialize", hello)
        self.send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        return answer

    def close(self, timeout: float = 30) -> int:
        """Close stdin, as a client does when it's done, and wait for the process."""
        self.proc.stdin.close()
        try:
            return self.proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            return self.proc.wait()
        finally:
            self.proc.stdout.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
