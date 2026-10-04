"""Shared pieces for the proxy tests: an in-memory gateway, the stdio test upstreams, ledger
reading, and approving what is pending (docs/PIN-SPEC.md, section 7, Existing M0 tests).

approve_all lives here, not in the shipped package. It runs the same library code as
`polarizer approve --group`, with actor "person"."""

import json
import os
import subprocess
import sys
from contextlib import asynccontextmanager
from pathlib import Path

import anyio
from mcp import Client, StdioServerParameters

from polarizer import config
from polarizer.decisions import Decider
from polarizer.pins import PinState
from polarizer.proxy import Gateway
from polarizer.upstream import UpstreamSpec
from polarizer.writer import LedgerWriter

HELPERS = Path(__file__).resolve().parent
PROBE = HELPERS / "probe_server.py"
MODERN = HELPERS / "modern_server.py"
CONFIG_SHA = "c0" * 32


def probe(env: dict | None = None) -> StdioServerParameters:
    """The stdlib probe (2025-11-25) as a stdio upstream."""
    return StdioServerParameters(command=sys.executable, args=[str(PROBE)], env=env)


def modern(env: dict | None = None) -> StdioServerParameters:
    """The SDK fake (2026-07-28) as a stdio upstream."""
    return StdioServerParameters(command=sys.executable, args=[str(MODERN)], env=env)


def spec(prefix: str, target, timeout: float = 10, mode: str = "auto") -> UpstreamSpec:
    return UpstreamSpec(prefix=prefix, target=target, connect_timeout=timeout, mode=mode)


def approve_all(ledger_dir: Path, upstream: str | None = None) -> int:
    """Approve the group `polarizer pending` would print now (the new definitions of tools
    with no decision), as `polarizer approve --group` does. Returns how many were approved."""
    decider = Decider.open(ledger_dir)
    try:
        members, group_id = decider.pending_group(upstream)
        if group_id is None:
            return 0
        errors = []
        code = decider.approve_group(group_id, upstream, lambda line: None, errors.append)
        assert code == 0, errors
        return len(members)
    finally:
        decider.close()


def approve_changed(ledger_dir: Path) -> int:
    """Approve, one by one as `polarizer approve <prefix> <tool> <hash>` does, every definition
    a group leaves out: changed ones, new ones after a rejection, and the definitions of a tool
    with more than one waiting. They go in listing order, so for a tool with several, the most
    recently seen becomes its latest decision."""
    from polarizer.pins import blocks

    decider = Decider.open(ledger_dir)
    try:
        found = [b for b in blocks(decider.pins, ledger_dir) if b.kind != "unservable"]
        todo = [b for b in found if (b.decided or b.several) and b.copy is not None]
        for b in todo:
            decider.approve_one(b.upstream, b.tool, b.def_hash, lambda line: None)
        return len(todo)
    finally:
        decider.close()


async def approve_pending(gw: Gateway, changed: bool = False) -> None:
    """Approve what is pending while `gw` runs, then let it catch up with the ledger, so its
    exposed list is complete when this returns."""
    ledger_dir = gw.writer.ledger_dir
    await anyio.to_thread.run_sync(approve_all, ledger_dir)
    if changed:
        await anyio.to_thread.run_sync(approve_changed, ledger_dir)
    await gw.catch_up()


@asynccontextmanager
async def gateway(ledger_dir: Path, specs: list[UpstreamSpec], approve: bool = True):
    """A started Gateway on a fresh writer; the writer is closed afterwards. With approve
    (the default), every tool its startup saw is approved before it is handed over, so M0
    tests see M0's tools. Pin tests pass approve=False."""
    state = PinState()
    writer = LedgerWriter.open(ledger_dir, on_entry=state.apply)
    gw = Gateway(specs, writer, config_sha256=CONFIG_SHA, pins=state)
    try:
        async with gw.running():
            if approve:
                await approve_pending(gw)
            yield gw
    finally:
        writer.close()


@asynccontextmanager
async def proxied(
    ledger_dir: Path, specs: list[UpstreamSpec], mode: str = "auto", approve: bool = True
):
    """(client, gateway): an SDK client connected in memory to a started gateway."""
    async with gateway(ledger_dir, specs, approve) as gw:
        async with Client(gw.server, mode=mode) as client:
            yield client, gw


def entries(ledger_dir: Path) -> list[dict]:
    raw = (Path(ledger_dir) / "ledger.jsonl").read_bytes()
    return [json.loads(line) for line in raw.splitlines()]


def kinds(ledger_dir: Path, kind: str) -> list[dict]:
    return [e for e in entries(ledger_dir) if e["kind"] == kind]


def serve_config(tmp: Path, upstreams: str, ledger_dir: Path) -> Path:
    """Write a polarizer.toml with ledger_dir and the given [upstream.*] tables."""
    path = tmp / "polarizer.toml"
    path.write_text(f'ledger_dir = "{ledger_dir.as_posix()}"\n\n{upstreams}', encoding="utf-8")
    return path


def toml_str(value) -> str:
    """A TOML basic string for a path or text (JSON's escapes are valid TOML)."""
    return json.dumps(str(value))


def run_serve(
    config_path: Path, env: dict | None = None, timeout: float = 120
) -> subprocess.CompletedProcess:
    """`python -m polarizer serve --config <config>` with stdin closed: it records what its
    upstreams list, then exits (docs/PIN-SPEC.md, section 8, End of input during startup).
    `env` adds to this process's environment."""
    return subprocess.run(
        [sys.executable, "-m", "polarizer", "serve", "--config", str(config_path)],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        timeout=timeout,
        env={**os.environ, **(env or {})},
    )


def prime(config_path: Path, env: dict | None = None) -> Path:
    """Prime the ledger of a polarizer.toml for the stdio tests: run serve with stdin closed,
    then approve_all, so the next serve starts with every tool approved. Returns ledger_dir."""
    done = run_serve(config_path, env)
    assert done.returncode == 0, done.stderr.decode("utf-8", "replace")
    ledger_dir = config.load(config_path, require_env=False).ledger_dir
    approve_all(ledger_dir)
    return ledger_dir


def serve_params(config: Path, env: dict | None = None) -> StdioServerParameters:
    """`python -m polarizer serve --config <config>` as a stdio server."""
    return StdioServerParameters(
        command=sys.executable, args=["-m", "polarizer", "serve", "--config", str(config)], env=env
    )
