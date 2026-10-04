"""Shared pieces for the proxy tests: an in-memory gateway, the stdio test upstreams, ledger
reading, approving what is pending (docs/PIN-SPEC.md, section 7, Existing M0 tests), and
classifying the test upstreams' tools (docs/HOLD-SPEC.md, section 11).

approve_all lives here, not in the shipped package. It runs the same library code as
`polarizer approve --group`, with actor "person".

M0 and M1a tests see no holds: rig.gateway() and rig.proxied() run with classified() unless
given policy=, and serve_config() classifies every upstream of the toml it writes. The classes
are local-read with no path arguments for the probe's and the fakes' tools, which allows every
call (HOLD-SPEC.md, section 4, row 16), and the example config's for the Filesystem server."""

import json
import os
import re
import subprocess
import sys
from contextlib import asynccontextmanager
from pathlib import Path

import anyio
from mcp import Client, StdioServerParameters

from polarizer import config
from polarizer.config import ToolRule
from polarizer.decisions import Decider
from polarizer.holds import HoldState, listener
from polarizer.pins import PinState
from polarizer.policy import Policy, UpstreamPolicy
from polarizer.proxy import Gateway
from polarizer.upstream import UpstreamSpec
from polarizer.writer import LedgerWriter

HELPERS = Path(__file__).resolve().parent
PROBE = HELPERS / "probe_server.py"
MODERN = HELPERS / "modern_server.py"
CONFIG_SHA = "c0" * 32

# Every tool name a test upstream declares: the probe's seven, the fakes' (fakes.NAMES with
# their extras) and the hostile stub's. Classified local-read with no path arguments.
PROBE_TOOLS = ("wait", "crash", "env", "fail", "rich", "invalid", "change")
FAKE_TOOLS = ("echo", "wait", "fail", "refuse", "closed", "ask", "elicit", "change", "rich",
              "rich_error", "boom")  # fmt: skip
# The reference Filesystem server's tools that take paths, as polarizer.example.toml has them.
FILESYSTEM_TOOLS = {
    "read_text_file": ToolRule("local-read", ("path",)),
    "read_media_file": ToolRule("local-read", ("path",)),
    "read_multiple_files": ToolRule("local-read", ("paths",)),
    "list_directory": ToolRule("local-read", ("path",)),
    "list_directory_with_sizes": ToolRule("local-read", ("path",)),
    "directory_tree": ToolRule("local-read", ("path",)),
    "search_files": ToolRule("local-read", ("path",)),
    "get_file_info": ToolRule("local-read", ("path",)),
    "list_allowed_directories": ToolRule("local-read"),
    "write_file": ToolRule("local-write", ("path",)),
    "edit_file": ToolRule("local-write", ("path",)),
    "create_directory": ToolRule("local-write", ("path",)),
    "move_file": ToolRule("destructive", ("source", "destination")),
}


def _declared(target) -> list[str]:
    """The tool names an upstream target declares: a FakeUpstream's own (its server knows its
    fake), or every name a test upstream can declare."""
    fake = getattr(target, "fake", None)
    names = list(fake.names) if fake is not None else []
    return [*names, *PROBE_TOOLS, *FAKE_TOOLS]


def classify_tools(names, cls: str = "local-read") -> dict:
    """{name: ToolRule(cls)} for each name, plus the Filesystem server's tools as the example
    config classifies them."""
    tools = {name: ToolRule(cls) for name in names}
    return {**tools, **FILESYSTEM_TOOLS}


def classified(specs: list[UpstreamSpec], cls: str = "local-read", **options) -> Policy:
    """The policy M0 and M1a tests run with: every tool each upstream declares has class `cls`
    and no path arguments. `options` go to Policy (workspace_roots, ledger_dir, ...)."""
    upstreams = {
        s.prefix: UpstreamPolicy(False, classify_tools(_declared(s.target), cls)) for s in specs
    }
    return Policy(upstreams=upstreams, **options)


def classify(config_path: Path, cls: str = "local-read") -> Path:
    """Add a [upstream.<p>.tools] table to each [upstream.<p>] of a polarizer.toml, giving every
    tool a test upstream declares class `cls` (and the Filesystem server's tools theirs)."""
    text = Path(config_path).read_text(encoding="utf-8")
    prefixes = re.findall(r"^\[upstream\.([A-Za-z0-9_-]+)\]\s*$", text, re.M)
    tables = []
    for prefix in prefixes:
        rows = []
        for name, rule in classify_tools([*PROBE_TOOLS, *FAKE_TOOLS], cls).items():
            args = ", ".join(json.dumps(a) for a in rule.path_args)
            rows.append(f'{name} = {{ class = "{rule.cls}", path_args = [{args}] }}')
        tables.append(f"[upstream.{prefix}.tools]\n" + "\n".join(rows) + "\n")
    Path(config_path).write_text(text.rstrip("\n") + "\n\n" + "\n".join(tables), encoding="utf-8")
    return Path(config_path)


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
async def gateway(
    ledger_dir: Path,
    specs: list[UpstreamSpec],
    approve: bool = True,
    *,
    ops=None,
    state: PinState | None = None,
    holds: HoldState | None = None,
    policy: Policy | None = None,
    **options,
):
    """A started Gateway on a fresh writer; the writer is closed afterwards. With approve
    (the default), every tool its startup saw is approved before it is handed over, so M0
    tests see M0's tools. Pin tests pass approve=False. `ops` is the writer's file layer,
    `state` and `holds` the folds it feeds, `policy` the gateway's (classified() by default,
    so nothing is held), and `options` go to Gateway (watch_interval, notice_interval, retry,
    hold_watch_interval, hold_timeout)."""
    state = state if state is not None else PinState()
    holds = holds if holds is not None else HoldState()
    policy = policy if policy is not None else classified(specs)
    writer = LedgerWriter.open(ledger_dir, on_entry=listener(state.apply, holds.apply), ops=ops)
    gw = Gateway(
        specs, writer, config_sha256=CONFIG_SHA, pins=state, holds=holds, policy=policy, **options
    )
    try:
        async with gw.running():
            if approve:
                await approve_pending(gw)
            yield gw
    finally:
        writer.close()


@asynccontextmanager
async def proxied(
    ledger_dir: Path,
    specs: list[UpstreamSpec],
    mode: str = "auto",
    approve: bool = True,
    message_handler=None,
    **options,
):
    """(client, gateway): an SDK client connected in memory to a started gateway. `options`
    go to gateway()."""
    async with gateway(ledger_dir, specs, approve, **options) as gw:
        async with Client(gw.server, mode=mode, message_handler=message_handler) as client:
            yield client, gw


async def next_notice(subscription, seconds: float = 10):
    """The next event on a client's listen subscription, waiting at most `seconds`. A notice
    can need about 2 s: up to 1 s for the watch, and up to 1 s more for the once-a-second
    limit on notices."""
    with anyio.fail_after(seconds):
        return await subscription.__anext__()


async def no_notice(subscription, seconds: float = 1.5) -> bool:
    """True if no event arrives on a listen subscription within `seconds`."""
    with anyio.move_on_after(seconds):
        await subscription.__anext__()
        return False
    return True


async def until(condition, seconds: float = 5, step: float = 0.01):
    """Wait until condition() is true, at most `seconds`."""
    with anyio.fail_after(seconds):
        while not condition():
            await anyio.sleep(step)


def entries(ledger_dir: Path) -> list[dict]:
    raw = (Path(ledger_dir) / "ledger.jsonl").read_bytes()
    return [json.loads(line) for line in raw.splitlines()]


def kinds(ledger_dir: Path, kind: str) -> list[dict]:
    return [e for e in entries(ledger_dir) if e["kind"] == kind]


def serve_config(tmp: Path, upstreams: str, ledger_dir: Path, classified: bool = True) -> Path:
    """Write a polarizer.toml with ledger_dir and the given [upstream.*] tables, every test
    upstream's tools classified (classify()) unless `classified` is False."""
    path = tmp / "polarizer.toml"
    path.write_text(f'ledger_dir = "{ledger_dir.as_posix()}"\n\n{upstreams}', encoding="utf-8")
    return classify(path) if classified else path


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


# Holds (docs/HOLD-SPEC.md) -------------------------------------------------------------------


def held(specs: list[UpstreamSpec], **classes) -> Policy:
    """classified(specs), with the named tools given other classes: held(specs, echo="egress")
    holds every call to echo."""
    policy = classified(specs)
    for upstream in policy.upstreams.values():
        for name, cls in classes.items():
            upstream.tools[name] = ToolRule(cls)
    return policy


def decide_hold(ledger_dir: Path, hold_id: str, decision: str = "allow", reason=None, ops=None):
    """`polarizer allow` or `deny` through the library: (stdout lines, stderr lines). Raises
    decisions.Refusal as the command would refuse."""
    from datetime import UTC, datetime

    from polarizer.decisions import HoldDecider

    decider = HoldDecider.open(ledger_dir, ops=ops)
    out, err = [], []
    try:
        decider.decide(hold_id, decision, reason, out.append, err.append, datetime.now(UTC))
    finally:
        decider.close()
    return out, err


def hold_ids(ledger_dir: Path) -> list[str]:
    """The hold ids of every hold.created, in seq order."""
    return [e["data"]["hold"] for e in kinds(ledger_dir, "hold.created")]


async def holds_created(ledger_dir: Path, n: int, seconds: float = 10) -> list[str]:
    """Wait until the ledger has at least n hold.created entries; their ids."""
    await until(lambda: len(hold_ids(ledger_dir)) >= n, seconds)
    return hold_ids(ledger_dir)
