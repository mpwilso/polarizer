"""scripts/live_check.py decides live-check.sh's result from its logs. These run its decision
and its config writer on made-up logs, with no Claude Code and no model."""

import json
import sys
import tomllib
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import live_check  # noqa: E402

CALL = {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "wait"}}


def cancel(rid):
    return {"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {"requestId": rid}}


def wire(*cancel_times):
    lines = ["100.000 c2s " + json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/call"})]
    lines += [f"{t:.3f} c2s {json.dumps(cancel(2))}" for t in cancel_times]
    return lines + ["120.000 c2s <end of input>"]


def probe(*cancel_times, called=True):
    lines = ["99.000 start 1234"]
    if called:
        lines.append(f"100.002 {json.dumps(CALL)}")
    return lines + [f"{t:.3f} {json.dumps(cancel(4))}" for t in cancel_times]


def ledger(outcome="cancelled", tool="probe__wait"):
    return [
        {"seq": 5, "kind": "call.sent", "data": {"tool": tool}},
        {"seq": 6, "kind": "call.returned", "data": {"call_seq": 5, "outcome": outcome}},
    ]


def verdict(*args):
    return [ok for _, ok, _ in live_check.evaluate(*args)]


def test_passes():
    assert verdict(wire(105.009), probe(105.010), ledger()) == [True, True, True]


@pytest.mark.parametrize(
    "args, expected",
    [
        ((wire(), probe(), ledger()), [True, False, True]),  # Claude Code never cancelled
        ((wire(105.0), probe(), ledger()), [True, False, True]),  # the probe never got it
        ((wire(105.0), probe(107.5), ledger()), [True, False, True]),  # 2.5 s late
        ((wire(105.0), probe(104.0), ledger()), [True, False, True]),  # before Claude Code's
        ((wire(105.0), probe(105.1), ledger("ok")), [True, True, False]),
        ((wire(105.0), probe(105.1), ledger(tool="other__wait")), [True, True, False]),
        ((wire(), probe(called=False), []), [False, False, False]),
    ],
)
def test_fails(args, expected):
    assert verdict(*args) == expected


def test_check_names_failures(tmp_path, capsys):
    (tmp_path / "ledger").mkdir()
    (tmp_path / "wire.log").write_text("\n".join(wire(105.0)) + "\n", encoding="utf-8")
    (tmp_path / "probe.log").write_text("\n".join(probe(105.1)) + "\n", encoding="utf-8")
    lines = [json.dumps(e) for e in ledger("ok")]
    (tmp_path / "ledger" / "ledger.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    assert live_check.check(tmp_path) == 1
    out = capsys.readouterr().out.splitlines()
    assert out[-1] == (
        "live check FAILED: the ledger has call.returned with outcome cancelled for that call"
    )


def test_setup_writes_both_configs(tmp_path):
    repo = Path(__file__).resolve().parent.parent
    live_check.setup(tmp_path, repo)
    config = tomllib.loads((tmp_path / "polarizer.toml").read_text(encoding="utf-8"))
    assert config["ledger_dir"] == (tmp_path / "ledger").as_posix()
    assert config["upstream"]["probe"]["env"] == {"PROBE_LOG": str(tmp_path / "probe.log")}
    server = json.loads((tmp_path / "mcp.json").read_text(encoding="utf-8"))["mcpServers"]["pz"]
    assert server["args"][:2] == [str(repo / "scripts" / "wiretap.py"), str(tmp_path / "wire.log")]
    assert server["args"][-2:] == ["--config", str(tmp_path / "polarizer.toml")]


@pytest.mark.skipif(
    sys.platform == "win32", reason="live-check.sh is POSIX only: its config names .venv/bin/python"
)
def test_prime_exposes_the_probe_tool(tmp_path, capsys):
    """live_check.py prime, with no Claude Code and no model: a serve run with stdin closed
    records the probe's definitions, and the group approval approves every one, so the next
    serve exposes probe__wait."""
    from polarizer.decisions import Decider

    repo = Path(__file__).resolve().parent.parent
    live_check.setup(tmp_path, repo)
    assert live_check.prime(tmp_path) == 0
    assert capsys.readouterr().out.startswith("prime: approved 7 definitions as group ")
    entries = [
        json.loads(line)
        for line in (tmp_path / "ledger" / "ledger.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    approved = {e["data"]["tool"] for e in entries if e["kind"] == "tool.approved"}
    assert "wait" in approved and len(approved) == 7
    assert not [e for e in entries if e["kind"].startswith("call.")]  # nothing was called
    decider = Decider.open(tmp_path / "ledger")
    try:
        assert decider.pending_group() == ([], None)
    finally:
        decider.close()
    assert "tools/call" not in (tmp_path / "probe.log").read_text(encoding="utf-8")
