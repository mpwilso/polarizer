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
from mcp_types import METHOD_NOT_FOUND
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


class Upstream:
    """One upstream server, its client and its latest tool list."""

    def __init__(self, spec: UpstreamSpec, on_change: Callable[["Upstream"], Awaitable[None]]):
        self.spec = spec
        self.prefix = spec.prefix
        self.client: Client | None = None  # set while its SDK client is open
        self.error: str | None = None  # why it never connected
        self.lost: str | None = None  # why a connected upstream went away
        self.protocol_version: str | None = None
        self.tools: dict[str, types.Tool] = {}  # the upstream's own name -> its tool, in order
        self.skipped: list[str] = []
        self._on_change = on_change
        self._ready = anyio.Event()
        self._scope = anyio.CancelScope()

    @property
    def connected(self) -> bool:
        """Connected and listed at startup. Stays true if the upstream goes away later: its
        tools stay listed, and calls to them fail as transport errors."""
        return self.error is None and self.protocol_version is not None

    async def run(self) -> None:
        """The upstream's task: connect, list, then follow change notices until cancelled."""
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
                    if self.error is None:
                        self.error = describe(e)
                else:
                    self.lost = describe(e)
                    log(f"polarizer: upstream {self.prefix} stopped: {self.lost}")
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

    async def refresh(self) -> None:
        """List again with cache_mode="refresh". A failure keeps the last good list, with one
        line on stderr, so a client's tools/list never hangs on one upstream."""
        client = self.client
        if client is None:
            return
        try:
            with anyio.fail_after(self.spec.connect_timeout):
                listed = await list_all(client)
        except TimeoutError:
            log(f"polarizer: upstream {self.prefix}: listing timed out; kept its last list")
            return
        except Exception as e:
            why = describe(e)
            log(f"polarizer: upstream {self.prefix}: listing failed ({why}); kept its last list")
            return
        self._absorb(listed)

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

    def exposed(self) -> list[types.Tool]:
        """The tools as served: the upstream's own definitions, renamed <prefix>__<tool>."""
        return [
            tool.model_copy(update={"name": f"{self.prefix}{SEPARATOR}{name}"})
            for name, tool in self.tools.items()
        ]

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
