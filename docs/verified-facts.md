# Verified facts for Polarizer

Checked on Oct 2, 2026, on WSL2 (kernel 6.18.33.2-microsoft-standard-WSL2, ext4, system Python 3.12.3). Each fact says how it was checked. "Headless" means it was observed only through `claude -p`, never in an interactive Claude Code session. "Binary" means it was read from strings in the Claude Code executable, not observed. Anything not listed here is unverified.

## Claude Code

Version: 2.1.287 (`claude --version`). The stage 3 live check ran on 2.1.288 (see Stage 3).

### How the probe runs were done

The probe was a stdlib Python MCP server over stdio. It answered `initialize` by echoing the requested `protocolVersion`, `tools/list` with one tool `wait(seconds)`, and `tools/call` by sleeping. With `PROBE_PROGRESS=1` and a `progressToken` present, it sent `notifications/progress` every 2 s. It answered everything else, including `server/discover`, with JSON-RPC error -32601. It logged every inbound message with a timestamp to `probe.log` next to itself.

Flaw: it read stdin on the main thread, so it could not log anything that arrived while it slept. The repo's probe (`tests/helpers/probe_server.py`) reads stdin on a separate thread.

The command was:

    claude -p "<prompt>" --model haiku --strict-mcp-config --mcp-config <file> --allowedTools=mcp__probe__wait < /dev/null

Two traps:

- Written as `--allowedTools mcp__probe__wait` with a space, the flag swallowed the prompt as a second value, and the run failed with "Input must be provided".
- Without `< /dev/null`, it waits 3 s for stdin.

### Protocol negotiation (headless)

1. First message: `server/discover` with `_meta` keys `io.modelcontextprotocol/protocolVersion` = `2026-07-28`, `io.modelcontextprotocol/clientInfo` (name `claude-code`, version `2.1.287`), and `io.modelcontextprotocol/clientCapabilities`.
2. After the -32601 error, it sent `initialize` with `protocolVersion` `2025-11-25`.
3. Then `notifications/initialized` and `tools/list`.

The five saved probe logs all show this exact sequence. (A sixth, earlier run's log was overwritten; its printout showed the same.)

Binary only: Claude Code remembers discover verdicts for 24 hours in a state file named `mcp-discover-verdicts.json`, and an environment variable `MCP_PROTOCOL_NEGOTIATION` exists. Observed: a server that answered -32601 was still sent `server/discover` first on each of five runs within a few minutes, so a "not modern" verdict did not stop the probe being re-tried. Whether a "modern" verdict is cached was not tested.

### Capabilities advertised (headless)

`{"roots": {"listChanged": true}, "elicitation": {"form": {}, "url": {}}}`, in both `server/discover` and `initialize`. No elicitation request was sent to it, so showing a form is untested, and headless can't show one anyway.

### `_meta` on tools/call (headless)

Every call carried exactly the keys `claudecode/toolUseId` (a `toolu_...` string) and `progressToken`, in all five saved logs, one call per run. The token was the integer 2 each time.

### Hard per-call timeout

**Binary:** the limit is the first of:

- the server's `timeout` in the MCP config, in ms, ignored if below 1000;
- `MCP_TOOL_TIMEOUT`;
- the default of 100,000,000 ms (about 27.8 hours).

The result is clamped to between 1000 and 2,147,483,647 ms. The config schema text says: "Hard wall-clock limit per call; progress notifications do not extend it."

**Headless, observed:**

- With `MCP_TOOL_TIMEOUT=5000`, a 14 s call failed after 5 s with exactly `MCP server "probe" tool "wait" timed out after 5s`.
- With `MCP_TOOL_TIMEOUT=6000` and progress every 2 s, a 14 s call failed after 6 s. Progress did not extend the limit.

**Not observed:** the per-server `timeout` key, and the default value (that would need a 27.8 hour call).

### Idle timeout

**Binary:**

- Set by `CLAUDE_CODE_MCP_TOOL_IDLE_TIMEOUT`. The default is 1,800,000 ms for stdio and 300,000 ms for remote servers, and 0 disables it.
- Server types `sse-ide`, `ws-ide` and `sdk` are exempt.
- The effective value is min(max(idle, per-server timeout, 1000), hard limit).
- A response or progress notification resets it.
- The error text contains: `sent no response or progress for <N>s; aborting`.

**Headless, observed:** with `CLAUDE_CODE_MCP_TOOL_IDLE_TIMEOUT=6000` and no progress, a 14 s call completed normally, with and without progress. It did not fire. Whether it applies in interactive sessions is unknown.

### Cancellation

Not settled by these runs: the probe was asleep and was killed when the session ended, so a `notifications/cancelled`, if sent, was never read. Settled by the spike below: it is sent on a timeout.

## MCP Python SDK (installed package)

Installed with `pip install mcp==2.2.0` into a venv on Python 3.12.3, then read with `inspect.signature` and the source. Only `Tool` parsing was executed. No server or client was run.

- **Versions:** `mcp` 2.2.0 (released Sep 7, 2026), `mcp-types` 2.2.0, pydantic 2.13.5, anyio 4.15.1, opentelemetry-api 1.45.0. Requires Python >= 3.10. Hard dependencies include `opentelemetry-api`, `jsonschema`, `httpx2`, `starlette`, `uvicorn`, and `pywin32` on Windows.
- **Protocol versions (`mcp_types/version.py`):** `MODERN_PROTOCOL_VERSIONS = ("2026-07-28",)`. Older handshake versions are in `HANDSHAKE_PROTOCOL_VERSIONS`, and `SUPPORTED_PROTOCOL_VERSIONS` is both. The low-level server registers a default `server/discover` handler (`mcp/server/lowlevel/server.py`, around line 449).
- **`mcp.server.lowlevel.Server(name, *, version, instructions, lifespan, on_list_tools, on_call_tool, on_subscriptions_listen, on_progress, ...)`:**
  - Handlers receive `(ctx, params)`.
  - `on_call_tool` returns `CallToolResult` or `InputRequiredResult`.
  - The server runs with `Server.run(read_stream, write_stream, initialization_options)`, using `mcp.server.stdio.stdio_server()`.
- **`ServerRequestContext` fields:** `session`, `lifespan_context`, `protocol_version`, `method`, `params`, `request_id`, `meta`, `request`.
- **`ServerSession`:**
  - `send_progress_notification(progress_token, progress, total=None, message=None, related_request_id=None)`;
  - `send_tool_list_changed()`.
- **Change notifications in the 2026-07-28 era** are delivered only on `subscriptions/listen` streams, and the `listChanged` capability follows whether that method is served. This is from a docstring in `server.py`, around line 570. The older era uses `NotificationOptions.tools_changed`.
- **`mcp.Client(server, *, read_timeout_seconds, message_handler, client_info, mode="auto", prior_discover, elicitation_callback, cache, ...)`:**
  - `server` may be a `Server`, a `Transport`, `StdioServerParameters`, or a URL string.
  - Passing a `Server` uses the in-memory transport (`mcp/client/_memory.py`, `InMemoryTransport`). Use that for tests.
  - It exposes `protocol_version`, `server_info`, `instructions` and `listen()`.
- **`Client.call_tool(name, arguments=None, read_timeout_seconds=None, progress_callback=None, *, meta=None, ...)`:** a `progress_callback` makes the SDK attach its own progress token (confirmed in the spike below).
- **`Client.list_tools(*, cursor=None, meta=None, cache_mode="use")`:**
  - `CacheMode` is `"use" | "refresh" | "bypass"`.
  - The client caches list results by default, so drift detection must not use `"use"`.
  - What each mode does was measured in the spike below.
- **Trace context:** `mcp/shared/_otel.py` injects and extracts W3C `traceparent` and `tracestate` in `_meta`. The SDK also ships an `OpenTelemetryMiddleware` (`mcp/server/_otel.py`) that sets `mcp.method.name`, `mcp.protocol.version` and `gen_ai.operation.name = execute_tool`.
- **`mcp_types.Tool`:**
  - Fields and wire names: `name`, `title`, `description`, `input_schema` (`inputSchema`, a `dict[str, Any]`), `execution`, `output_schema` (`outputSchema`), `icons`, `annotations`, `meta` (`_meta`).
  - Every optional field defaults to `None`. The model config is camelCase aliases with `populate_by_name`, `validate_by_alias` and `validate_by_name`, and no `extra` setting.
  - Executed: an unknown field (`bogusField`) is silently dropped on parse.
  - Executed with `Tool.model_validate` and its default settings: a snake_case `input_schema` is accepted and dumps as `inputSchema`. That is not how the SDK client parses a list: through `Client.list_tools`, a tool sent with `input_schema` fails the whole list (see M1a spec round).
  - Executed: an explicit `"description": null` survives `exclude_unset=True` but not `exclude_none=True`.
  - Floats in schemas (`"minimum": 0.5`) are kept.
- **Serving:** results are serialized with `model_dump(by_alias=True, mode="json", exclude_none=True)` (`mcp/server/runner.py:118`).

## rfc8785 (installed package)

Version 0.1.4, installed into the same venv.

- **API:** `dumps(obj) -> bytes`, `dump(obj, fp)`, and the errors `CanonicalizationError`, `IntegerDomainError` and `FloatDomainError`.
- **How it handles edge cases:**
  - 2^53 raises `IntegerDomainError`; 2^53 - 1 is fine.
  - NaN raises `FloatDomainError`.
  - A lone surrogate raises `CanonicalizationError`.
  - 1.5 is accepted.
- **The stdlib doesn't match on all of those:** `json.dumps` accepts 2^53 without complaint, and with `allow_nan=False` it raises `ValueError` on NaN. Encoding a lone surrogate to UTF-8 raises `UnicodeEncodeError`.
- **Differential, executed once as a throwaway (seed 7):**
  - 20,000 random values with ASCII keys. Integers stayed within plus or minus 2^53-1. Strings drew from C0 controls, ASCII, U+007F, U+0080, U+00A0, U+00E9, U+2028, U+2029, U+FEFF, U+FFFD, U+E000, U+E0049, U+1F600 and U+10FFFF, with nesting to depth 4.
  - Compared `json.dumps(x, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")` with `rfc8785.dumps(x)`: 0 mismatches.
  - Separate case: `{"\ue000": 1, "\U0001f600": 2}` gives different bytes. RFC 8785 sorts keys by UTF-16 code units and Python by code points.

## Append cost (WSL2 ext4, measured once)

Each run did 1,000 appends of about 380 bytes with `os.write` on an `O_APPEND` descriptor, measured with `perf_counter`. The first two rows were run twice.

| Mode | p50 | p95 | p99 |
|---|---|---|---|
| write only | 0.3 µs | 1.0 to 1.1 µs | 1.2 to 1.5 µs |
| write + fsync | 857 to 943 µs | 1,301 to 1,372 µs | 1,767 to 1,832 µs |
| write + fdatasync | 1,101 µs | 1,696 µs | not taken |

Reading and parsing a 10,000-line file, as a whole-file re-read per append does, took 18.2 ms each time.

Not measured: native Windows (no Windows interop from this shell), macOS, and WSL on the Windows filesystem (`/mnt/c` was not mounted).

**Through the writer** (`scripts/bench_append.py`, one run, 1,000 appends, WSL2, Python 3.12.3): write only p50 84.7 µs and p95 167.3 µs; write + fsync p50 3.6 ms and p95 7.7 ms. Each time covers `append(...).result()`: the hand-off to the writer thread, the lock, the size check, the write and, in the second row, the fsync. One run only; the CI job prints the same numbers per runner.

## Parallax v0 ledger (read from source, read-only)

`~/code/parallax/parallax/ledger.py`:

- `GENESIS = "0" * 64`.
- Entry body keys: `id` (8 hex), `ts` (UTC ISO seconds), `kind`, `actor`, `reason`, `data`, `prev`.
- `hash = sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()`, with default separators and `ensure_ascii=True`.
- Each line is `json.dumps(entry, sort_keys=True)`.
- `append` reads every entry to find the last hash.
- `verify` skips a final line with no newline.

`parallax/accept.py:112`: the `Ledger-Head:` commit trailer is the full `hash` of the last entry at accept time.

## RFC 8785 text (fetched Oct 2, 2026)

`https://www.rfc-editor.org/rfc/rfc8785.txt`, 984 lines, fetched with curl and read for sections 3.2.2 to 3.2.3 and Appendix B. The vectors in `tests/test_rfc8785_vectors.py` were written out by hand from it.
- **Used inside the subset:** the "string" and "literals" members of the 3.2.2 example; the same example without "numbers", sorted per 3.2.3; the 3.2.3 sort order "", "a", "aa", "ab"; the 3.2.2.2 control-character rules; Appendix B's zero and minus zero (as the JSON integer -0); and note (1)'s range limits, 2^53-1 and its negative.
- **Checked as rejected, outside the subset:** the full 3.2.2 example (a float); the 3.2.3 sorting test data (non-ASCII keys; rfc8785 still gives the RFC's order, and the stdlib doesn't); a lone surrogate (3.2.2.2 note); and every other Appendix B row: integers past 2^53-1, every non-integer, NaN and Infinity.
- **Left out:** the expected serializations of Appendix B's floats, since floats never enter a v1 entry; the development portal's large number file (Appendix I), for the same reason.

## CI actions pinned by commit (Oct 2, 2026)

Looked up over the network from each action's own repository with the GitHub REST API (`/repos/<owner>/<repo>/releases`, `/git/ref/tags/<tag>` and `/commits/<sha>`), and `action.yml` read at that commit from raw.githubusercontent.com:

| Action | Tag | Commit | Runtime in `action.yml` |
|---|---|---|---|
| `actions/checkout` | v7.0.1 (latest release, Jul 20, 2026) | `3d3c42e5aac5ba805825da76410c181273ba90b1` | node24 |
| `astral-sh/setup-uv` | v10.2.0 (latest release, Sep 21, 2026) | `c18668ad3cf93ea998bef934396af7bb5c839dc7` | node24; still has the `version` input |

Both tags are lightweight tags that point directly at those commits. The workflow had used the moving tags `v4` and `v6`; it now uses these newer releases, pinned by SHA.

## The v0 fixture (run once, Oct 2, 2026)

`tools/make_v0_fixture.py ~/code/parallax` checked that `22ef600` resolves to `22ef60083b56683ab5abda19bb224309d07fee88`, wrote `git show 22ef600:parallax/ledger.py` and a small driver into a temporary directory, and ran the driver there with `PYTHONDONTWRITEBYTECODE=1` and `python -B`. The extracted file imports only the standard library (`hashlib`, `json`, `sys`, `threading`, `uuid`, `contextlib`, `datetime`, `pathlib`, and `fcntl` or `msvcrt`). Four appends produced `conformance/valid/v0-parallax.jsonl`: floats (0.5, 2.25), non-ASCII text escaped as `\u` sequences, and seconds-precision `+00:00` timestamps. `git status --porcelain` in the Parallax clone was empty afterwards, and the guard reported no change. Parallax's own `verify` skips blank lines; Polarizer's v0 check reports a blank line as `invalid` (not valid JSON). That is a deliberate difference, because the frozen v0 check is byte for byte on line form (LEDGER-SPEC.md, v0).

## Names (live, Oct 2)

- PyPI: `polarizer`, `polarizer-mcp` and `mcp-polarizer` all return 404.
- npm: `polarizer` returns 404.
- GitHub: `mpwilso/polarizer` does not exist. `github.com/polarizer` is an existing user account with no public repos. About 13 repos named `polarizer` exist, none MCP or AI related.

## mcpclerk: ideas credited, nothing else used

- The run-end entry that records how many entries the run wrote.
- Tools hidden from the list are still refused and recorded when called by name.
- An approve-everything switch that exists only as a command-line flag, never in the policy file.

## Spike: SDK proxy under Claude Code (Oct 2, 2026, second round)

A throwaway proxy of 61 lines on the SDK's low-level `Server` and `Client` (mcp 2.2.0, in `~/code/polarizer/.venv`, made with uv) sat in front of two upstreams:

- **probe2:** a stdlib server that reads stdin on its own thread, runs calls on workers, sends progress every second, and changes its tool description on each `tools/list`.
- **Everything:** the reference server `@modelcontextprotocol/server-everything`, run with `npx -y @modelcontextprotocol/server-everything stdio`. That run was unpinned, before the pinning rule. npm's cache shows it resolved to version 2026.8.31, which was then the latest published. From now on it is pinned; see "Pinned fetches" below.

A wiretap logged every line in both directions with timestamps. Nothing from the spike is in the repo.

**Ran the proxy in memory and over stdio** (SDK client, no Claude Code):

- `Client(proxy_server)` and `Client(StdioServerParameters(proxy.py))` both negotiated 2026-07-28 with the proxy.
- The proxy's own upstream clients negotiated 2025-11-25 with probe2 and with Everything. Each tried `server/discover` first.
- Prefixed names worked, and `every__echo` returned `Echo: hi` through the proxy.
- `params.meta` in `on_call_tool` is a plain dict. Calling `.model_dump()` on it raised `AttributeError`.
- The client's progress token in that dict:
  - over stdio, the key was `progress_token` (snake_case), not `progressToken`;
  - in memory, it was absent, because the in-memory transport passes the callback directly.

**Progress relay** (in memory, stdio, and under Claude Code headless):

- `ctx.session.send_progress_notification` with a token read from `params.meta` sent nothing, because the token wasn't where the code looked.
- `ctx.session.report_progress(progress, total, message)`, called from the `progress_callback` passed to the upstream `call_tool`, delivered every update to the client:
  - in memory and over stdio: `[(1, 3), (2, 3), (3, 3)]`;
  - under Claude Code: updates under Claude Code's own token, 2, while probe2 received the SDK's token, 4.
- The SDK's docstring says `report_progress` is a no-op when the caller asked for no progress.

**Cancellation by an SDK client** (in memory and stdio): cancelling a 6 s call after 2 s:

- the proxy's handler got `CancelledError`;
- probe2 received `notifications/cancelled {"requestId": <its id>, "reason": "caller cancelled"}` from the proxy's upstream client.

**`cache_mode`** (three tests):

- **Handshake-era upstreams** (probe2 and Everything, both at 2025-11-25): `use`, `refresh` and `bypass` each sent a new `tools/list` upstream every time. There was no caching.
- **2026-07-28 in-memory server with no cache hint:** same; every mode reached the handler.
- **2026-07-28 server with `cache_hints={"tools/list": CacheHint(ttl_ms=60000)}`:**
  - `use` served the cached list without calling the handler;
  - `refresh` called it and updated the cache;
  - `bypass` called it but did not update the cache: a later `use` returned the `refresh` result, not the `bypass` one.

### Claude Code 2.1.287 through the proxy (headless, 5 runs)

Each run was `claude -p ... --model haiku --no-session-persistence --strict-mcp-config --mcp-config <file in the scratchpad>`, with the wiretap in front of the proxy (runs 1 to 3) or of probe2 directly (runs 4 and 5).

- **Connection order in the 2026-07-28 era** (all three proxy runs):
  1. `server/discover` (id `server-discover-probe-1`);
  2. the result, `supportedVersions: ["2026-07-28"]`;
  3. `subscriptions/listen` (id `listen:0`), acknowledged by the SDK's `ListenHandler` with `notifications: {toolsListChanged: true}`;
  4. a second `server/discover` (id 0);
  5. `tools/list`.

  There was no `initialize`.
- **`_meta` on tools/call in the 2026-07-28 era:** `claudecode/toolUseId`, `io.modelcontextprotocol/clientCapabilities`, `io.modelcontextprotocol/clientInfo`, `io.modelcontextprotocol/protocolVersion` and `progressToken` (the integer 2, then 4 for the second call in a session). In the older era, as before, only `claudecode/toolUseId` and `progressToken`.
- **List changes:** after each call, the proxy published `ToolsListChanged()` on the listen bus, and Claude Code sent a new `tools/list` within 1 s each time (run 1, twice).
- **Hard timeout** (`MCP_TOOL_TIMEOUT=5000`, 14 s call):
  - 2026-07-28, through the proxy: Claude Code sent `notifications/cancelled {"requestId": 2, "reason": "SdkError: Request timed out"}` exactly 5.0 s after the call. The proxy handler got `CancelledError`, and probe2 received `notifications/cancelled` from the proxy at the same moment. The model saw `MCP server "pz" tool "probe__wait" timed out after 5s`.
  - 2025-11-25, probe2 direct: the same `notifications/cancelled` with the same reason, 5.0 s after the call.
- **SIGINT to `claude -p`** 9 s into a 20 s call: Claude Code sent no `notifications/cancelled` in either era. It closed the connection. Through the proxy, the proxy's shutdown cancelled the in-flight upstream call, so probe2 still received `notifications/cancelled` from the proxy. With probe2 direct, probe2 saw only end of input.
- **Elicitation:** advertised in both eras (`{"form": {}, "url": {}}`); not exercised.

## Pinned fetches

Everything fetched for tests is pinned to an exact version. Commands, run from `~/code/polarizer`:

    uv venv .venv --python 3.12
    uv pip install --python .venv/bin/python "mcp==2.2.0" "rfc8785==0.1.4"
    npx -y @modelcontextprotocol/server-everything@2026.8.31 stdio

`mcp==2.2.0` pulls in `mcp-types==2.2.0` and, unpinned, pydantic (2.13.5 installed), anyio (4.15.1) and opentelemetry-api (1.45.0). M0's `uv.lock` pins those too. The round 1 scratch venv used `pip install mcp==2.2.0` and `pip install rfc8785==0.1.4`.

**Stage 1 (Oct 2, 2026).** `pyproject.toml` declares `mcp>=2.2,<3`, `rfc8785==0.1.4`, and the dev group `pytest==9.1.1` and `ruff==0.16.10`; the build backend is `uv_build==0.12.19`, matching the local uv 0.12.19. Versions were read from PyPI's JSON API before pinning. Commands, run from `~/code/polarizer`:

    uv lock
    uv lock --upgrade-package mcp==2.2.0 --upgrade-package mcp-types==2.2.0
    uv sync --locked

The first `uv lock` resolved `mcp>=2.2,<3` to the newest release in range, 2.3.0 (with `mcp-types` 2.3.0), and `uv sync` installed it in `.venv` before this was noticed. Nothing ran against it; stage 1 never imports `mcp`. The second command re-locked both to 2.2.0, the version every SDK fact above was checked against. `uv.lock` now pins, among others: mcp 2.2.0, mcp-types 2.2.0, pydantic 2.13.5, anyio 4.15.1, opentelemetry-api 1.45.0, rfc8785 0.1.4, pytest 9.1.1 (with pluggy 1.6.0, iniconfig 2.3.0, packaging 26.3, pygments 2.21.0, and colorama 0.4.6 on Windows) and ruff 0.16.10. CI installs with `uv sync --locked`, using uv 0.12.19 from `astral-sh/setup-uv`.

**Stage 3 (Oct 2, 2026).** The Filesystem server's version was resolved once with `npm view @modelcontextprotocol/server-filesystem dist-tags` (latest: 2026.8.31, published Aug 31, 2026) and pinned. Node v24.21.0, npm 11.19.0. Both servers were pre-warmed, as run from the scratch directory:

    npx -y @modelcontextprotocol/server-everything@2026.8.31 stdio < /dev/null
    npx -y @modelcontextprotocol/server-filesystem@2026.8.31 <dir> < /dev/null

`npx` pins only the named package. Its own dependencies are caret ranges, resolved at fetch time; npm's npx cache recorded `@modelcontextprotocol/sdk` 1.32.0 under both, and `diff` 8.0.4, `glob` 13.0.6 and `minimatch` 10.2.6 under Filesystem (103 and 107 packages in the two lock files). A fresh fetch later may resolve them differently. `pyproject.toml` now also declares `pydantic==2.13.5`, the version `uv.lock` already held, because `proxy.py` imports it; `uv lock --offline` changed only Polarizer's own entries in `uv.lock`.

## Cancellation through the proxy, with timestamps (headless)

Run `r2_timeout` from the round 2 spike:

- `MCP_TOOL_TIMEOUT=5000`;
- Claude Code 2.1.287 calling `probe__wait` with 14 s;
- the wiretap between Claude Code and the proxy;
- probe2, the threaded stdlib upstream, logging every inbound message.

Times are Unix seconds from each process's own log, all on one machine.

| Time | Log | Line |
|---|---|---|
| 1790966378.845 | wire, Claude Code to proxy | `tools/call` `probe__wait`, request id 2, `progressToken` 2 |
| 1790966378.846 | proxy | handler starts `probe__wait` |
| 1790966378.847 | probe2 | `tools/call` `wait`, request id 4, `progressToken` 4 |
| 1790966383.854 | wire, Claude Code to proxy | `notifications/cancelled` `{"requestId": 2, "reason": "SdkError: Request timed out"}` |
| 1790966383.855 | proxy | handler ends with `CancelledError` |
| 1790966383.855 | probe2 | `notifications/cancelled` `{"requestId": 4, "reason": "caller cancelled"}` |
| 1790966385.354 | probe2 | end of input (session closed) |

The model saw `MCP server "pz" tool "probe__wait" timed out after 5s`. The upstream received the cancel 1 ms after Claude Code sent its own, 5.009 s after the call. probe2 doesn't act on a cancel, so it never finished the call; it's a logger.

## Spike round 3: a 2026-07-28 upstream (no Claude Code)

**Upstream:** an SDK 2.2 low-level `Server` with:
- `on_subscriptions_listen=ListenHandler(InMemorySubscriptionBus())`;
- `cache_hints={"tools/list": CacheHint(ttl_ms=60000)}`;
- a `wait` tool that reports progress each second;
- a `change` tool that bumps its tool definitions and publishes `ToolsListChanged()`.

**Proxy:** a copy of the spike proxy, with one upstream reached in memory (`Client(server)`) or over stdio (`Client(StdioServerParameters(...))`). With `LISTEN=1`, it opens `upstream_client.listen(tools_list_changed=True)` in its lifespan and republishes each event on its own bus.

**Driver:** an SDK client connects to the proxy the same way (in memory or stdio) and opens its own `listen` stream. Six runs: {memory, stdio} x {`use` without listen, `use` with listen, `refresh` without listen}.

**Versions:** in all six runs, client to proxy and proxy to upstream both negotiated 2026-07-28. That's the proxy's `Client.protocol_version` and the upstream handler's `ctx.protocol_version`.

**A list change, in memory** (upstream and proxy timestamps, in seconds):
1. `.686`: the upstream publishes `ToolsListChanged`.
2. `.687`: the proxy's listen stream receives it and republishes.
3. `.687`: the driver's listen stream receives it.

Over stdio, the same steps took `.293`, `.294` and `.295`. That's 1 to 2 ms end to end.

**TTL hint with `cache_mode="use"`** in the proxy's upstream client, after the upstream changed:

| Proxy setup | Second `tools/list` from the driver | Upstream handler called? |
|---|---|---|
| `use`, no listen | stale: "Definition v1" | no; served from the client cache |
| `use`, listening | fresh: "Definition v2" | yes, once |
| `refresh`, no listen | fresh: "Definition v2" | yes, once |

The results were the same in memory and over stdio. The listen event clears the SDK client's response cache before the listener wakes (`Client._evict_for_listen_event`), which is why `use` is fresh only with a listener.

**Cancellation and progress** (listening runs): the driver cancelled a 6 s `wait` after 2.5 s.
- **Relay:** progress 1 and 2 reached the driver through `report_progress`.
- **Memory:** the upstream handler logged `CancelledError` at `.189`, and the proxy handler at `.190`.
- **Stdio:** both at `.805`.

**Upstream `_meta`:** requests from the proxy carried `io.modelcontextprotocol/clientCapabilities`, `clientInfo` and `protocolVersion`. Over stdio, calls made with a progress callback also carried `progress_token` (snake_case, as seen by the handler's `params.meta`). In memory they didn't.

## Round 4 checks (Oct 2, 2026)

### Claude Code's MCP startup timeout

**Binary:**
- Server startup uses `MCP_TIMEOUT`: `return _&&_>0?Math.min(_,2147483647):30000`, so 30,000 ms by default.
- A separate `MCP_CONNECT_TIMEOUT_MS` defaults to 5,000 ms. Its purpose wasn't read.

**Headless, observed:** four `claude -p` runs against probe2, set to wait before reading its first message:

| Handshake delay | Setting | Result |
|---|---|---|
| 2 s | defaults | worked; `server/discover` at 2.3 s, tool called |
| 8 s | defaults | worked; `server/discover` at 8.9 s, tool called |
| 8 s | `MCP_TIMEOUT=4000` | failed. The model said the server "failed to connect with a timeout error". The probe logged nothing after its delay began, and the run ended at 6.5 s. |
| 8 s | `MCP_CONNECT_TIMEOUT_MS=15000` | worked |

So the startup limit is `MCP_TIMEOUT` (30 s by default), and `MCP_CONNECT_TIMEOUT_MS` did not cut off an 8 s handshake in headless runs. Polarizer's default `connect_timeout_seconds` of 10, with a maximum of 20, sits well under 30 s. The interactive behavior was not tested.

### Long tool names (headless)

A probe tool with a 100-character name (full name `mcp__pz__<name>`, 109 characters) was called successfully, and Claude Code sent the exact 100-character name in `tools/call`. The SDK's `mcp/shared/tool_name_validation.py` applies SEP-986: tool names SHOULD be 1 to 128 characters of `[A-Za-z0-9._-]`.

### The SDK's default environment for stdio upstreams (installed package)

`mcp/client/stdio.py` starts an upstream with `env=get_default_environment() | (server.env or {})`.
- `get_default_environment()` copies only `DEFAULT_INHERITED_ENV_VARS` from the parent.
- On POSIX those are `HOME`, `LOGNAME`, `PATH`, `SHELL`, `TERM` and `USER`.
- On Windows they are `APPDATA`, `HOMEDRIVE`, `HOMEPATH`, `LOCALAPPDATA`, `PATH`, `PATHEXT`, `PROCESSOR_ARCHITECTURE`, `SYSTEMDRIVE`, `SYSTEMROOT`, `TEMP`, `USERNAME` and `USERPROFILE`.
- It skips any value starting with `()` (shell functions).

Read from source, not run.

### Requests from an upstream to the client, with no client callbacks (SDK, no Claude Code)

**Setup:** a throwaway SDK 2.2 upstream whose tools ask the client for something, called by an SDK `Client` with no sampling, elicitation or roots callbacks. Three ways: 2026-07-28 in memory, 2026-07-28 over stdio, and 2025-11-25 over stdio (`mode="legacy"`).

| Upstream tool does | 2026-07-28 (memory and stdio) | 2025-11-25 (stdio) |
|---|---|---|
| `ctx.session.elicit_form(...)` | the upstream itself raises `NoBackChannelError`; `call_tool` raises `MCPError` -32600 "Cannot send 'elicitation/create': this transport context has no back-channel for server-initiated requests." (in memory the exception class is `NoBackChannelError`, a subclass of `MCPError`) | the client refuses with -32600; `call_tool` raises `MCPError` -32600 "Elicitation not supported" |
| `ctx.session.create_message(...)` | same, for `sampling/createMessage` | `MCPError` -32600 "Sampling not supported" |
| `ctx.session.list_roots()` | same, for `roots/list` | `MCPError` -32600 "List roots not supported" |
| returns an `InputRequiredResult` | `Client.call_tool` raises `MCPError` -32600 "Elicitation not supported"; `client.session.call_tool(..., allow_input_required=True)` returns the `InputRequiredResult` itself, with `input_requests` {"q1": "elicitation/create"} | `MCPError` -32603 "Handler returned an invalid result" (not valid in that era) |

- **The `message_handler`** passed to an older-era `Client` saw none of these requests. The SDK's default callbacks answer them first.
- **Advertising** (from `mcp/client/session.py`): the client advertises sampling, elicitation or roots only when a non-default callback is registered for it. So registering a callback to detect such requests would also invite upstreams to send them.

### Errors from `Client.call_tool` (SDK, no Claude Code)

The same in both eras, over stdio:
- **Upstream JSON-RPC error:** an upstream handler raising `MCPError(code=-32042, message="upstream says no")` reached the client as `MCPError` with code -32042 and the same message.
- **Tool error:** a result with `is_error=True` came back as a normal result.
- **Read timeout:** `read_timeout_seconds=1` on a 3 s call raised `MCPError` -32001 "Request 'tools/call' timed out".
- **Dead upstream:** an upstream that exited mid-call (`os._exit`) raised `MCPError` -32000 "Connection closed", and so did every later call on that client.

`mcp_types/jsonrpc.py` defines `CONNECTION_CLOSED = -32000` and `REQUEST_TIMEOUT = -32001`. `MCPError` has `.code`, `.message` and `.data`, and nothing that marks an error as raised locally rather than sent by the upstream. `ListToolsResult` has `next_cursor` (and `ttl_ms` and `cache_scope`).

### Capabilities a tools-only proxy advertises (SDK, no Claude Code)

A low-level `Server` with only `on_list_tools`, `on_call_tool` and `on_subscriptions_listen`, run with `create_initialization_options(NotificationOptions(tools_changed=True))`, advertised:
- 2026-07-28: `{"tools": {"listChanged": true}}`;
- 2025-11-25: `{"experimental": {}, "tools": {"listChanged": true}}`.

### Claude Code bookkeeping during these runs

`~/.claude.json` grew from 85,455 to 85,543 bytes when the four startup runs began, at 19:19:14Z. The long-name run changed its mtime again without changing its size. The guard's MCP hashes stayed unchanged.

## Stage 3 (Oct 2, 2026)

### Results through the SDK (installed package, executed)

- **Unknown fields:** `CallToolResult`, the content models and the resource models have no `extra` setting, so pydantic ignores fields the protocol doesn't define. Raw bytes from `polarizer serve`, in front of the probe's `rich` result, lacked every unknown field: at the result, in a content block, in an embedded resource and in annotations (`tests/test_fidelity.py::test_rich_result_on_the_wire`).
- **`isError`:** `CallToolResult.is_error` is `bool = False` (`mcp_types/_types.py`, around line 1480), so a result whose upstream left `isError` out is sent with `"isError": false`.
- **`execution`:** `ToolExecution` is documented as "2025-11-25 only", and the 2026-07-28 wire `Tool` has no such field, so tool definitions served on a 2026-07-28 connection have no `execution`.
- **The serverInfo stamp:** `mcp/server/runner.py`, `_stamp_server_info` (around lines 397 to 417), adds `_meta["io.modelcontextprotocol/serverInfo"]` to every 2026-era result. A value the handler set wins, and an explicit null is stamped over. Older-era results are never stamped. On the same connections, `resultType` is filled in when absent.
- **Unparseable results:** a content block with `"type": "video"` made the upstream call raise pydantic's `ValidationError`, whose message quotes the input (`input_value={'type': 'video', ...}`). Stage 2 passed that message to the client and into the ledger's `error`; stage 3 replaced it with a fixed line.

### The reference servers through the proxy (local, no Claude Code)

Run with `POLARIZER_REFERENCE=1 uv run --locked pytest tests/test_reference.py`, on Linux:
- **Everything 2026.8.31** reports `serverInfo` `mcp-servers/everything` 2.0.0, answers `initialize` with 2025-11-25, lists 13 tools in one page, and sends `notifications/tools/list_changed` right after `initialize`.
- **Filesystem 2026.8.31** reports `secure-filesystem-server` 0.2.0, 2025-11-25, 14 tools, and uses the directory from its arguments when the client has no roots capability.
- **Raw JSON-RPC at 2025-11-25 on both sides:** all 27 tool definitions were identical apart from the prefix. `echo`, `list_directory` and `read_text_file` results were identical apart from an added `"isError": false`.
- **SDK clients:** the client side of the proxy negotiated 2026-07-28, and the direct side 2025-11-25. Every one of the 27 tools lost only `execution` (`{"taskSupport": "forbidden"}` on all of them). The three results were identical apart from Polarizer's serverInfo stamp.

### Live check (headless, Claude Code 2.1.288)

`scripts/live-check.sh`, run once. `claude -p` with `--model haiku`, `--strict-mcp-config`, a generated `--mcp-config`, `--allowedTools=mcp__pz__probe__wait`, `--permission-mode default`, `--no-session-persistence` and `--output-format json`, with `MCP_TOOL_TIMEOUT=5000`. Auto mode was not set by the script; user-scope settings were not read. The run was started from inside another Claude Code session, so `CLAUDE_CODE_*` variables (names only, listed by the script) were set.

| Time | Log | Line |
|---|---|---|
| 1790998917.738 | wire, Claude Code to Polarizer | `server/discover` |
| 1790998918.182 | wire, Claude Code to Polarizer | `subscriptions/listen` (id `listen:0`) |
| 1790998920.778 | wire, Claude Code to Polarizer | `tools/call` `probe__wait` `{"seconds": 14}`, request id 2 |
| 1790998920.780 | probe | `tools/call` `wait`, request id 5, `progressToken` 5 |
| 1790998925.790 | wire, Claude Code to Polarizer | `notifications/cancelled` `{"requestId": 2, "reason": "SdkError: Request timed out"}` |
| 1790998925.791 | probe | `notifications/cancelled` `{"requestId": 5, "reason": "caller cancelled"}` |
| 1790998926.939 | probe | end of input |

- **The cancel** came 5.012 s after the call, and reached the probe 1 ms later.
- **The ledger** verified intact: 6 entries, with `call.returned` outcome `cancelled`, `latency_ms` 5012 and `client_call_id` set from `claudecode/toolUseId`.
- **The model replied:** "The probe__wait tool timed out after 5 seconds while attempting to wait for 14 seconds."
- **Cost:** 0.0239 USD, from Claude Code's own `total_cost_usd`.
- **Bookkeeping:** `~/.claude.json` went from 91,166 bytes (mtime 03:25:28Z, Oct 3 UTC) to 88,917 bytes (03:41:57Z) during the run. The run's working directory was a new temp directory.

## Interactive (Oct 3, 2026)

### Esc during a long call (interactive, Claude Code 2.1.288)

Verified by the owner in an interactive session, following docs/MANUAL-CHECK.md step 5. Claude Code 2.1.288 negotiated 2026-07-28 with Polarizer; the probe was the upstream, at 2025-11-25. Claude called `probe__wait` with 30 s, and the owner pressed Esc during the call. No wiretap was in place, so nothing recorded what Claude Code itself sent to Polarizer.

Probe log (`/tmp/polarizer-probe.log`), Unix seconds:

    1791036268.181 {"jsonrpc":"2.0","id":5,"method":"tools/call","params":{"name":"wait","arguments":{"seconds":30},...}}
    1791036274.792 {"jsonrpc":"2.0","method":"notifications/cancelled","params":{"requestId":5,"reason":"caller cancelled"}}
    1791036274.792 call 5 stopped after cancel

Ledger (`~/.local/share/polarizer/ledger.jsonl`), abridged:

    seq 21 call.sent      ts 2026-10-03T14:04:28.180Z  tool probe__wait
    seq 22 call.returned  ts 2026-10-03T14:04:34.791Z  call_seq 21, outcome cancelled, latency_ms 7730,
                                                       error "the client cancelled the call", result_bytes 0

Claude Code's own log for the server (`~/.cache/claude-cli-nodejs/-home-matt-code-polarizer/mcp-logs-polarizer/2026-10-03T14-03-26-955Z.jsonl`), abridged:

    14:04:28.160Z  Calling MCP tool: probe__wait
    14:04:34.791Z  Sending SIGINT to MCP server process
    14:04:34.793Z  subscriptions/listen stream dropped (remote); attempting to re-listen
    14:04:34.891Z  SIGINT failed, sending SIGTERM to MCP server process
    14:04:34.903Z  Tool 'probe__wait' failed after 6s: Connection closed
    14:04:34.942Z  MCP server process exited cleanly

- **Esc stopped the call:** Polarizer sent its own `notifications/cancelled` upstream, the probe stopped, and the ledger recorded `call.returned` with outcome `cancelled`. The cancel reached the probe 6.611 s after the call did, by the probe's wall clock. `latency_ms` 7730 is on the monotonic clock; see "Wall clock and monotonic clock on WSL2" below for the 1.1 s difference.
- **How Claude Code stopped it.** Claude Code logged a SIGINT to the Polarizer process in the same millisecond as `call.returned`, then a SIGTERM 100 ms later. The owner confirmed that pressing Esc alone caused this; the session was not quit. Polarizer's source has no signal handler of its own, but anyio runs asyncio through `asyncio.Runner`, whose SIGINT handler cancels the main task (see M1a spec round), which is why the call was recorded as `cancelled`. Whether Claude Code also sent `notifications/cancelled` to Polarizer is not visible in these logs, because no wiretap was running. The text `caller cancelled` is what Polarizer's own upstream client sends, whatever the reason for the cancellation.

### After the Esc: Polarizer restarted (unresolved)

In order, from the three logs, between the cancel and the next ledger entry (seq 23):

| Time (UTC) | Log | Line |
|---|---|---|
| 14:04:34.791 | ledger | seq 22 `call.returned`, outcome `cancelled` |
| 14:04:34.791 | Claude Code | SIGINT to the Polarizer process |
| 14:04:34.792 | probe | `notifications/cancelled`, then `call 5 stopped after cancel` |
| 14:04:34.891 | Claude Code | SIGINT failed, sending SIGTERM |
| 14:04:34.894 | probe | end of input |
| 14:04:34.903 | Claude Code | the tool failed after 6 s: Connection closed |
| 14:04:34.942 | Claude Code | the server process exited cleanly |
| 14:04:46.954 | Claude Code | new log file: starting a connection |
| 14:04:47.380 | ledger | seq 23 `session.started` (a new Polarizer session) |
| 14:04:47.393 | probe | a new probe process starts |

- **The ledger** has no entry between seq 22 and seq 23. Polarizer writes no session-end entry, so none was expected.
- **Polarizer's stderr:** Claude Code's log records stderr only once, at connect (14:03:27.865Z: the probe and Filesystem startup lines). Nothing from Polarizer's stderr was logged after the signals.
- **This session's transcript** shows the call as interrupted: "the session ended before this call's result was recorded".
- **Settled:** Esc itself made Claude Code signal the server process; the owner did not quit `claude`. **Still unknown:** what started the new connection 12 s later.

### Wall clock and monotonic clock on WSL2

A throwaway script sampled `time.time()` and `time.monotonic()` once a second for 30 s, three times in a row, on this machine (WSL2, kernel 6.18.33.2). Each run saw exactly one backward step of the wall clock against the monotonic clock: 1112, 1103 and 1108 ms. Between steps, the two agreed to within 0.01 ms.

- **The ledger's own calls** (`~/.local/share/polarizer/ledger.jsonl` against `/tmp/polarizer-probe.log`): `call.sent` `ts` was 1 to 6 ms before the probe received each call. For the four probe calls, `latency_ms` minus the gap between `call.returned` and `call.sent` `ts` was -1 ms (2 s call), +1063 ms (30 s), +1119 ms (30 s, cancelled) and +1125 ms (30 s). In the two completed 30 s calls, the probe's own monotonic span (30116 and 30168 ms) matched `latency_ms`, while its own wall clock showed 29047 and 29042 ms: the same gap in a separate process.
- **Through the test rig** (in-memory SDK client, `polarizer` Gateway, the stdio probe; no model, no Claude Code), three 5 s calls with wall and monotonic times taken at the `call.sent` timestamp, the forward, the upstream's return and the `call.returned` timestamp: no step happened, the two clocks agreed throughout, and the `ts` gaps (5041, 5060, 5017 ms) matched `latency_ms` (5040, 5059, 5017).
- **So:** `ts` is the wall clock and `latency_ms` is monotonic. On this machine they can disagree by about 1.1 s whenever a step falls inside a call; the code measures each one as specified.

## M1a spec round (Oct 3, 2026)

Checked for docs/PIN-SPEC.md on mcp 2.2.0, mcp-types 2.2.0, pydantic 2.13.5, anyio 4.15.1 and rfc8785 0.1.4, in `.venv`, with throwaway scripts in the session scratch directory. Nothing from them is in the repo. No Claude Code, no model.

- **How the SDK client parses a result** (`mcp/client/session.py`, around lines 588 to 601, read): it validates the raw result against the negotiated era's model, drops fields from a later revision, then parses the version-free model with `by_name=False`. Executed: a stdlib stdio server at 2025-11-25 listing a tool with `inputSchema` listed fine through `Client.list_tools`. The same server with `input_schema` instead made `list_tools` raise pydantic's `ValidationError` for `ListToolsResult`.
- **`execution` on the client side** (executed, in memory): an SDK server whose tool sets `execution` (`taskSupport` `optional`), listed by an SDK `Client`. In `legacy` mode (2025-11-25) the parsed `Tool` kept `execution`. In `auto` mode (2026-07-28) it was `None`, because the server's own 2026-07-28 serializer leaves it out. The version-free `Tool` keeps `execution` whenever the wire has it, in either era.
- **How the SDK serves a result** (`mcp_types.methods.serialize_server_result`, read and executed): it validates against the era's model (`extra="ignore"`) and dumps with `exclude_none`. Every handshake version (2024-11-05 to 2025-11-25) uses the 2025-11-25 model. Comparing the two models' fields recursively, the only `Tool` field the 2025-11-25 model has and the 2026-07-28 model lacks is `execution`. The 2025-11-25 `inputSchema` and `outputSchema` models also type `properties` and `required`. Both eras' schema models allow extra keys.
- **Nulls when serving** (executed, both eras): a null nested in a schema property (`"default": null`) is kept. A null key at the top level of `inputSchema` is kept by `model_dump` but dropped when served. `"annotations": {"title": null}` is served as `"annotations": {}`.
- **rfc8785** (executed): `dumps` raises `IntegerDomainError` for 9223372036854775807 in a schema, and writes the float 1e21 as `1e+21`.
- **Definition hashes** under PIN-SPEC.md's form (executed): the probe's `wait` definition hashes to `ac0778cf2bd27c6f802ca57ffc736ffddcd97d9773898dcc1c9c68ea9bc54e8b`. With `"description": "How long."` added to its `seconds` property, it hashes to `3462be54bba570aacd2a87ea2bb68c6806a68abac4761e0d848d1b6deb8a11b5`. The `legacy` and `auto` listings above gave one hash. Adding `execution`, a null `title`, a null top-level schema key or an unknown top-level field, or reordering keys, left the hash unchanged.
- **anyio and SIGINT** (`anyio/_backends/_asyncio.py`, around lines 114 to 228, read): on Python 3.11 and later, `anyio.run` uses `asyncio.Runner`, which installs a SIGINT handler that cancels the main task when SIGINT still has Python's default handler. SIGTERM keeps the operating system's default, which ends the process at once.

## Definition hashes on real definitions (Oct 3, 2026)

A throwaway script in the session scratch directory, outside the repo, implemented PIN-SPEC.md section 2 exactly: `model_dump(by_alias=True, mode="json", exclude_none=True)`, `mcp_types.methods.serialize_server_result("tools/list", "2026-07-28", ...)` with the three envelope values, `rfc8785.dumps`, and sha256 over `POLARIZER-TOOLDEF/1\n` plus the canonical bytes. It ran on `.venv` (mcp 2.2.0, mcp-types 2.2.0, rfc8785 0.1.4), with `POLARIZER_REFERENCE=1` set for that run only. No Claude Code, no model.

**Servers:** Everything and Filesystem at 2026.8.31, run with the npx commands under Pinned fetches (Filesystem confined to a scratch directory), and the repo's probe (`tests/helpers/probe_server.py`). Nothing else was run.

**Captures:** each server through an SDK `Client` with `cache_mode="refresh"`, following `next_cursor`, in four ways:

| Capture | Negotiated | Parsed `Tool`s with `execution` |
|---|---|---|
| direct, `mode="auto"` | 2025-11-25 | Everything 13 of 13, Filesystem 14 of 14, probe 0 of 6 |
| direct, `mode="legacy"` | 2025-11-25 | the same |
| through `polarizer serve` (M0), `mode="auto"` | 2026-07-28 | none |
| through `polarizer serve` (M0), `mode="legacy"` | 2025-11-25 | the same as direct |

None of the three servers speaks 2026-07-28: in `auto` mode the SDK client fell back to 2025-11-25 with each. The only 2026-07-28 wire was therefore the client side of `polarizer serve`, whose ledger was a scratch directory. The prefix was removed from those names before hashing.

**Results:**
- **Definitions:** 33 tools (Everything 13, Filesystem 14, probe 6), captured 4 times each, 132 definitions.
- **Hashed:** 132 of 132. None failed at step 3 (the 2026-07-28 model) or step 4 (RFC 8785). No large integer, non-finite float or lone surrogate appeared.
- **Equal across eras:** all 33 tools gave one hash in all four captures, with and without `execution` on the parsed `Tool`.
- **The probe's `wait`** hashed to `ac0778cf2bd27c6f802ca57ffc736ffddcd97d9773898dcc1c9c68ea9bc54e8b` in every capture, the value in PIN-SPEC.md section 10.
- **What the definitions hold:** the served forms use only `name`, `title`, `description`, `inputSchema`, `outputSchema` and `annotations`. They have no `_meta`, no floats and no non-ASCII text. They hold 7 integers, the largest 10 (`trigger-long-running-operation`'s `duration` default and `get-resource-links`' `count` maximum). So open question 1 (large integers) affects none of these 27 reference tools.

## Manual check follow-up (Oct 3, 2026)

### Development sessions spawned the proxy (from the ledger, not interactive)

The manual check had created a `.mcp.json` at the repo root, and `.claude/settings.local.json` (local, gitignored) enables project MCP servers. So every `claude` started in `~/code/polarizer` launched `polarizer serve` in front of the probe and Filesystem, and wrote to `~/.local/share/polarizer`. The owner deleted that `.mcp.json` and moved the ledger to `~/.local/share/polarizer-m0-check-20261003`. Read in this session, that ledger verifies `intact: 41 entries, 7 sessions, 6 calls`, head at seq 40. After the manual check's last call (seq 28), it holds three such starts, each a `session.started`, two `upstream.connected` (probe 6 tools, fs 14) and a `session.client` (`claude-code` 2.1.288, 2026-07-28), with no calls:

| Seqs | `session.started` ts (UTC) |
|---|---|
| 29 to 32 | 2026-10-03T22:24:57.473Z |
| 33 to 36 | 2026-10-03T22:55:52.131Z |
| 37 to 40 | 2026-10-03T22:57:01.118Z |

The last is the development session that wrote this section: its `scripts/guard.sh snapshot` ran at 22:57:06Z, and Polarizer's tools were listed in it, though it never called one. Which sessions made the first two starts is not known.

### Esc restart timing (interactive, Claude Code 2.1.288)

From Claude Code's MCP log for the server, as read by the owner, with the ledger read in this session:
- **14:04:34.791Z:** SIGINT to the Polarizer process (log); `call.returned` `cancelled` at seq 22 (ledger).
- **14:04:46.954Z:** a new connection, 12.16 s later (log); `session.started` at seq 23, 14:04:47.380Z (ledger).
- **14:05:03.755Z:** the next tool call (log); `call.sent` at seq 27, 14:05:03.776Z (ledger).

Whether Claude Code restarted the server by itself or when the owner typed the next prompt is unresolved.

### `/mcp` names and labels (interactive, observed by the owner)

From a screenshot of `/mcp` during the manual check: all 20 tool names were shown in full, including the longest, `fs__list_directory_with_sizes`, and none was shortened. Claude Code showed read-only and destructive labels taken from the upstream tools' annotations. Annotations are part of the definition hash (PIN-SPEC.md, section 2), so from M1a a changed label counts as drift.

### `claude --help` (2.1.288, read in this session)

    --mcp-config <configs...>             Load MCP servers from JSON files or
                                          strings (space-separated)
    --strict-mcp-config                   Only use MCP servers from --mcp-config,
                                          ignoring all other MCP configurations

Neither is marked "only works with --print", unlike other options in the same help, so both apply to interactive sessions. The help doesn't mention claude.ai connectors. Whether `--strict-mcp-config` turns them off is not verified.

## Stage 4 (Oct 3, 2026)

Checked while building M1a's pins at rest, on mcp 2.2.0, mcp-types 2.2.0, pydantic 2.13.5, anyio 4.15.1 and rfc8785 0.1.4, in `.venv`. No Claude Code, no model.

- **The two fixed hashes,** computed with `_meta` left out of the hashed form (the owner's decision), are unchanged: the probe's `wait` hashes to `ac0778cf2bd27c6f802ca57ffc736ffddcd97d9773898dcc1c9c68ea9bc54e8b`, and with the nested description to `3462be54bba570aacd2a87ea2bb68c6806a68abac4761e0d848d1b6deb8a11b5`. First with a throwaway script in the session scratch directory, then in `tests/test_defhash.py`. The same tool with `_meta` set hashes to `5658e381...` when `_meta` is kept, and to the unchanged value when it is left out.
- **An upstream that dies while idle** (executed: the probe's `crash` tool, and the probe killed with SIGTERM between requests): the SDK `Client` raises nothing into the task that holds it open. The upstream task keeps waiting, and `client.session` stays set. Only the next request shows it: it raises `MCPError` -32000 "Connection closed" at once, because the dispatcher closed on end of input (`mcp/shared/jsonrpc_dispatcher.py`, around lines 505 to 523, read). Polarizer therefore confirms a -32000 with a ping before it marks an upstream lost.
- **Ping on 2026-07-28** (executed, read): `Client.send_ping` is marked deprecated ("ping is removed as of 2026-07-28; the method only works under mode='legacy'") and warns with `MCPDeprecationWarning`. `ClientSession.send_ping`, which it wraps, carries no warning, and on a closed connection it raises -32000 like any request. Polarizer uses the session's method.
- **The 2026-07-28 result model** (executed) rejects a tool whose `inputSchema` has `"type": "string"` (step 3 of the hashed form fails with pydantic's `ValidationError`), and accepts a schema whose `properties` is not an object.
- **rfc8785's errors** (executed): `IntegerDomainError` and `FloatDomainError` are subclasses of `CanonicalizationError`, itself a `ValueError`. The message for 9223372036854775808 is `9223372036854775808 exceeds safe integer domain for JSON floats`.
- **The reference servers under pins** (`POLARIZER_REFERENCE=1 uv run --locked pytest -s "tests/test_reference.py::test_reference_tools_hash_and_serve"`, run once, on Linux): Everything 2026.8.31 lists 13 tools and Filesystem 2026.8.31 lists 14. All 27 hashed, none has `_meta`, none was unservable, and the largest integer in any schema is 10. Two priming runs into separate ledgers gave the same 27 hashes. Approved, every tool reached an SDK client (2026-07-28) and a raw client (2025-11-25) exactly as its stored copy, renamed. The servers ran with the pinned commands under Pinned fetches. Afterwards, of npx's two cache directories for them (`~/.npm/_npx/68b53d3fd47bf8db` and `a705c79b42eea4c8`, each holding version 2026.8.31), only the directories' own mtimes had changed; none of the 9,132 entries inside was newer than the session's guard snapshot, so nothing was fetched and nothing new was pinned.

## Stage 4 review (Oct 4, 2026, UTC)

Checked while fixing the stage 4 review items, on mcp 2.2.0, mcp-types 2.2.0, pydantic 2.13.5 and rfc8785 0.1.4, in `.venv`, with throwaway scripts in the session scratch directory and then in tests. No Claude Code, no model.

- **rfc8785 and a lone surrogate** (read and executed): `dumps` raises its base `CanonicalizationError` with the message `input contains non-UTF-8 codepoints`, raised from the `UnicodeEncodeError` (`rfc8785/_impl.py`, around line 93). It has no subclass of its own, so only the cause tells it apart.
- **pydantic's messages quote their input** (executed): a tool whose `inputSchema` has `"type"` set to an ESC and 5,000 characters fails the 2026-07-28 result model with a `ValidationError` whose message holds a cut-down part of that text, with the ESC shown as `\x1b`. Such a tool can't reach the hash through an upstream: the 2025-11-25 result model, which the SDK client checks first on older-era connections, also requires `"type": "object"`.
- **A NaN in a schema** (executed): `model_dump(mode="json")` writes it as `null`, so the definition hashes, with `null` in its place.
- **Nothing limits one definition's size** (read): `mcp/client/stdio.py` splits stdout into lines with no length limit, and Polarizer's only listing limits are 100 pages and 1,000 tools.
- **The reference servers, offline.** `npx --offline -y <package>@2026.8.31 ...` ran both pinned servers from npx's cache; `--offline` makes npm refuse any registry request, and a version not in the cache failed with `notarget`. The two reference tests ran through a scratch `npx` script that adds `--offline` (STAGE4-NOTES.md, Review fixes). Nothing new was fetched or pinned.

## Stage 5 (Oct 4, 2026, UTC)

Checked while building pins in motion and shutdown, on mcp 2.2.0, anyio 4.15.1 and Python 3.12.3, in `.venv`. No Claude Code, no model.

- **End of input cancels the calls in flight** (read): `JSONRPCDispatcher.run` (`mcp/shared/jsonrpc_dispatcher.py`, around lines 476 to 525) cancels its handler task group when the read stream ends, so a `tools/call` handler sees `CancelledError`, as M0 relied on.
- **The SDK's stdio server reads stdin on an anyio worker thread** (read): `stdio_server` iterates `anyio.wrap_file(...)`, whose reads run through `to_thread.run_sync` without `abandon_on_cancel`, so a cancel waits for the read. anyio's `WorkerThread` is not a daemon thread (`anyio/_backends/_asyncio.py`, around line 1044). With an explicit `stdin` argument, `stdio_server` skips its claim on file descriptor 0 and still claims 1 (`mcp/server/stdio.py`).
- **The SDK's stdio client shuts an upstream down inside a shield** (read): close its stdin, wait up to 2 s (`PROCESS_TERMINATION_TIMEOUT`), then SIGTERM to its process group, 2 s more, and up to 2 s to reap (`mcp/client/stdio.py`). A cancelled caller can't cut that short.
- **A daemon thread blocked in `sys.stdin.buffer.readline` aborts the interpreter at exit** (executed, a throwaway script with stdin a pipe left open): exit code -6 and `Fatal Python error: _enter_buffered_busy: could not acquire lock for <_io.BufferedReader name='<stdin>'> at interpreter shutdown, possibly due to daemon threads`. The same thread blocked in `os.read(0, ...)` exits 0. Seen first as `test_sigterm_alone_during_call` exiting -6, before serve read stdin with `os.read`.
- **anyio's signal receiver restores the default action when it closes** (read): `_SignalReceiver.__exit__` calls `loop.remove_signal_handler`, which sets SIGTERM back to `SIG_DFL` (and SIGINT to `default_int_handler`) in asyncio's `unix_events.py`. Seen as `test_sigint_then_sigterm_during_call` exiting -15 when serve's shutdown finished within the 100 ms before the SIGTERM and then tore down normally; that the SIGTERM landed after the receiver closed is inferred from the exit code, not traced.

## Stage 5 follow-up (Oct 4, 2026, UTC)

Checked while making upstream text safe and looking at upstream processes at shutdown, on mcp 2.2.0, pydantic 2.13.5 and Python 3.12.3, in `.venv`. No Claude Code, no model.

- **The SDK keeps each upstream's process to itself** (read): `stdio_client` holds the process in a local variable (`mcp/client/stdio.py`, around line 125), and `Client` calls `stdio_client(srv)` with no hook (`mcp/client/client.py`, around line 396). Nothing public returns the process or its pid. On POSIX it starts each upstream with `start_new_session=True` (around line 347); on Windows it uses a job object with `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` (`mcp/os/win32/utilities.py`, around line 207).
- **An upstream writes its stderr to serve's** (read, then seen): `stdio_client`'s `errlog` defaults to `sys.stderr`, and `Client` passes none. Seen as a probe that ignored end of input for 60 s keeping serve's captured stderr open: `subprocess.run(..., capture_output=True)` returned after 60.7 s, though serve had exited.
- **A lone surrogate escape makes the SDK refuse the whole line** (executed): a JSON-RPC error whose message holds the JSON escape for U+D800 fails `jsonrpc_message_adapter.validate_json` with `Invalid JSON: unexpected end of hex escape`. The SDK logs that with `logger.exception("Failed to parse JSONRPC message from server")` (`mcp/client/stdio.py`, around line 223). With no logging set up, Python's last-resort handler prints it with a traceback, and pydantic's message quotes the line, cut down, with escapes shown as `\x1b`, over several lines.
- **pydantic's error type for a string `inputSchema` over stdio is `model_type`** (executed, through `polarizer serve`), and `dict_type` when `ListToolsResult.model_validate` is called on a dict directly.
- **Probes left running by `test_signal_during_startup`** (seen): 22 probe processes started 01:05 to 01:42 UTC, about 86 s apart, each with `PROBE_DELAY=5` and a `test_signal_during_startup0` log path, each with one thread waiting. Each had read `server/discover` from its stdin after serve had exited, failed to answer, and lost its reader thread to `BrokenPipeError`. This session's runs added two, stopped by their pids; the 22 were left for the owner (STAGE5-NOTES.md, Follow-up).

## Unverified

These are assumed or open. Nothing here has been observed.

- **Interactive sessions:** apart from Esc to cancel (see Interactive), every Claude Code fact above is headless. Not tested interactively:
  - closing a session without pressing Esc while a call is in flight (does Claude Code send `notifications/cancelled`, or only close stdin?);
  - whether Claude Code sends `notifications/cancelled` on Esc, before or instead of signaling the server process (needs the wiretap);
  - whether interactive sessions open `subscriptions/listen` the same way;
  - elicitation forms.
- **Idle timeout:** it did not fire headless. Whether it applies interactively is unknown.
- **Hard limit settings:** the default of 27.8 hours and the per-server `timeout` key are read from the binary, not observed.
- **Startup in interactive sessions:** whether `MCP_TIMEOUT` (30 s) and `MCP_CONNECT_TIMEOUT_MS` (5 s) behave there as they did headless.
- **What `MCP_CONNECT_TIMEOUT_MS` controls.**
- **Polarizer's own `unsupported` path end to end:** only the SDK behavior beneath it was run.
- **Claude Code and the result limits:** whether Claude Code reads anything that the SDK drops or adds (PROXY-SPEC.md, Results). Through Polarizer it always gets the 2026-07-28 form.
- **Discover-verdict cache:** whether a cached "modern" verdict changes the connection order.
- **Claude Code and cache hints:** whether it honors server cache hints on `tools/list`.
- **Older-era list changes from an upstream:** receiving `notifications/tools/list_changed` from a 2025-11-25 upstream through the SDK `Client`'s `message_handler`. Stage 2 ran it SDK to SDK only (`tests/test_eras.py::test_handshake_upstream`, an in-memory upstream in the 2025-11-25 era); never with a real third-party server.
- **Older-era list changes toward a client:** `send_tool_list_changed` toward an older-era client, with Claude Code, which always negotiates 2026-07-28 with an SDK 2.2 server. Stage 2 ran it toward an SDK client only (`tests/test_eras.py::test_eras`).
- **Append costs:** native Windows, macOS, and WSL on the Windows filesystem (the CI benchmark will measure the first two).
- **Windows `ledger.head`:** the `os.replace` failure when the file is held open is expected from Windows semantics, not observed.
- **A third-party upstream on 2026-07-28:** Everything, Filesystem and the probe all answer at 2025-11-25 (Definition hashes on real definitions). The definition hash of a tool from a 2026-07-28 upstream has been checked only with SDK servers written for the tests (M1a spec round), never with a real one.
- **A 2026-07-28 upstream under Claude Code:** round 3 ran a 2026-07-28 upstream through the proxy with SDK clients only. The Claude Code side doesn't depend on the upstream's version, because the proxy ends one connection and starts another, but the combination wasn't run.
- **Windows and macOS behavior of the stage 1 code:** the lock, `os.replace` retry, binary-mode file access and the read-only verify test are written for both, but have only run on Linux. The Windows test that holds `ledger.head` open runs only on the Windows CI runner.
