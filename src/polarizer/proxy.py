"""The gateway: one MCP server in front of the upstreams (docs/PROXY-SPEC.md, and
docs/PIN-SPEC.md for pins).

Tools only. The server registers tools/list, tools/call and subscriptions/listen, and nothing
else. Every call is recorded: call.sent before it is forwarded and call.returned after, or one
call.refused for a name that matches no exposed tool. Arguments go to side files; results
never enter the ledger.

Pins: every listing is hashed, its copies stored and its observations recorded; only tools whose
latest decision approves their live hash are exposed, each served from its stored copy. Pin
state comes from the ledger alone (polarizer.pins). The gateway catches up with the ledger once
a second (the watch) and at the start of every client tools/list and tools/call, so a decision
made by another process applies within about a second.

In motion (docs/PIN-SPEC.md, sections 6 and 8): an upstream's change notice refreshes that
upstream (one refresh at a time, and notices during one cause exactly one more); a refresh that
fails is retried on a timer; and clients are told when the exposed list changes, at most once a
second, never just because an upstream said something changed.
"""

import asyncio
import json
import secrets
import threading
import time
from collections.abc import Mapping
from contextlib import asynccontextmanager

import anyio
import mcp_types as types
import pydantic
from mcp import MCPError
from mcp.server.lowlevel import NotificationOptions, Server
from mcp.server.subscriptions import InMemorySubscriptionBus, ListenHandler
from mcp.shared.subscriptions import ToolsListChanged
from mcp_types import CLIENT_INFO_META_KEY, CONNECTION_CLOSED, PROTOCOL_VERSION_META_KEY

from polarizer import __version__, defhash, pins
from polarizer.canon import MAX_INT, EntryRefused
from polarizer.pins import PinState
from polarizer.sidefiles import write_args
from polarizer.upstream import SEPARATOR, Upstream, UpstreamSpec, clip, describe, log, one_line
from polarizer.writer import LedgerWriter, Stopped

FORWARDED_META = ("traceparent", "tracestate")
RESERVED_META_PREFIX = "io.modelcontextprotocol/"
PROGRESS_TOKEN_KEYS = ("progressToken", "progress_token")
CLIENT_CALL_ID = "claudecode/toolUseId"
MAX_DROPPED = 32  # meta_dropped keeps at most this many key names, each cut to 128 bytes
# The client's line when the SDK can't parse an upstream's result. The SDK's own message quotes
# the result, which must not reach the ledger.
UNREADABLE = "result did not match the MCP schema"
# call.returned's error when Polarizer's own shutdown cancelled the call (PIN-SPEC.md, section 8),
# and when the client's notifications/cancelled did.
SHUTDOWN_ERROR = "polarizer shut down during the call"
CLIENT_CANCEL_ERROR = "the client cancelled the call"
WATCH_INTERVAL = 1.0  # seconds between catch-ups with the ledger
NOTICE_INTERVAL = 1.0  # at most one change notice to clients per this many seconds
RETRY = (30.0, 300.0)  # the retry timer after a failed refresh: first interval, and the cap
INPUT_KINDS = {
    "elicitation/create": "elicitation",
    "sampling/createMessage": "sampling",
    "roots/list": "roots",
}


class _ProxyServer(Server):
    """A low-level Server whose initialization options always advertise tools.listChanged in
    the older era, however it is run (stdio, or the SDK's in-memory transport in tests)."""

    def create_initialization_options(self, notification_options=None, *args, **kwargs):
        options = notification_options or NotificationOptions(tools_changed=True)
        return super().create_initialization_options(options, *args, **kwargs)


def _text_result(line: str) -> types.CallToolResult:
    return types.CallToolResult(content=[types.TextContent(text=line)], is_error=True)


def _leaf(error: BaseException, kind: type) -> bool:
    """True if `error`, or the first leaf of an exception group, is a `kind`."""
    while isinstance(error, BaseExceptionGroup) and error.exceptions:
        error = error.exceptions[0]
    return isinstance(error, kind)


def _dump(tools: list[types.Tool]) -> list[dict]:
    return [t.model_dump(by_alias=True, mode="json", exclude_none=True) for t in tools]


def result_bytes(result: types.Result) -> int:
    dumped = result.model_dump(by_alias=True, mode="json", exclude_none=True)
    text = json.dumps(dumped, separators=(",", ":"), ensure_ascii=False)
    return len(text.encode("utf-8", "surrogatepass"))


class Gateway:
    """The proxy for one Polarizer process: its upstreams, its ledger session and its server.

    `pins` must be the PinState the writer folded at open (LedgerWriter.open(on_entry=...)),
    so it reflects the whole ledger. Without one, the gateway starts from an empty state.
    """

    def __init__(
        self,
        specs: list[UpstreamSpec],
        writer: LedgerWriter,
        *,
        config_sha256: str,
        pins: PinState | None = None,
        watch_interval: float = WATCH_INTERVAL,
        notice_interval: float = NOTICE_INTERVAL,
        retry: tuple[float, float] = RETRY,
    ):
        self.writer = writer
        self.watch_interval = watch_interval
        self.notice_interval = notice_interval
        self.retry_first, self.retry_max = retry
        self.sleep = anyio.sleep  # the retry timer's sleep; a test replaces it to see intervals
        self.config_sha256 = config_sha256
        self.session = secrets.token_hex(8)
        self.pins = pins if pins is not None else PinState()
        self.upstreams = {
            spec.prefix: Upstream(spec, self._upstream_changed, self._upstream_lost)
            for spec in specs
        }
        # From each upstream's latest successful listing: its tool name -> live hash, or None
        # when the definition can't be served at all: it can't be hashed or is too large (the
        # problem is in _live_problem).
        self._live: dict[str, dict[str, str | None]] = {}
        self._live_problem: dict[tuple[str, str], str] = {}
        self._unservable_recorded: set = set()  # (prefix, tool, def_hash, problem), per process
        self._failing: set[str] = set()  # prefixes in a run of failed refreshes, recorded
        self._observe_lock = anyio.Lock()
        self._stop_announced = False
        self.shutting_down = False
        self._started = False  # startup finished: notices and the timers may run
        self._background = None  # the task group of the watch, notices, refreshes and retries
        self._deferred: set[str] = set()  # upstreams whose notice came during startup
        self._notice_refreshing: set[str] = set()
        self._notice_again: set[str] = set()
        self._retrying: set[str] = set()
        self._announced: list[dict] | None = None  # the exposed list clients last got
        self._dirty = anyio.Event()  # set when the exposed list may have changed
        self.bus = InMemorySubscriptionBus()
        self.server = _ProxyServer(
            "polarizer",
            version=__version__,
            on_list_tools=self._list_tools,
            on_call_tool=self._call_tool,
            on_subscriptions_listen=ListenHandler(self.bus),
        )
        self.server.middleware.append(self._client_middleware)
        self._client_lock = threading.Lock()
        self._client_recorded = False
        self._legacy_sessions: list = []

    # Startup ---------------------------------------------------------------------------

    @asynccontextmanager
    async def running(self):
        """start() in a task group of its own, then serve until the block exits."""
        async with anyio.create_task_group() as tasks:
            await self.start(tasks)
            try:
                yield self
            finally:
                tasks.cancel_scope.cancel()

    async def start(self, tasks) -> None:
        """Startup (PROXY-SPEC.md, steps 4 and 5): append session.started, connect every
        upstream in parallel, append one upstream.connected per upstream, and observe each
        listing. Then start the background work: the watch, change notices, notice-driven
        refreshes and the retry timer. Upstream tasks and the background run in `tasks`."""
        await self._append(
            "session.started",
            {
                "session": self.session,
                "polarizer_version": __version__,
                "config_sha256": self.config_sha256,
            },
        )
        for upstream in self.upstreams.values():
            upstream.started = True
            tasks.start_soon(upstream.run)
        async with anyio.create_task_group() as waits:
            for upstream in self.upstreams.values():
                waits.start_soon(upstream.wait_ready)
        for upstream in self.upstreams.values():
            await self._append("upstream.connected", self._connected_data(upstream))
        for upstream in self.upstreams.values():
            if upstream.list_known:
                await self._observe(upstream)
        waiting = self._count_waiting()
        if waiting:
            log(f"polarizer: {waiting} tools wait for approval; run polarizer pending")
        self._announced = await self._exposed_dump()
        self._background = await tasks.start(self._run_background)
        self._started = True
        for prefix in sorted(self._deferred):
            await self._upstream_changed(self.upstreams[prefix])
        self._deferred.clear()

    async def _run_background(self, *, task_status=anyio.TASK_STATUS_IGNORED) -> None:
        async with anyio.create_task_group() as background:
            background.start_soon(self._watch)
            background.start_soon(self._notifier)
            task_status.started(background)
            await anyio.sleep_forever()

    # Shutdown (PIN-SPEC.md, section 8; cli.serve drives it) ---------------------------

    def begin_shutdown(self) -> None:
        """From now on a cancelled call records SHUTDOWN_ERROR, and the background work
        (the watch, notices, refreshes and retries) stops."""
        self.shutting_down = True
        if self._background is not None:
            self._background.cancel_scope.cancel()

    async def close_upstreams(self, bound: float) -> bool:
        """Close every upstream client in parallel, waiting at most `bound` seconds in total.
        Returns True if all of them finished; an upstream still closing is left to see end of
        input when Polarizer's pipes close."""
        started = [u for u in self.upstreams.values() if u.started]
        for upstream in started:
            upstream.close()
        with anyio.move_on_after(bound):
            for upstream in started:
                await upstream.closed.wait()
        return all(u.closed.is_set() for u in started)

    def _connected_data(self, upstream: Upstream) -> dict:
        if not upstream.connected:
            log(f"polarizer: upstream {upstream.prefix} did not connect: {upstream.error}")
            return {"prefix": upstream.prefix, "error": clip(upstream.error)}
        skipped, budget = [], 8192
        for name in upstream.skipped:
            name = clip(name, 256)
            budget -= len(name.encode("utf-8")) + 3
            if budget < 0:
                left = len(upstream.skipped) - len(skipped)
                log(
                    f"polarizer: upstream {upstream.prefix}: {left} more skipped names not recorded"
                )
                break
            skipped.append(name)
        for name in upstream.skipped:
            log(f"polarizer: upstream {upstream.prefix}: skipped tool {name!r}: name not allowed")
        return {
            "prefix": upstream.prefix,
            "protocol_version": clip(upstream.protocol_version, 64),
            "tools": len(upstream.tools),
            "skipped_tools": skipped,
        }

    # The ledger ------------------------------------------------------------------------

    async def _append(self, kind: str, data: dict):
        """Append one entry. A ledger that stopped, or an entry that can't be written, is
        reported on stderr and returns None; it never takes the gateway down."""
        try:
            return await self.writer.append_async(kind, data)
        except (Stopped, EntryRefused) as e:
            log(f"polarizer: could not record {kind}: {e}")
            return None

    async def _client_middleware(self, ctx, call_next):
        """session.client, written once per process: from the first initialize (older era)
        or the first request whose _meta names the client (2026-07-28)."""
        params = ctx.params if isinstance(ctx.params, Mapping) else {}
        if ctx.method == "initialize":
            result = await call_next(ctx)
            self._legacy_sessions.append(ctx.session)
            version = result.get("protocolVersion") if isinstance(result, Mapping) else None
            await self._client_seen(params.get("clientInfo"), version)
            return result
        meta = params.get("_meta")
        if (
            ctx.request_id is not None
            and isinstance(meta, Mapping)
            and CLIENT_INFO_META_KEY in meta
        ):
            await self._client_seen(
                meta.get(CLIENT_INFO_META_KEY), meta.get(PROTOCOL_VERSION_META_KEY)
            )
        return await call_next(ctx)

    async def _client_seen(self, info, version) -> None:
        info = info if isinstance(info, Mapping) else {}

        def text(value, limit=256):
            return clip(value, limit) if isinstance(value, str) else None

        data = {
            "session": self.session,
            "client_name": text(info.get("name")),
            "client_version": text(info.get("version")),
            "protocol_version": text(version, 64),
        }
        # The flag is set before the write, under one lock, and the entry is queued before
        # the lock is released, so exactly one request writes it.
        with self._client_lock:
            if self._client_recorded:
                return
            self._client_recorded = True
            future = self.writer.append("session.client", data)
        try:
            await asyncio.wrap_future(future)
        except (Stopped, EntryRefused) as e:
            log(f"polarizer: could not record session.client: {e}")

    # Change notices --------------------------------------------------------------------

    async def _notify_clients(self) -> None:
        """Tell clients their tool list changed: on the listen bus for 2026-07-28 clients, and
        as notifications/tools/list_changed to older-era clients."""
        await self.bus.publish(ToolsListChanged())
        for session in list(self._legacy_sessions):
            try:
                await session.send_tool_list_changed()
            except Exception:
                self._legacy_sessions.remove(session)

    def _changed(self) -> None:
        """The exposed list may have changed: the notifier compares and tells clients."""
        self._dirty.set()

    async def _exposed_dump(self) -> list[dict]:
        """The exposed list as clients receive it, for comparing with what they last got."""
        return _dump(await self.exposed())

    async def _notifier(self) -> None:
        """Tell clients when the exposed list differs from what they last got, at most once
        per notice_interval: a change inside that interval is folded into one notice at its
        end. A client's own tools/list counts as being told."""
        last = None
        while True:
            await self._dirty.wait()
            if last is not None:
                wait = last + self.notice_interval - anyio.current_time()
                if wait > 0:
                    await anyio.sleep(wait)
            self._dirty = anyio.Event()  # changes from here on wake the next round
            current = await self._exposed_dump()
            if current != self._announced:
                self._announced = current
                last = anyio.current_time()
                await self._notify_clients()

    async def _watch(self) -> None:
        """Catch up with the ledger once every watch_interval, so another process's decision
        applies within about that long (PIN-SPEC.md, section 8)."""
        while True:
            await anyio.sleep(self.watch_interval)
            await self._catch_up()

    async def _upstream_changed(self, upstream: Upstream) -> None:
        """An upstream's change notice: refresh that upstream, one refresh at a time; notices
        during one cause exactly one more. Clients hear about it only if the exposed list
        changes. Called from the upstream's own tasks, so it never waits for the refresh."""
        prefix = upstream.prefix
        if self.shutting_down:
            return
        if not self._started:
            self._deferred.add(prefix)  # refreshed once startup is done
            return
        if prefix in self._notice_refreshing:
            self._notice_again.add(prefix)
            return
        self._notice_refreshing.add(prefix)
        self._background.start_soon(self._notice_refresh, upstream)

    async def _notice_refresh(self, upstream: Upstream) -> None:
        prefix = upstream.prefix
        try:
            while True:
                self._notice_again.discard(prefix)
                await self._refresh_one(upstream, "upstream-notice")
                if prefix not in self._notice_again:
                    return
        finally:
            self._notice_refreshing.discard(prefix)

    async def _refresh_one(self, upstream: Upstream, trigger: str | None) -> bool:
        """List one upstream again and observe it, or record the failure (unless `trigger` is
        None: the retry timer, whose failures are never the first of a run) and start the
        retry timer. Returns True on success."""
        ok = await upstream.refresh()
        if ok:
            await self._observe(upstream)
        elif upstream.client is not None:
            if trigger is not None:
                await self._refresh_failed(upstream, trigger, upstream.list_error or "failed")
            self._start_retry(upstream)
        self._changed()
        return ok

    def _start_retry(self, upstream: Upstream) -> None:
        """The retry timer for a refresh that failed (not for a lost connection, which is
        never retried): PIN-SPEC.md, section 6."""
        prefix = upstream.prefix
        if prefix in self._retrying or self._background is None or self.shutting_down:
            return
        self._retrying.add(prefix)
        self._background.start_soon(self._retry, upstream)

    async def _retry(self, upstream: Upstream) -> None:
        interval = self.retry_first
        try:
            while True:
                await self.sleep(interval)
                if upstream.client is None or upstream.list_error is None:
                    return  # lost, or another trigger's refresh succeeded
                if await self._refresh_one(upstream, None):
                    return  # the next run of failures starts at retry_first again
                interval = min(interval * 2, self.retry_max)
        finally:
            self._retrying.discard(upstream.prefix)

    # Pins --------------------------------------------------------------------------------

    async def _catch_up(self) -> None:
        """Adopt what other processes appended (decisions in particular). A ledger that changed
        under the writer stops it: nothing is exposed after that, and clients are told once."""
        if not self.writer.stopped:
            try:
                if await self.writer.catch_up_async():
                    self._changed()
            except Stopped:
                pass
        await self._check_stopped()

    async def _check_stopped(self) -> None:
        if self.writer.stopped and not self._stop_announced:
            self._stop_announced = True
            await self._notify_clients()

    async def _upstream_lost(self, upstream: Upstream) -> None:
        why = upstream.lost or "connection closed"
        await self._refresh_failed(upstream, "connection-lost", why)
        self._changed()

    async def _refresh_failed(self, upstream: Upstream, trigger: str, error: str) -> None:
        """Record the first failure of a run of failed refreshes; the next success ends it."""
        if upstream.prefix in self._failing:
            return
        self._failing.add(upstream.prefix)
        data = {
            "session": self.session,
            "prefix": upstream.prefix,
            "trigger": trigger,
            "error": one_line(error),
        }
        await self._append("upstream.refresh_failed", data)

    async def _unservable(self, prefix: str, tool: str, def_hash: str | None, problem: str):
        problem = one_line(problem)
        key = (prefix, tool, def_hash, problem)
        if key in self._unservable_recorded:
            return
        self._unservable_recorded.add(key)
        data = {
            "session": self.session,
            "upstream": prefix,
            "tool": tool,
            "def_hash": def_hash,
            "problem": problem,
        }
        await self._append("tool.unservable", data)

    async def _observe(self, upstream: Upstream) -> None:
        """After a successful listing: hash each tool, store copies that are missing, and record
        what PIN-SPEC.md section 3 asks for, in list order, one upstream at a time."""
        async with self._observe_lock:
            if self.writer.stopped:
                return
            prefix = upstream.prefix
            self._failing.discard(prefix)
            live: dict[str, str | None] = {}
            for name, tool in upstream.tools.items():
                try:
                    def_hash, canon = defhash.definition(tool)
                except defhash.Unhashable as e:
                    live[name] = None
                    problem = f"cannot be hashed: {e}"
                    self._live_problem[(prefix, name)] = problem
                    await self._unservable(prefix, name, None, problem)
                    continue
                if len(canon) > defhash.MAX_DEFINITION_BYTES:
                    # Never stored, seen or counted toward the cap; recorded once, hidden.
                    live[name] = None
                    self._live_problem[(prefix, name)] = defhash.TOO_LARGE
                    await self._unservable(prefix, name, def_hash, defhash.TOO_LARGE)
                    continue
                live[name] = def_hash
                tp = self.pins.get(prefix, name)
                action = pins.what_to_record(tp, def_hash)
                if action == "cap":
                    await self._unservable(prefix, name, def_hash, pins.CAP_PROBLEM)
                    continue
                if pins.state(tp, def_hash)[1] == pins.CAP_PROBLEM:
                    continue  # beyond the cap: neither stored nor recorded
                try:
                    defhash.write_copy(self.writer.ledger_dir, def_hash, canon)
                except OSError as e:
                    why = e.strerror or describe(e)
                    await self._unservable(
                        prefix, name, def_hash, f"stored copy could not be written: {why}"
                    )
                base = {"session": self.session, "upstream": prefix, "tool": name}
                if action == "seen":
                    await self._append("tool.seen", {**base, "def_hash": def_hash})
                elif action == "drift":
                    drift = {"approved_hash": tp.decided_hash, "live_hash": def_hash}
                    await self._append("tool.drift", {**base, **drift})
            self._live[prefix] = live

    async def _tool_state(self, prefix: str, name: str) -> tuple[str, str | None, dict | None]:
        """(state, problem, stored copy) for a listed tool of an upstream whose list is known.
        An approved tool's copy is read and rehashed every time."""
        live = self._live.get(prefix, {}).get(name)
        if live is None:
            return "unservable", self._live_problem.get((prefix, name), "cannot be hashed"), None
        st, problem = pins.state(self.pins.get(prefix, name), live)
        if st != "approved":
            return st, problem, None
        try:
            obj = defhash.read_copy(self.writer.ledger_dir, live)
        except defhash.CopyProblem as e:
            problem = f"stored copy {e}"
            await self._unservable(prefix, name, live, problem)
            return "unservable", problem, None
        return "approved", None, obj

    def _listed(self, upstream: Upstream) -> list[str]:
        """The tools of an upstream whose list is known, in its order; [] otherwise."""
        if not upstream.list_known or upstream.prefix not in self._live:
            return []
        return list(upstream.tools)

    def _count_waiting(self) -> int:
        """Listed tools that wait for a decision: pending or changed."""
        count = 0
        for upstream in self.upstreams.values():
            for name in self._listed(upstream):
                live = self._live[upstream.prefix].get(name)
                if live is not None:
                    st, _ = pins.state(self.pins.get(upstream.prefix, name), live)
                    count += st in ("pending", "changed")
        return count

    async def exposed(self) -> list[types.Tool]:
        """The tools served now: approved ones, from their stored copies, in config order of
        upstreams and each upstream's own order. Nothing while the writer is stopped."""
        if self.writer.stopped:
            return []
        tools = []
        for upstream in self.upstreams.values():
            for name in self._listed(upstream):
                st, _, obj = await self._tool_state(upstream.prefix, name)
                if st == "approved":
                    tools.append(defhash.served(obj, f"{upstream.prefix}{SEPARATOR}{name}"))
        return tools

    async def catch_up(self) -> None:
        """Adopt decisions other processes wrote. Called before every client tools/list and
        tools/call; tests call it directly after approving."""
        await self._catch_up()

    # tools/list ------------------------------------------------------------------------

    async def _list_tools(self, ctx, params) -> types.ListToolsResult:
        await self._catch_up()
        if self.writer.stopped:
            return types.ListToolsResult(tools=[])
        live = [u for u in self.upstreams.values() if u.connected and u.client is not None]
        results: dict[str, bool] = {}

        async def refresh(upstream):
            results[upstream.prefix] = await upstream.refresh()

        async with anyio.create_task_group() as tasks:
            for upstream in live:
                tasks.start_soon(refresh, upstream)
        for upstream in live:  # observed in config order, so records come in a stable order
            if results.get(upstream.prefix):
                await self._observe(upstream)
            elif upstream.client is not None:
                await self._refresh_failed(upstream, "client-list", upstream.list_error or "failed")
                self._start_retry(upstream)
        await self._check_stopped()
        tools = await self.exposed()
        # The response tells this client the list; no notice is needed for what it shows.
        self._announced = _dump(tools)
        return types.ListToolsResult(tools=tools)

    # tools/call ------------------------------------------------------------------------

    async def _route(self, name: str) -> tuple[Upstream | None, str, str | None]:
        """(upstream, the upstream's tool name, None), or (None, the refusal reason, the
        client's <why>; None for M0's refusals, which say there is no such tool)."""
        if SEPARATOR not in name:
            return None, "not a prefixed name", None
        prefix, tool = name.split(SEPARATOR, 1)
        upstream = self.upstreams.get(prefix)
        if upstream is None:
            return None, f'no upstream with prefix "{clip(prefix, 256)}"', None
        if not upstream.connected:
            return None, f"upstream {prefix} did not connect", None
        if not upstream.list_known or prefix not in self._live:
            reason = f"upstream {prefix} tool list could not be refreshed"
            return None, reason, "its server's tool list could not be checked"
        if tool not in upstream.tools:
            return None, f'upstream {prefix} has no tool "{clip(tool, 512)}"', None
        st, problem, _ = await self._tool_state(prefix, tool)
        if st != "approved":
            reason, why = pins.REASONS[st]
            reason = reason.format(problem=problem)
            return None, f'upstream {prefix} tool "{clip(tool, 512)}" {reason}', why
        return upstream, tool, None

    async def _call_tool(self, ctx, params: types.CallToolRequestParams):
        await self._catch_up()
        if self.writer.stopped:
            log(f"polarizer: refused {params.name}: could not record it: {self.writer.stopped}")
            return _text_result(
                f"polarizer: {params.name} was not called: the ledger could not record it"
            )
        upstream, tool, why = await self._route(params.name)
        if upstream is None:
            await self._append(
                "call.refused", {"session": self.session, "tool": clip(params.name), "reason": tool}
            )
            if why is None:
                return _text_result(f"polarizer: no tool named {clip(params.name, 4096)}")
            return _text_result(f"polarizer: {clip(params.name, 4096)} is not available: {why}")

        meta = dict(params.meta or {})
        forward = {key: meta[key] for key in FORWARDED_META if key in meta}
        dropped = sorted(
            key
            for key in meta
            if key not in FORWARDED_META
            and key not in PROGRESS_TOKEN_KEYS
            and not key.startswith(RESERVED_META_PREFIX)
        )
        call_id = meta.get(CLIENT_CALL_ID)
        arguments = params.arguments
        try:
            # Shielded: once started, the side file and call.sent are written even if the
            # client cancels meanwhile; the cancel then lands on the upstream call below.
            with anyio.CancelScope(shield=True):
                commit = write_args(self.writer.ledger_dir, arguments)
                sent = await self.writer.append_async(
                    "call.sent",
                    {
                        "session": self.session,
                        "tool": params.name,
                        "args_commit": commit,
                        "meta_dropped": [clip(k, 128) for k in dropped[:MAX_DROPPED]],
                        "client_call_id": clip(call_id, 256) if isinstance(call_id, str) else None,
                    },
                )
        except (Stopped, EntryRefused, OSError) as e:
            log(f"polarizer: refused {params.name}: could not record it: {describe(e)}")
            return _text_result(
                f"polarizer: {params.name} was not called: the ledger could not record it"
            )

        async def relay(progress, total, message):
            await ctx.session.report_progress(progress, total, message)

        returned = {"call_seq": sent.seq}
        start = time.monotonic_ns()

        def finish(outcome: str, size: int, error: str | None = None, code=None) -> dict:
            returned["outcome"] = outcome
            returned["latency_ms"] = (time.monotonic_ns() - start) // 1_000_000
            returned["result_bytes"] = size
            if error is not None:
                returned["error"] = one_line(error)
            if code is not None:
                returned["code"] = code if -MAX_INT <= code <= MAX_INT else str(code)
            return returned

        try:
            client = upstream.client
            if client is None:
                raise ConnectionError(upstream.lost or "connection closed")
            result = await client.session.call_tool(
                tool,
                arguments,
                progress_callback=relay,
                meta=forward or None,
                allow_input_required=True,
            )
        except anyio.get_cancelled_exc_class():
            # Shutdown sets its flag before it cancels anything, so a cancel that came first
            # (the client's notifications/cancelled) keeps the client's text.
            error = SHUTDOWN_ERROR if self.shutting_down else CLIENT_CANCEL_ERROR
            with anyio.CancelScope(shield=True):
                await self._append("call.returned", finish("cancelled", 0, error))
            raise
        except MCPError as e:
            if e.code != CONNECTION_CLOSED:
                await self._append("call.returned", finish("protocol-error", 0, e.message, e.code))
                raise MCPError(code=e.code, message=e.message) from None
            line = f"polarizer: upstream {upstream.prefix} failed: {describe(e)}"
            reply = await self._reply_error(line, "transport-error", finish)
            await upstream.check_closed(e)  # a dead upstream hides its tools before we answer
            return reply
        except Exception as e:
            why = UNREADABLE if _leaf(e, pydantic.ValidationError) else describe(e)
            line = f"polarizer: upstream {upstream.prefix} failed: {why}"
            reply = await self._reply_error(line, "transport-error", finish)
            await upstream.check_closed(e)
            return reply

        if isinstance(result, types.InputRequiredResult):
            asked = (result.input_requests or {}).values()
            kinds = ", ".join(sorted({INPUT_KINDS.get(r.method, r.method) for r in asked}))
            line = (
                f"polarizer: upstream {upstream.prefix} asked the client for {kinds or 'input'}; "
                "Polarizer 0.1 does not forward these"
            )
            return await self._reply_error(line, "unsupported", finish)
        if result.is_error:
            error = f"upstream {upstream.prefix} returned a tool error"
            await self._append("call.returned", finish("tool-error", result_bytes(result), error))
        else:
            await self._append("call.returned", finish("ok", result_bytes(result)))
        return result

    async def _reply_error(self, line: str, outcome: str, finish) -> types.CallToolResult:
        """Answer the client with a one-line isError result that Polarizer wrote itself, and
        record the outcome with that line as its error."""
        reply = _text_result(line)
        await self._append("call.returned", finish(outcome, result_bytes(reply), line))
        return reply
