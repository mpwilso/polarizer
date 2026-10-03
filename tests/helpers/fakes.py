"""Test upstreams built on the SDK's low-level Server, for in-memory tests and (through
modern_server.py) over stdio.

Tools, by name:
- echo: returns its arguments as JSON text.
- wait(seconds): sleeps, reporting progress every second; a cancel is recorded in `events`.
- fail: a result with isError true.
- refuse: raises MCPError -32042 with data (a protocol error).
- closed: raises MCPError -32000 itself (recorded as a transport error; see PROXY-SPEC.md).
- ask: returns an InputRequiredResult asking for elicitation (2026-07-28 only).
- elicit: asks the client for a form with ctx.session.elicit_form (an upstream-to-client request).
- change: bumps every description to "Definition v<n>" and announces a list change.
"""

import json
import os

import anyio
import mcp_types as types
from mcp import MCPError
from mcp.server.caching import CacheHint
from mcp.server.lowlevel import Server
from mcp.server.subscriptions import InMemorySubscriptionBus, ListenHandler
from mcp.shared.subscriptions import ToolsListChanged
from mcp_types.version import MODERN_PROTOCOL_VERSIONS

NAMES = ("echo", "wait", "fail", "refuse", "closed", "ask", "elicit", "change")


def text(value: str, is_error: bool = False) -> types.CallToolResult:
    return types.CallToolResult(content=[types.TextContent(text=value)], is_error=is_error)


class FakeUpstream:
    def __init__(
        self, names=NAMES, *, extra=(), page_size=None, ttl_ms=None, listen=True, log=None
    ):
        self.names = list(names) + list(extra)
        self.version = 1
        self.page_size = page_size
        self.calls: list[tuple[str, dict | None, dict]] = []  # (name, arguments, _meta)
        self.events: list[str] = []
        self.list_calls = 0
        self.log = log
        self.bus = InMemorySubscriptionBus()
        hints = {"tools/list": CacheHint(ttl_ms=ttl_ms)} if ttl_ms else None
        self.server = Server(
            "fake",
            version="1",
            cache_hints=hints,
            on_list_tools=self._list,
            on_call_tool=self._call,
            on_subscriptions_listen=ListenHandler(self.bus) if listen else None,
        )

    def _event(self, line: str) -> None:
        self.events.append(line)
        if self.log:
            with open(self.log, "a", encoding="utf-8") as f:
                f.write(line + "\n")

    def tool(self, name: str) -> types.Tool:
        return types.Tool(
            name=name,
            description=f"Definition v{self.version}",
            input_schema={"type": "object"},
        )

    async def _list(self, ctx, params) -> types.ListToolsResult:
        self.list_calls += 1
        tools = [self.tool(n) for n in self.names]
        if self.page_size is None:
            return types.ListToolsResult(tools=tools)
        start = int(params.cursor) if params is not None and params.cursor else 0
        end = start + self.page_size
        cursor = str(end) if end < len(tools) else None
        return types.ListToolsResult(tools=tools[start:end], next_cursor=cursor)

    async def _call(self, ctx, params):
        name, arguments = params.name, params.arguments
        self.calls.append((name, arguments, dict(params.meta or {})))
        if name == "wait":
            seconds = float((arguments or {}).get("seconds", 0))
            try:
                for i in range(1, int(seconds) + 1):
                    await anyio.sleep(1)
                    await ctx.session.report_progress(i, int(seconds), f"step {i}")
                await anyio.sleep(seconds - int(seconds))
            except anyio.get_cancelled_exc_class():
                self._event("wait cancelled")
                raise
            return text(f"waited {seconds:g} s")
        if name == "fail":
            return text("it failed", is_error=True)
        if name == "refuse":
            raise MCPError(code=-32042, message="upstream says no", data={"why": "secret"})
        if name == "closed":
            raise MCPError(code=-32000, message="the upstream says connection closed")
        if name == "ask":
            request = types.ElicitRequest(
                params=types.ElicitRequestFormParams(
                    message="Name?",
                    requested_schema={"type": "object", "properties": {"n": {"type": "string"}}},
                )
            )
            return types.InputRequiredResult(input_requests={"q1": request}, request_state="s")
        if name == "elicit":
            await ctx.session.elicit_form(
                "Name?", {"type": "object", "properties": {"n": {"type": "string"}}}
            )
            return text("answered")
        if name == "change":
            self.version += 1
            if ctx.protocol_version in MODERN_PROTOCOL_VERSIONS:
                await self.bus.publish(ToolsListChanged())
            else:
                await ctx.session.send_tool_list_changed()
            return text(f"now v{self.version}")
        return text(json.dumps(arguments, sort_keys=True))


def from_env() -> FakeUpstream:
    """The fake as modern_server.py runs it: FAKE_TTL_MS sets a tools/list cache hint, and
    FAKE_LOG names a file for its events."""
    ttl = os.environ.get("FAKE_TTL_MS")
    return FakeUpstream(ttl_ms=int(ttl) if ttl else None, log=os.environ.get("FAKE_LOG"))
