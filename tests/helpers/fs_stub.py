"""A stand-in for the reference Filesystem server, for tests/test_hold_check.py: a stdlib MCP
server over stdio (2025-11-25) with two of its tools, under the same names and arguments:

- write_file(path, content): writes content to path, which must be inside the directory given
  as the first argument; the parent directory must exist.
- move_file(source, destination): renames source to destination, both inside that directory.

Anything else gets JSON-RPC error -32601. Every inbound line is appended to FS_STUB_LOG, if
set. It is not the real server: it only lets the hold check's script run end to end without
npm."""

import json
import os
import sys
import threading

ROOT = os.path.realpath(sys.argv[1]) if len(sys.argv) > 1 else None
TOOLS = [
    {
        "name": "write_file",
        "description": "Create a new file or overwrite an existing one.",
        "inputSchema": {
            "type": "object",
            "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
            "required": ["path", "content"],
        },
    },
    {
        "name": "move_file",
        "description": "Move or rename a file.",
        "inputSchema": {
            "type": "object",
            "properties": {"source": {"type": "string"}, "destination": {"type": "string"}},
            "required": ["source", "destination"],
        },
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


def log(line: str) -> None:
    path = os.environ.get("FS_STUB_LOG")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write(line + "\n")


def inside(path: str) -> str:
    resolved = os.path.realpath(path)
    if ROOT is None or os.path.commonpath([ROOT, resolved]) != ROOT:
        raise PermissionError(f"Access denied - path outside allowed directories: {path}")
    return resolved


def result(text: str, is_error: bool = False) -> dict:
    return {"content": [{"type": "text", "text": text}], "isError": is_error}


def call(rid, name: str, arguments: dict) -> None:
    try:
        if name == "write_file":
            with open(inside(arguments["path"]), "w", encoding="utf-8") as f:
                f.write(arguments["content"])
            reply = result(f"Successfully wrote to {arguments['path']}")
        elif name == "move_file":
            os.rename(inside(arguments["source"]), inside(arguments["destination"]))
            reply = result(f"Successfully moved {arguments['source']}")
        else:
            send({"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": "no tool"}})
            return
    except (OSError, KeyError, TypeError) as e:
        reply = result(f"Error: {e}", True)
    send({"jsonrpc": "2.0", "id": rid, "result": reply})


def main() -> int:
    sys.stdout.reconfigure(newline="\n")
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        log(line)
        try:
            message = json.loads(line)
        except ValueError:
            continue
        rid, method = message.get("id"), message.get("method")
        if rid is None:
            continue
        params = message.get("params") or {}
        if method == "initialize":
            info = {"name": "fs-stub", "version": "1"}
            answer = {
                "protocolVersion": params.get("protocolVersion", "2025-11-25"),
                "capabilities": {"tools": {}},
                "serverInfo": info,
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
    log("end of input")
    return 0


if __name__ == "__main__":
    sys.exit(main())
