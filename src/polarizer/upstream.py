"""Upstream MCP servers: connecting, listing and change notices (docs/PROXY-SPEC.md).

Each upstream runs in its own long-lived task, which owns its SDK Client from entry to exit
(anyio requires that). Startup waits for each one up to its connect timeout and cancels the
task of any that hasn't connected by then. Nothing here writes the ledger; proxy.py does.
"""

import re
import sys
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import anyio
import mcp_types as types
from mcp import Client, MCPError
from mcp_types import CONNECTION_CLOSED, METHOD_NOT_FOUND
from mcp_types.version import MODERN_PROTOCOL_VERSIONS

from polarizer import __version__

SEPARATOR = "__"
EXPOSED_NAME = re.compile(r"[A-Za-z0-9._-]{1,128}")
MAX_PAGES = 100
MAX_TOOLS = 1000
_SURROGATE = re.compile("[" + chr(0xD800) + "-" + chr(0xDFFF) + "]")


def clip(text: str, limit: int = 1024) -> str:
    """Text safe for a ledger entry: lone surrogates become U+FFFD, and the UTF-8 form is cut
    to at most `limit` bytes on a character boundary."""
    text = _SURROGATE.sub(chr(0xFFFD), str(text))
    raw = text.encode("utf-8")
    return text if len(raw) <= limit else raw[:limit].decode("utf-8", "ignore")


def one_line(text: str, limit: int = 1024) -> str:
    """clip(), with every run of whitespace (newlines included) folded to one space."""
    return clip(" ".join(str(text).split()), limit)


def describe(error: BaseException) -> str:
    """One line for an exception: its message, or its type's name when it has none. An
    exception group is described by its first leaf."""
    while isinstance(error, BaseExceptionGroup) and error.exceptions:
        error = error.exceptions[0]
    return one_line(str(error) or type(error).__name__)


def log(line: str) -> None:
    """serve's stdout is the protocol channel, so every log line goes to stderr."""
    print(line, file=sys.stderr, flush=True)


class ListingLimit(Exception):
    """An upstream listed more than MAX_PAGES pages or MAX_TOOLS tools."""


async def list_all(client: Client) -> list[types.Tool]:
    """Every tool the upstream lists, following next_cursor, always fetched fresh."""
    tools: list[types.Tool] = []
    cursor = None
    for _ in range(MAX_PAGES):
        page = await client.list_tools(cursor=cursor, cache_mode="refresh")
        tools.extend(page.tools)
        if len(tools) > MAX_TOOLS:
            raise ListingLimit(f"more than {MAX_TOOLS} tools")
        cursor = page.next_cursor
        if cursor is None:
            return tools
    raise ListingLimit(f"more than {MAX_PAGES} pages")


@dataclass(frozen=True)
class UpstreamSpec:
    """How to reach one upstream. `target` is anything mcp.Client accepts: serve passes
    StdioServerParameters, and tests may pass an in-memory Server. `mode` is the Client's
    negotiation mode; serve always uses "auto"."""

    prefix: str
    target: Any
    connect_timeout: float
    mode: str = "auto"


Callback = Callable[["Upstream"], Awaitable[None]]


class Upstream:
    """One upstream server, its client and its latest tool list."""

    def __init__(self, spec: UpstreamSpec, on_change: Callback, on_lost: Callback | None = None):
        self.spec = spec
        self.prefix = spec.prefix
        self.client: Client | None = None  # set while its SDK client is open
        self.error: str | None = None  # why it never connected
        self.lost: str | None = None  # why a connected upstream went away
        self.list_error: str | None = None  # why its latest refresh failed; None after a success
        self.protocol_version: str | None = None
        self.tools: dict[str, types.Tool] = {}  # the upstream's own name -> its tool, in order
        self.skipped: list[str] = []
        self._on_change = on_change
        self._on_lost = on_lost
        self._ready = anyio.Event()
        self._scope = anyio.CancelScope()
        self._refresh_lock = anyio.Lock()  # one listing at a time, whatever triggered it
        self.started = False  # run() was handed to a task group
        self.closed = anyio.Event()  # run() has returned: the client and its process are gone

    @property
    def connected(self) -> bool:
        """Connected and listed at startup. Stays true if the upstream goes away later; the
        gateway then hides its tools (`lost`)."""
        return self.error is None and self.protocol_version is not None

    @property
    def list_known(self) -> bool:
        """Connected, still there, and its latest listing succeeded (docs/PIN-SPEC.md,
        section 6: anything else is the "list unknown" state)."""
        return self.connected and self.client is not None and self.list_error is None

    async def run(self) -> None:
        """The upstream's task: connect, list, then follow change notices until cancelled."""
        try:
            await self._run()
        finally:
            self.closed.set()

    def close(self) -> None:
        """Cancel the task; the SDK then closes the client and stops the process."""
        self._scope.cancel()

    async def _run(self) -> None:
        with self._scope:
            try:
                async with Client(
                    self.spec.target,
                    mode=self.spec.mode,
                    message_handler=self._message,
                    client_info=types.Implementation(name="polarizer", version=__version__),
                ) as client:
                    listed = await list_all(client)
                    if self.error is None:
                        self._absorb(listed)
                        self.protocol_version = client.protocol_version
                        self.client = client
                    self._ready.set()
                    await self._follow_changes(client)
            except Exception as e:
                if self.client is None:
                    if self.error is None and self.lost is None:
                        self.error = describe(e)
                else:
                    await self.lose(describe(e))
            finally:
                if self.client is not None:
                    self.client = None
                    self.lost = self.lost or "connection closed"
                self._ready.set()

    async def wait_ready(self) -> None:
        """Wait up to the connect timeout; past it, record the timeout and cancel the task."""
        with anyio.move_on_after(self.spec.connect_timeout):
            await self._ready.wait()
        if not self._ready.is_set():
            self.error = f"timeout after {self.spec.connect_timeout:g} s"
            self._ready.set()
            self._scope.cancel()

    async def lose(self, why: str) -> None:
        """The connection is gone for good (M0 never reconnects): hide everything and tell the
        gateway once."""
        if self.client is None:
            return
        self.client = None
        self.lost = why
        log(f"polarizer: upstream {self.prefix} stopped: {why}; its tools are hidden")
        if self._on_lost is not None:
            await self._on_lost(self)

    async def check_closed(self, error: BaseException) -> bool:
        """After `error` from this upstream's client: if it says the connection closed, confirm
        with a ping, which fails at once on a closed connection. An upstream that sent -32000
        itself still answers the ping. Returns True if the connection is lost."""
        client = self.client
        while isinstance(error, BaseExceptionGroup) and error.exceptions:
            error = error.exceptions[0]
        if client is None:
            return True
        if not (isinstance(error, MCPError) and error.code == CONNECTION_CLOSED):
            return False
        try:
            with anyio.move_on_after(self.spec.connect_timeout):
                # The session's own ping: Client.send_ping warns on 2026-07-28 connections.
                # Only the closed check matters here, and a closed connection fails any request.
                await client.session.send_ping()
        except MCPError as e:
            if e.code != CONNECTION_CLOSED:
                return False
            await self.lose(describe(error))
            return True
        except Exception:
            return False
        return False

    async def refresh(self) -> bool:
        """List again with cache_mode="refresh", bounded by the connect timeout. On a failure,
        `list_error` says why, one line goes to stderr, and the tools count as unknown until a
        later listing succeeds. Returns True on success."""
        async with self._refresh_lock:
            return await self._refresh()

    async def _refresh(self) -> bool:
        client = self.client
        if client is None:
            return False
        try:
            with anyio.fail_after(self.spec.connect_timeout):
                listed = await list_all(client)
        except TimeoutError:
            self.list_error = f"timeout after {self.spec.connect_timeout:g} s"
            log(f"polarizer: upstream {self.prefix}: listing timed out; its tools are hidden")
            return False
        except Exception as e:
            if await self.check_closed(e):
                return False
            self.list_error = describe(e)
            log(
                f"polarizer: upstream {self.prefix}: listing failed ({self.list_error}); "
                "its tools are hidden"
            )
            return False
        self._absorb(listed)
        self.list_error = None
        return True

    def _absorb(self, listed: list[types.Tool]) -> None:
        tools: dict[str, types.Tool] = {}
        skipped: list[str] = []
        for tool in listed:
            if not EXPOSED_NAME.fullmatch(f"{self.prefix}{SEPARATOR}{tool.name}"):
                skipped.append(tool.name)
            else:
                tools.setdefault(tool.name, tool)
        self.tools = tools
        self.skipped = skipped

    async def _follow_changes(self, client: Client) -> None:
        if client.protocol_version in MODERN_PROTOCOL_VERSIONS:
            try:
                async with client.listen(tools_list_changed=True) as subscription:
                    async for _event in subscription:
                        await self._on_change(self)
            except MCPError as e:
                if e.code != METHOD_NOT_FOUND:
                    log(f"polarizer: upstream {self.prefix}: change notices stopped: {describe(e)}")
            except Exception as e:
                log(f"polarizer: upstream {self.prefix}: change notices stopped: {describe(e)}")
        await anyio.sleep_forever()

    async def _message(self, message) -> None:
        """Older-era upstreams send notifications/tools/list_changed outside any request."""
        if isinstance(message, types.ToolListChangedNotification):
            await self._on_change(self)
