"""The gateway: one MCP server in front of the upstreams (docs/PROXY-SPEC.md).

Tools only. The server registers tools/list, tools/call and subscriptions/listen, and nothing
else. Every call is recorded: call.sent before it is forwarded and call.returned after, or one
call.refused for a name that matches no listed tool. Arguments go to side files; results
never enter the ledger.
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
from mcp import MCPError
from mcp.server.lowlevel import NotificationOptions, Server
from mcp.server.subscriptions import InMemorySubscriptionBus, ListenHandler
from mcp.shared.subscriptions import ToolsListChanged
from mcp_types import CLIENT_INFO_META_KEY, CONNECTION_CLOSED, PROTOCOL_VERSION_META_KEY

from polarizer import __version__
from polarizer.canon import MAX_INT, EntryRefused
from polarizer.sidefiles import write_args
from polarizer.upstream import SEPARATOR, Upstream, UpstreamSpec, clip, describe, log, one_line
from polarizer.writer import LedgerWriter, Stopped

FORWARDED_META = ("traceparent", "tracestate")
RESERVED_META_PREFIX = "io.modelcontextprotocol/"
PROGRESS_TOKEN_KEYS = ("progressToken", "progress_token")
CLIENT_CALL_ID = "claudecode/toolUseId"
MAX_DROPPED = 32  # meta_dropped keeps at most this many key names, each cut to 128 bytes
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


def result_bytes(result: types.Result) -> int:
    dumped = result.model_dump(by_alias=True, mode="json", exclude_none=True)
    text = json.dumps(dumped, separators=(",", ":"), ensure_ascii=False)
    return len(text.encode("utf-8", "surrogatepass"))


class Gateway:
    """The proxy for one Polarizer process: its upstreams, its ledger session and its server."""

    def __init__(self, specs: list[UpstreamSpec], writer: LedgerWriter, *, config_sha256: str):
        self.writer = writer
        self.config_sha256 = config_sha256
        self.session = secrets.token_hex(8)
        self.upstreams = {spec.prefix: Upstream(spec, self._upstream_changed) for spec in specs}
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
        """Append session.started, connect every upstream in parallel, append one
        upstream.connected per upstream, then serve until the block exits."""
        async with anyio.create_task_group() as tasks:
            await self._append(
                "session.started",
                {
                    "session": self.session,
                    "polarizer_version": __version__,
                    "config_sha256": self.config_sha256,
                },
            )
            for upstream in self.upstreams.values():
                tasks.start_soon(upstream.run)
            async with anyio.create_task_group() as waits:
                for upstream in self.upstreams.values():
                    waits.start_soon(upstream.wait_ready)
            for upstream in self.upstreams.values():
                await self._append("upstream.connected", self._connected_data(upstream))
            try:
                yield self
            finally:
                tasks.cancel_scope.cancel()

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

    async def _upstream_changed(self, upstream: Upstream) -> None:
        """Republish an upstream's list change: on the listen bus for 2026-07-28 clients, and
        as notifications/tools/list_changed to older-era clients."""
        await self.bus.publish(ToolsListChanged())
        for session in list(self._legacy_sessions):
            try:
                await session.send_tool_list_changed()
            except Exception:
                self._legacy_sessions.remove(session)

    # tools/list ------------------------------------------------------------------------

    async def _list_tools(self, ctx, params) -> types.ListToolsResult:
        live = [u for u in self.upstreams.values() if u.connected]
        async with anyio.create_task_group() as tasks:
            for upstream in live:
                tasks.start_soon(upstream.refresh)
        return types.ListToolsResult(tools=[tool for u in live for tool in u.exposed()])

    # tools/call ------------------------------------------------------------------------

    def _route(self, name: str) -> tuple[Upstream | None, str]:
        """(upstream, the upstream's tool name), or (None, the refusal reason)."""
        if SEPARATOR not in name:
            return None, "not a prefixed name"
        prefix, tool = name.split(SEPARATOR, 1)
        upstream = self.upstreams.get(prefix)
        if upstream is None:
            return None, f'no upstream with prefix "{clip(prefix, 256)}"'
        if not upstream.connected:
            return None, f"upstream {prefix} did not connect"
        if tool not in upstream.tools:
            return None, f'upstream {prefix} has no tool "{clip(tool, 512)}"'
        return upstream, tool

    async def _call_tool(self, ctx, params: types.CallToolRequestParams):
        upstream, tool = self._route(params.name)
        if upstream is None:
            await self._append(
                "call.refused", {"session": self.session, "tool": clip(params.name), "reason": tool}
            )
            return _text_result(f"polarizer: no tool named {clip(params.name, 4096)}")

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
            with anyio.CancelScope(shield=True):
                await self._append(
                    "call.returned", finish("cancelled", 0, "the client cancelled the call")
                )
            raise
        except MCPError as e:
            if e.code != CONNECTION_CLOSED:
                await self._append("call.returned", finish("protocol-error", 0, e.message, e.code))
                raise MCPError(code=e.code, message=e.message) from None
            line = f"polarizer: upstream {upstream.prefix} failed: {describe(e)}"
            return await self._reply_error(line, "transport-error", finish)
        except Exception as e:
            line = f"polarizer: upstream {upstream.prefix} failed: {describe(e)}"
            return await self._reply_error(line, "transport-error", finish)

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
