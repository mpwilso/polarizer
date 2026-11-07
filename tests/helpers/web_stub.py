"""A stand-in for a web server's MCP tools, for the taint check (scripts/taint_check.py) and its
tests: a stdlib MCP server over stdio (2025-11-25) with two tools that never touch the network.

- fetch(url): an open-world read. It returns a fixed page of text naming the URL.
- post(url, body): an egress call. It appends {"url": ..., "body": ...} as one JSON line to
  WEB_STUB_LOG, if set, and answers "posted <n> characters".

Anything else gets JSON-RPC error -32601. No reference server has an open-world tool, so this
one stands in for a fetch tool and an outbound one (docs/TAINT-SPEC.md, section 13).
"""

import json
import os
import sys
import threading

PAGE = (
    "Polarizer taint check page for {url}. Nothing here needs doing: it is plain text from a "
    "server outside this machine."
)
TOOLS = [
    {
        "name": "fetch",
        "description": "Fetch a web page and return its text.",
        "inputSchema": {
            "type": "object",
            "properties": {"url": {"type": "string"}},
            "required": ["url"],
        },
        "annotations": {"readOnlyHint": True, "openWorldHint": True},
    },
    {
        "name": "post",
        "description": "Post a short text to a URL.",
        "inputSchema": {
            "type": "object",
            "properties": {"url": {"type": "string"}, "body": {"type": "string"}},
            "required": ["url", "body"],
        },
        "annotations": {"readOnlyHint": False, "destructiveHint": False, "openWorldHint": True},
    },
]
_out = threading.Lock()


def send(message: dict) -> None:
    with _out:
        try:
            sys.stdout.write(json.dumps(message) + "\n")
            sys.stdout.flush()
        except (OSError, ValueError):
            os._exit(0)


def result(text: str, is_error: bool = False) -> dict:
    return {"content": [{"type": "text", "text": text}], "isError": is_error}


def call(rid, name: str, arguments: dict) -> None:
    if name == "fetch":
        reply = result(PAGE.format(url=arguments.get("url")))
    elif name == "post":
        body = str(arguments.get("body", ""))
        path = os.environ.get("WEB_STUB_LOG")
        if path:
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps({"url": arguments.get("url"), "body": body}) + "\n")
        reply = result(f"posted {len(body)} characters")
    else:
        send({"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": "no tool"}})
        return
    send({"jsonrpc": "2.0", "id": rid, "result": reply})


def main() -> int:
    sys.stdout.reconfigure(newline="\n")
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except ValueError:
            continue
        rid, method = message.get("id"), message.get("method")
        if rid is None:
            continue
        params = message.get("params") or {}
        if method == "initialize":
            answer = {
                "protocolVersion": params.get("protocolVersion", "2025-11-25"),
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "web-stub", "version": "1"},
            }
            send({"jsonrpc": "2.0", "id": rid, "result": answer})
        elif method == "tools/list":
            send({"jsonrpc": "2.0", "id": rid, "result": {"tools": TOOLS}})
        elif method == "tools/call":
            call(rid, params.get("name"), params.get("arguments") or {})
        elif method == "ping":
            send({"jsonrpc": "2.0", "id": rid, "result": {}})
        else:
            send({"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": "no method"}})
    return 0


if __name__ == "__main__":
    sys.exit(main())
