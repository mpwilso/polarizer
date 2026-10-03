"""Test upstream: a stdlib-only MCP server over stdio, in the 2025-11-25 handshake era.

It reads stdin on its own thread and runs each tools/call on a worker thread, so it can log a
notifications/cancelled that arrives while a call is running. It answers server/discover with
-32601, so an SDK client falls back to initialize.

Tools:
- wait(seconds): sleeps, sending notifications/progress every second when the call carries a
  progress token. A notifications/cancelled for the call ends it with no response.
- crash(): the process exits at once, mid-call.
- env(): the environment it was started with, as a JSON object in text.
- fail(): a tool error (isError true).

Environment:
- PROBE_LOG: a file to append every inbound line to, each prefixed with a Unix timestamp.
- PROBE_DELAY: seconds to wait before reading the first message (a slow handshake).
"""

import json
import os
import sys
import threading
import time

TOOLS = [
    {
        "name": "wait",
        "description": "Wait for a number of seconds.",
        "inputSchema": {
            "type": "object",
            "properties": {"seconds": {"type": "number"}},
            "required": ["seconds"],
        },
    },
    {"name": "crash", "description": "Exit mid-call.", "inputSchema": {"type": "object"}},
    {"name": "env", "description": "List environment names.", "inputSchema": {"type": "object"}},
    {"name": "fail", "description": "Return a tool error.", "inputSchema": {"type": "object"}},
]

_out = threading.Lock()
_log = threading.Lock()
_cancelled: dict = {}  # request id -> threading.Event


def send(message: dict) -> None:
    line = json.dumps(message) + "\n"
    with _out:
        sys.stdout.write(line)
        sys.stdout.flush()


def log(line: str) -> None:
    path = os.environ.get("PROBE_LOG")
    if path:
        with _log, open(path, "a", encoding="utf-8") as f:
            f.write(f"{time.time():.3f} {line}\n")


def text(value: str, is_error: bool = False) -> dict:
    return {"content": [{"type": "text", "text": value}], "isError": is_error}


def call(request: dict) -> None:
    rid = request["id"]
    params = request.get("params") or {}
    name = params.get("name")
    arguments = params.get("arguments") or {}
    token = (params.get("_meta") or {}).get("progressToken")
    stop = _cancelled.setdefault(rid, threading.Event())
    if name == "wait":
        seconds = float(arguments.get("seconds", 0))
        ticks = int(seconds)
        for i in range(1, ticks + 1):
            if stop.wait(1.0):
                log(f"call {rid} stopped after cancel")
                return
            if token is not None:
                progress = {"progressToken": token, "progress": i, "total": ticks}
                send({"jsonrpc": "2.0", "method": "notifications/progress", "params": progress})
        if stop.wait(seconds - ticks):
            return
        send({"jsonrpc": "2.0", "id": rid, "result": text(f"waited {seconds:g} s")})
    elif name == "crash":
        log("crashing")
        os._exit(3)
    elif name == "env":
        send({"jsonrpc": "2.0", "id": rid, "result": text(json.dumps(dict(os.environ)))})
    elif name == "fail":
        send({"jsonrpc": "2.0", "id": rid, "result": text("the probe failed on purpose", True)})
    else:
        error = {"code": -32602, "message": f"unknown tool {name}"}
        send({"jsonrpc": "2.0", "id": rid, "error": error})


def handle(message: dict) -> None:
    method = message.get("method")
    rid = message.get("id")
    if method == "notifications/cancelled":
        target = (message.get("params") or {}).get("requestId")
        _cancelled.setdefault(target, threading.Event()).set()
        return
    if rid is None:
        return  # other notifications
    if method == "initialize":
        version = (message.get("params") or {}).get("protocolVersion", "2025-11-25")
        result = {
            "protocolVersion": version,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "probe", "version": "1"},
        }
        send({"jsonrpc": "2.0", "id": rid, "result": result})
    elif method == "tools/list":
        send({"jsonrpc": "2.0", "id": rid, "result": {"tools": TOOLS}})
    elif method == "tools/call":
        threading.Thread(target=call, args=(message,), daemon=True).start()
    elif method == "ping":
        send({"jsonrpc": "2.0", "id": rid, "result": {}})
    else:
        error = {"code": -32601, "message": "Method not found"}
        send({"jsonrpc": "2.0", "id": rid, "error": error})


def read_stdin(done: threading.Event) -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        log(line)
        try:
            message = json.loads(line)
        except ValueError:
            continue
        handle(message)
    log("end of input")
    done.set()


def main() -> int:
    sys.stdout.reconfigure(newline="\n")  # one "\n" per message on every platform
    log(f"start {os.getpid()}")
    print("probe: started", file=sys.stderr, flush=True)  # stderr only, never the protocol
    delay = float(os.environ.get("PROBE_DELAY", "0"))
    if delay:
        time.sleep(delay)
    done = threading.Event()
    threading.Thread(target=read_stdin, args=(done,), daemon=True).start()
    done.wait()
    return 0


if __name__ == "__main__":
    sys.exit(main())
