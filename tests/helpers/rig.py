"""Shared pieces for the proxy tests: an in-memory gateway, the stdio test upstreams, and
ledger reading."""

import json
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from mcp import Client, StdioServerParameters

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


@asynccontextmanager
async def gateway(ledger_dir: Path, specs: list[UpstreamSpec]):
    """A started Gateway on a fresh writer; the writer is closed afterwards."""
    writer = LedgerWriter.open(ledger_dir)
    gw = Gateway(specs, writer, config_sha256=CONFIG_SHA)
    try:
        async with gw.running():
            yield gw
    finally:
        writer.close()


@asynccontextmanager
async def proxied(ledger_dir: Path, specs: list[UpstreamSpec], mode: str = "auto"):
    """(client, gateway): an SDK client connected in memory to a started gateway."""
    async with gateway(ledger_dir, specs) as gw:
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


def serve_params(config: Path, env: dict | None = None) -> StdioServerParameters:
    """`python -m polarizer serve --config <config>` as a stdio server."""
    return StdioServerParameters(
        command=sys.executable, args=["-m", "polarizer", "serve", "--config", str(config)], env=env
    )
