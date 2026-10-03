"""serve's stdout carries only JSON-RPC; everything else goes to stderr (docs/PROXY-SPEC.md,
Output streams). Real stdio, raw bytes: no SDK client between the test and the pipe."""

import json
import subprocess
import sys

from helpers import rig


def test_stdout_clean(tmp_path):
    ledger_dir = tmp_path / "ledger"
    toml = (
        f"[upstream.p]\ncommand = {rig.toml_str(sys.executable)}\nargs = [{rig.toml_str(rig.PROBE)}]\n\n"
        f"[upstream.gone]\ncommand = {rig.toml_str(tmp_path / 'no-such-command')}\n"
    )
    cfg = rig.serve_config(tmp_path, toml, ledger_dir)
    requests = [
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-11-25",
                "capabilities": {},
                "clientInfo": {"name": "raw-test", "version": "9"},
            },
        },
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "p__wait",
                "arguments": {"seconds": 1},
                "_meta": {"progressToken": "t1"},
            },
        },
        {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {"name": "nope", "arguments": {}},
        },
        {"jsonrpc": "2.0", "id": 5, "method": "resources/list"},
    ]
    proc = subprocess.Popen(
        [sys.executable, "-m", "polarizer", "serve", "--config", str(cfg)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    # Send each request after the previous answer, so the call finishes before stdin closes.
    out_lines = []
    for request in requests:
        proc.stdin.write(json.dumps(request).encode("utf-8") + b"\n")
        proc.stdin.flush()
        if "id" not in request:
            continue
        while True:
            line = proc.stdout.readline()
            assert line, proc.stderr.read().decode()
            out_lines.append(line)
            if json.loads(line).get("id") == request["id"]:
                break
    rest, err = proc.communicate(timeout=30)  # closes stdin: the client is done
    out = b"".join(out_lines) + rest
    assert proc.returncode == 0, err

    assert out.endswith(b"\n")
    messages = [json.loads(line) for line in out.split(b"\n")[:-1]]  # each line is JSON-RPC
    assert all(m["jsonrpc"] == "2.0" for m in messages)
    by_id = {m["id"]: m for m in messages if "id" in m}
    assert by_id[1]["result"]["capabilities"] == {
        "experimental": {},
        "tools": {"listChanged": True},
    }
    assert by_id[1]["result"]["protocolVersion"] == "2025-11-25"
    assert [t["name"] for t in by_id[2]["result"]["tools"]] == [
        "p__wait",
        "p__crash",
        "p__env",
        "p__fail",
    ]
    assert by_id[3]["result"]["content"] == [{"type": "text", "text": "waited 1 s"}]
    assert by_id[4]["result"]["isError"] is True
    assert by_id[5]["error"]["code"] == -32601
    progress = [m for m in messages if m.get("method") == "notifications/progress"]
    assert [p["params"]["progressToken"] for p in progress] == ["t1"]

    text = err.decode("utf-8")
    assert "probe: started" in text  # the upstream's stderr reaches serve's stderr...
    assert "polarizer: upstream gone did not connect:" in text  # ...with serve's own logs
    assert b"probe: started" not in out and b"did not connect" not in out
    (client,) = rig.kinds(ledger_dir, "session.client")
    assert client["data"]["client_name"] == "raw-test"
    assert client["data"]["protocol_version"] == "2025-11-25"
