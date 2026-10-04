"""serve's shutdown: SIGINT, SIGTERM and end of input in the middle of a call, and a kill
(docs/PIN-SPEC.md, section 8). Each test runs `polarizer serve` over stdio in front of the probe,
with a primed ledger, and starts a 30 s wait call before stopping it."""

import json
import os
import signal
import sys
import time

import pytest
from helpers import rig
from helpers.raw import RawClient

from polarizer.ledger import verify_bytes

POSIX_ONLY = pytest.mark.skipif(
    sys.platform == "win32",
    reason="Windows has no way to send SIGINT and SIGTERM to a child the way Claude Code does: "
    "os.kill with SIGTERM there is TerminateProcess, which can't be caught, and SIGINT needs "
    "a shared console",
)
SHUTDOWN_ERROR = "polarizer shut down during the call"


def primed(tmp_path):
    """(config, ledger_dir, probe log) with every probe tool approved."""
    log = tmp_path / "probe.log"
    toml = (
        f"[upstream.p]\ncommand = {rig.toml_str(sys.executable)}\n"
        f"args = [{rig.toml_str(rig.PROBE)}]\nenv = {{ PROBE_LOG = {rig.toml_str(log)} }}\n"
    )
    cfg = rig.serve_config(tmp_path, toml, tmp_path / "ledger")
    return cfg, rig.prime(cfg), log


def serve(cfg) -> RawClient:
    return RawClient([sys.executable, "-m", "polarizer", "serve", "--config", str(cfg)])


def wait_for(condition, seconds=30):
    deadline = time.monotonic() + seconds
    while not condition():
        assert time.monotonic() < deadline, "timed out"
        time.sleep(0.01)


def start_long_call(client: RawClient, log) -> None:
    """Send tools/call p__wait(30) without waiting for its answer, then wait until the probe
    has the call, so the call is in flight in the upstream."""
    client.initialize()
    calls = log.read_text(encoding="utf-8").count('"tools/call"')
    params = {"name": "p__wait", "arguments": {"seconds": 30}}
    client.send({"jsonrpc": "2.0", "id": 99, "method": "tools/call", "params": params})
    wait_for(lambda: log.read_text(encoding="utf-8").count('"tools/call"') > calls)


def finish(client: RawClient, seconds=30) -> int:
    try:
        return client.proc.wait(timeout=seconds)
    finally:
        client.proc.stdin.close()
        client.proc.stdout.close()


def the_call(ledger_dir) -> tuple[dict, list[dict]]:
    """(the p__wait call.sent, the call.returned entries for it); the ledger must verify."""
    data = (ledger_dir / "ledger.jsonl").read_bytes()
    assert verify_bytes(data, (ledger_dir / "ledger.head").read_bytes()).status == "intact"
    entries = [json.loads(line) for line in data.splitlines()]
    (sent,) = [e for e in entries if e["kind"] == "call.sent" and e["data"]["tool"] == "p__wait"]
    returned = [
        e for e in entries if e["kind"] == "call.returned" and e["data"]["call_seq"] == sent["seq"]
    ]
    return sent, returned


def assert_cancelled_by_shutdown(ledger_dir, log):
    _, returned = the_call(ledger_dir)
    (entry,) = returned  # exactly one call.returned
    assert entry["data"]["outcome"] == "cancelled"
    assert entry["data"]["error"] == SHUTDOWN_ERROR
    assert entry["data"]["result_bytes"] == 0
    # The SDK's upstream client sent its own cancel as part of the call's cancellation.
    assert "notifications/cancelled" in log.read_text(encoding="utf-8")


@POSIX_ONLY
def test_sigint_then_sigterm_during_call(tmp_path):
    """SIGINT, then SIGTERM 100 ms later, as Claude Code sends them on Esc: serve exits 0, the
    ledger verifies, and the call has one call.returned, cancelled by the shutdown."""
    cfg, ledger_dir, log = primed(tmp_path)
    client = serve(cfg)
    start_long_call(client, log)
    os.kill(client.proc.pid, signal.SIGINT)
    time.sleep(0.1)
    if client.proc.poll() is None:
        os.kill(client.proc.pid, signal.SIGTERM)
    assert finish(client) == 0
    assert_cancelled_by_shutdown(ledger_dir, log)


@POSIX_ONLY
def test_sigterm_alone_during_call(tmp_path):
    cfg, ledger_dir, log = primed(tmp_path)
    client = serve(cfg)
    start_long_call(client, log)
    began = time.monotonic()
    os.kill(client.proc.pid, signal.SIGTERM)
    assert finish(client) == 0
    elapsed = time.monotonic() - began
    assert_cancelled_by_shutdown(ledger_dir, log)
    assert "end of input" in log.read_text(encoding="utf-8")  # the probe was closed in step 2
    assert elapsed < 30  # nowhere near the call's own 30 s


def test_stdin_closed_during_call(tmp_path):
    """Closing serve's stdin mid-call gives the same ledger result."""
    cfg, ledger_dir, log = primed(tmp_path)
    client = serve(cfg)
    start_long_call(client, log)
    client.proc.stdin.close()
    assert client.proc.wait(timeout=30) == 0
    client.proc.stdout.close()
    assert_cancelled_by_shutdown(ledger_dir, log)


@POSIX_ONLY
def test_kill_then_restart(tmp_path):
    """SIGKILL mid-call leaves an intact ledger with the call.sent and no call.returned, and a
    new serve exposes the same tools."""
    cfg, ledger_dir, log = primed(tmp_path)
    client = serve(cfg)
    client.initialize()
    before = [t["name"] for t in client.request("tools/list")["result"]["tools"]]
    calls = log.read_text(encoding="utf-8").count('"tools/call"')
    params = {"name": "p__wait", "arguments": {"seconds": 30}}
    client.send({"jsonrpc": "2.0", "id": 99, "method": "tools/call", "params": params})
    wait_for(lambda: log.read_text(encoding="utf-8").count('"tools/call"') > calls)
    os.kill(client.proc.pid, signal.SIGKILL)
    assert finish(client) == -signal.SIGKILL
    sent, returned = the_call(ledger_dir)
    assert returned == []  # outcome unknown, which is what a lone call.sent means
    with serve(cfg) as again:
        again.initialize()
        after = [t["name"] for t in again.request("tools/list")["result"]["tools"]]
    assert after == before and "p__wait" in after
    # The killed serve's probe saw end of input when serve's end of its pipes closed.
    wait_for(lambda: log.read_text(encoding="utf-8").count("end of input") >= 2)


@POSIX_ONLY
def test_signal_during_startup(tmp_path):
    """A signal while an upstream is still connecting starts the shutdown path at once: serve
    exits 0 without waiting for the upstream's 15 s connect timeout, and the ledger verifies.
    The slow probe is left to see end of input after the 1 s bound."""
    log = tmp_path / "probe.log"
    toml = (
        f"[upstream.p]\ncommand = {rig.toml_str(sys.executable)}\n"
        f"args = [{rig.toml_str(rig.PROBE)}]\n"
        f'env = {{ PROBE_LOG = {rig.toml_str(log)}, PROBE_DELAY = "5" }}\n'
        "connect_timeout_seconds = 15\n"
    )
    cfg = rig.serve_config(tmp_path, toml, tmp_path / "ledger")
    client = serve(cfg)
    wait_for(lambda: log.exists() and "start" in log.read_text(encoding="utf-8"))
    began = time.monotonic()
    os.kill(client.proc.pid, signal.SIGTERM)
    assert finish(client) == 0
    assert time.monotonic() - began < 10  # it needs about 1 s, the bound on closing upstreams
    ledger_dir = tmp_path / "ledger"
    data = (ledger_dir / "ledger.jsonl").read_bytes()
    assert verify_bytes(data, (ledger_dir / "ledger.head").read_bytes()).status == "intact"
    kinds = [json.loads(line)["kind"] for line in data.splitlines()]
    assert "session.started" in kinds and "upstream.connected" not in kinds
