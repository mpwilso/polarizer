"""Test upstream: a hostile stdlib-only MCP server over stdio, in the 2025-11-25 handshake era,
for tests/test_upstream_text.py. What it sends that Polarizer might print or record carries
HOSTILE: terminal escapes, a bell, a newline, a carriage return, 5,000 characters and a bidi
override. A lone surrogate can't arrive in a value the SDK parses: its JSON parser refuses the
whole line. So connect-garbage sends one in a line of its own, and the SDK's log of that
failure is what has to stay safe.

It writes nothing to its own stderr: an upstream's stderr is its own channel, which serve
passes through as it is (docs/STAGE5-NOTES.md, Upstream text).

HOSTILE_MODE:
- connect-error: every tools/list answers JSON-RPC error -32603 with HOSTILE as its message.
- connect-garbage: every tools/list gets two lines the SDK can't parse, one carrying HOSTILE
  and one an error response whose message has a lone surrogate escape, and no answer, so the
  upstream times out at connect.
- refresh-error: the first tools/list succeeds; later ones answer as in connect-error.
- refresh-invalid: the first tools/list succeeds; later ones list `boom` with HOSTILE, a
  string, as its inputSchema, which the SDK's models refuse (pydantic's message quotes it).
- drop: tools/list succeeds, and also lists a tool named HOSTILE, which Polarizer skips.

In every mode, tools/call answers -32000 with HOSTILE as its message, then the process exits:
an upstream whose connection drops.
"""

import json
import os
import sys

ESC = chr(27)
HOSTILE = (
    ESC + "[2J" + ESC + "]0;pwned" + chr(7) + "\nINJECTED polarizer: a line of its own\r"
    + "Z" * 5000 + chr(0x202E) + "end"
)  # fmt: skip
LONE_SURROGATE_ESCAPE = chr(0x5C) + "ud800"  # as JSON text, which the SDK's parser refuses
BOOM = {"name": "boom", "description": "Drop the connection.", "inputSchema": {"type": "object"}}

_lists = [0]


def send_text(text: str) -> None:
    sys.stdout.buffer.write(text.encode("utf-8") + b"\n")
    sys.stdout.buffer.flush()


def send(message: dict) -> None:
    send_text(json.dumps(message))


def tools_list(rid, mode: str) -> None:
    first = _lists[0] == 0
    _lists[0] += 1
    hostile_error = {"jsonrpc": "2.0", "id": rid, "error": {"code": -32603, "message": HOSTILE}}
    if mode == "connect-error" or (mode == "refresh-error" and not first):
        send(hostile_error)
    elif mode == "connect-garbage":
        send_text("not json " + HOSTILE.replace("\n", " "))
        line = json.dumps({**hostile_error, "error": {"code": -32603, "message": "@"}})
        send_text(line.replace("@", LONE_SURROGATE_ESCAPE))
    elif mode == "refresh-invalid" and not first:
        send({"jsonrpc": "2.0", "id": rid, "result": {"tools": [{**BOOM, "inputSchema": HOSTILE}]}})
    else:
        tools = [BOOM]
        if mode == "drop":
            tools.append({"name": HOSTILE, "inputSchema": {"type": "object"}})
        send({"jsonrpc": "2.0", "id": rid, "result": {"tools": tools}})


def handle(message: dict, mode: str) -> None:
    method = message.get("method")
    rid = message.get("id")
    if rid is None:
        return  # notifications
    if method == "initialize":
        version = (message.get("params") or {}).get("protocolVersion", "2025-11-25")
        result = {
            "protocolVersion": version,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "hostile", "version": "1"},
        }
        send({"jsonrpc": "2.0", "id": rid, "result": result})
    elif method == "tools/list":
        tools_list(rid, mode)
    elif method == "tools/call":
        send({"jsonrpc": "2.0", "id": rid, "error": {"code": -32000, "message": HOSTILE}})
        os._exit(0)
    elif method == "ping":
        send({"jsonrpc": "2.0", "id": rid, "result": {}})
    else:
        send(
            {"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": "Method not found"}}
        )


def main() -> int:
    mode = os.environ.get("HOSTILE_MODE", "drop")
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except ValueError:
            continue
        handle(message, mode)
    return 0


if __name__ == "__main__":
    sys.exit(main())
