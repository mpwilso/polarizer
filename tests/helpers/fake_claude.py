"""A stand-in for `claude -p` in tests/test_rugpull_check.py: no model, no network.

It reads the --mcp-config it is given, starts that server the way Claude Code does (its own
environment plus the config's env), lists the tools, and calls probe__wait with seconds 1 only
if it is listed, as Claude Code can only call a listed tool. It answers with Claude Code's JSON
result shape (`--output-format json`), with a made-up total_cost_usd.

Environment (set by the test):
- STUB_DIR: where it counts its runs (`count`) and records each run's argv, working directory,
  stdin and MCP_TOOL_TIMEOUT (`run-<n>.json`). `--version` is not counted.
- STUB_MODE: what goes wrong, for the failing cases.
  - "" or "normal": nothing.
  - "b-calls": in run 2 (B), after listing, it approves the changed definition through the
    library and lists again until probe__wait appears, then calls it: a stand-in for a
    Polarizer that lets the changed tool through.
  - "no-drift": the server gets PROBE_PHASE=original whatever the config says, so the probe
    never changes.
  - "tamper": after run 3 (C), it changes one byte of an earlier ledger entry.
  - "fail-b": run 2 (B) prints an error, as Claude Code does for a config it rejects, and
    exits 1 without starting anything.
"""

import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from raw import RawClient  # noqa: E402

TOOL = "probe__wait"


def record(stub_dir: Path, argv: list[str]) -> int:
    count = stub_dir / "count"
    n = int(count.read_text(encoding="utf-8")) + 1 if count.exists() else 1
    count.write_text(str(n), encoding="utf-8")
    stdin = os.fstat(0)
    null = os.stat(os.devnull)
    info = {
        "argv": argv,
        "cwd": os.getcwd(),
        "stdin_is_devnull": (stdin.st_dev, stdin.st_ino) == (null.st_dev, null.st_ino),
        "MCP_TOOL_TIMEOUT": os.environ.get("MCP_TOOL_TIMEOUT"),
    }
    (stub_dir / f"run-{n}.json").write_text(json.dumps(info), encoding="utf-8")
    return n


def listed(client: RawClient) -> list[str]:
    answer = client.request("tools/list", {})
    return [t["name"] for t in answer["result"]["tools"]]


def approve_changed(config_path: str) -> None:
    from polarizer import config, pins
    from polarizer.decisions import Decider

    ledger_dir = config.load(Path(config_path), require_env=False).ledger_dir
    decider = Decider.open(ledger_dir)
    try:
        found = pins.blocks(decider.pins, decider.ledger_dir, "probe")
        block = next(b for b in found if b.kind == "changed" and b.tool == "wait")
        decider.approve_one("probe", "wait", block.def_hash, lambda line: None)
    finally:
        decider.close()


def tamper(config_path: str) -> None:
    from polarizer import config

    ledger = config.load(Path(config_path), require_env=False).ledger_dir / "ledger.jsonl"
    data = bytearray(ledger.read_bytes())
    at = data.index(b'"ts":"', data.index(b"\n") + 1) + len(b'"ts":"')  # entry 1's year
    data[at] = ord("1") if data[at] != ord("1") else ord("2")
    ledger.write_bytes(bytes(data))


def main(argv: list[str]) -> int:
    if argv == ["--version"]:
        print("0.0.0 (fake claude for tests)")
        return 0
    stub_dir = Path(os.environ["STUB_DIR"])
    mode = os.environ.get("STUB_MODE", "")
    run = record(stub_dir, argv)
    if mode == "fail-b" and run == 2:
        print("Error: Invalid MCP configuration:", file=sys.stderr)
        print("mcpServers.pz: the stub rejected this config", file=sys.stderr)
        return 1

    server = json.loads(Path(argv[argv.index("--mcp-config") + 1]).read_text())["mcpServers"]
    server = server["pz"]
    env = {**os.environ, **server.get("env", {})}
    if mode == "no-drift":
        env["PROBE_PHASE"] = "original"
    config_path = server["args"][server["args"].index("--config") + 1]
    with open(stub_dir / f"serve-{run}.err", "wb") as errlog:
        client = RawClient([server["command"], *server["args"]], stderr=errlog, env=env)
        try:
            client.initialize()
            names = listed(client)
            if mode == "b-calls" and run == 2 and TOOL not in names:
                approve_changed(config_path)
                deadline = time.monotonic() + 10
                while TOOL not in names and time.monotonic() < deadline:
                    time.sleep(0.2)
                    names = listed(client)
            if TOOL in names:
                answer = client.request("tools/call", {"name": TOOL, "arguments": {"seconds": 1}})
                texts = [c.get("text", "") for c in answer["result"]["content"]]
                reply = f"I called {TOOL} and it said: {' '.join(texts)}"
            else:
                reply = f"The tool {TOOL} is not available, so I did not call anything."
        finally:
            client.close()
    if mode == "tamper" and run == 3:
        tamper(config_path)
    result = {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "result": reply,
        "total_cost_usd": 0.0012,
        "num_turns": 2,
    }
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
