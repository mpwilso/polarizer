"""Test upstream: the FakeUpstream from fakes.py, served over stdio by the SDK.

An SDK client in "auto" mode negotiates 2026-07-28 with it. See fakes.from_env for its
environment variables.
"""

import anyio
from fakes import from_env
from mcp.server.stdio import stdio_server


async def main() -> None:
    fake = from_env()
    async with stdio_server() as (read_stream, write_stream):
        await fake.server.run(
            read_stream, write_stream, fake.server.create_initialization_options()
        )


if __name__ == "__main__":
    anyio.run(main)
