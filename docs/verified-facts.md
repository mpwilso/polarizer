# Verified facts for Polarizer

Checked on Oct 2, 2026, on WSL2 (kernel 6.18.33.2-microsoft-standard-WSL2, ext4, system Python 3.12.3). Each fact says how it was checked. "Headless" means it was observed only through `claude -p`, never in an interactive Claude Code session. "Binary" means it was read from strings in the Claude Code executable, not observed. Anything not listed here is unverified. "The owner" is the project's author, who ran the interactive checks; "this session" or "a development session" is a Claude Code session that built Polarizer. Records made before v0.1 name `scripts/guard.sh` (now `scripts/dev/guard.sh`) and, for the manual check's config, `polarizer.example.toml` (now `manual/polarizer.manual.toml`).

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
- GitHub: `mpwilso/polarizer` did not exist yet (this repository was created later). `github.com/polarizer` is an existing user account with no public repos. About 13 repos named `polarizer` exist, none MCP or AI related.

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

Verified by the owner in an interactive session, following docs/dev/MANUAL-CHECK.md step 5. Claude Code 2.1.288 negotiated 2026-07-28 with Polarizer; the probe was the upstream, at 2025-11-25. Claude called `probe__wait` with 30 s, and the owner pressed Esc during the call. No wiretap was in place, so nothing recorded what Claude Code itself sent to Polarizer.

Probe log (`/tmp/polarizer-probe.log`), Unix seconds:

    1791036268.181 {"jsonrpc":"2.0","id":5,"method":"tools/call","params":{"name":"wait","arguments":{"seconds":30},...}}
    1791036274.792 {"jsonrpc":"2.0","method":"notifications/cancelled","params":{"requestId":5,"reason":"caller cancelled"}}
    1791036274.792 call 5 stopped after cancel

Ledger (`~/.local/share/polarizer/ledger.jsonl`), abridged:

    seq 21 call.sent      ts 2026-10-03T14:04:28.180Z  tool probe__wait
    seq 22 call.returned  ts 2026-10-03T14:04:34.791Z  call_seq 21, outcome cancelled, latency_ms 7730,
                                                       error "the client cancelled the call", result_bytes 0

Claude Code's own log for the server (`~/.cache/claude-cli-nodejs/-home-<you>-code-polarizer/mcp-logs-polarizer/2026-10-03T14-03-26-955Z.jsonl`), abridged:

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
- **The reference servers, offline.** `npx --offline -y <package>@2026.8.31 ...` ran both pinned servers from npx's cache; `--offline` makes npm refuse any registry request, and a version not in the cache failed with `notarget`. The two reference tests ran through a scratch `npx` script that adds `--offline` (dev/STAGE4-NOTES.md, Review fixes). Nothing new was fetched or pinned.

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
- **Probes left running by `test_signal_during_startup`** (seen): 22 probe processes started 01:05 to 01:42 UTC, about 86 s apart, each with `PROBE_DELAY=5` and a `test_signal_during_startup0` log path, each with one thread waiting. Each had read `server/discover` from its stdin after serve had exited, failed to answer, and lost its reader thread to `BrokenPipeError`. This session's runs added two, stopped by their pids; the 22 were left for the owner (dev/STAGE5-NOTES.md, Follow-up).

## M1a check, interactive (Oct 4, 2026, observed by the owner)

Observed by the owner, interactively, with Claude Code 2.1.289, running `scripts/m1a-check.sh` (docs/dev/MANUAL-CHECK.md's M1a section at the time). These are the owner's results, from that script's results file; no development session saw the run.

- **Approving the first run's group while a session was open** (`approve`): the answer was `y`. `/mcp` showed the tools without a reconnect.
- **Approving the changed definition while a session was open** (`approve-changed`): the answer was `y`. `/mcp` listed `probe__wait` again without a reconnect.
- **The ledger** (`finish`): 53 entries, verified intact.
- **The `rugpull` step** of `m1a-check.sh` was skipped, so that run recorded nothing about `/mcp` after the rug pull or about what Claude said when asked to call `probe__wait`. `scripts/rugpull-check.sh` now checks the rug pull headless (docs/dev/MANUAL-CHECK.md, M1a).

So for Claude Code 2.1.289, interactive Claude Code lists the tools again after Polarizer's change notice, without a reconnect, for both a first approval and an approval of a changed definition (PIN-SPEC.md, decision 9).

## Rug-pull check (Oct 4, 2026, headless, Claude Code 2.1.289)

`scripts/rugpull-check.sh`, run once by the owner, at commit a46cbc8. Three `claude -p` runs with `--model haiku`, `--strict-mcp-config`, a generated `--mcp-config` (one per phase, differing only in `PROBE_PHASE`), `--allowedTools=mcp__pz__probe__wait`, `--permission-mode default`, `--no-session-persistence` and `--output-format json`, stdin from `/dev/null`, `MCP_TOOL_TIMEOUT` unset (it was unset before too). Auto mode was not set by the script; user-scope settings were not read. The owner started it with `!` from inside a Claude Code session, not from a plain terminal, so that session's `CLAUDE_CODE_*` variables (names only, listed by the script) were set for the three runs. The results come from the script's output, which the owner pasted into that session.

| Check | Result |
|---|---|
| A: the probe received `tools/call wait` | PASS: 1 during run A |
| A: `call.sent` and `call.returned` outcome `ok` for `probe__wait` | PASS: seq 20 and 21 |
| B: `tool.drift` for probe wait, with different `approved_hash` and `live_hash` | PASS: seq 24, approved `ac0778cf2bd27c6f...`, live `9214f449513a1334...` |
| B: the probe received no `tools/call wait` | PASS: 0 |
| B: no `call.sent` for `probe__wait` in B's session | PASS: 0 `call.sent`, 0 `call.refused` |
| The approval: `tool.approved` for the new hash | PASS: seq 26 |
| C: the probe received `tools/call wait` | PASS: 1 during run C |
| C: `call.sent` and `call.returned` outcome `ok` for `probe__wait` | PASS: seq 30 and 31 |
| The ledger verifies as intact | PASS: 32 entries, 4 sessions, 2 calls |

- **The model replied** (information only, not used for pass or fail): A, "The tool waited for 1 second as requested."; B, "The tool `mcp__pz__probe__wait` is not available."; C, "The tool executed successfully and waited for 1 second as requested."
- **Cost,** from Claude Code's own `total_cost_usd`: A 0.0237063, B 0.0111517 and C 0.0103563 USD, 0.045214 USD in total.
- **No serve process** for the check's config was left running after any run. Every check passed, so the temp directory and its ledger were removed.
- **Bookkeeping:** `~/.claude.json` went from 91,602 bytes (mtime 16:40:49Z) to 89,343 bytes (16:42:34Z) during the runs. `scripts/guard.sh check` afterwards found its MCP config unchanged in all 6 locations, and nothing else changed.
- **Run B, why no refusal:** B's session has `tool.drift` at seq 24 and neither a `call.sent` nor a `call.refused` for `probe__wait`, because the model did not try to call the tool. It was not offered the tool and said so. `tool.approved` for the new hash followed at seq 26, between runs B and C.

What it shows: with Claude Code 2.1.289, headless, a definition that changed after approval is hidden from a real agent until the new definition is approved. In run B the agent was not offered `probe__wait` and nothing reached the probe; after the approval, run C's call reached it and was recorded `ok`.

What it does not show:

- **The refusal path.** B's agent never named the hidden tool, so no call for it reached Polarizer and nothing was refused. That a hidden tool called by name is refused and recorded is shown by tests only (`tests/test_pins.py::test_hidden_tool_called_by_name`).
- **Re-listing within one live session.** Each run was a new Claude Code process with a new Polarizer, and B saw the changed definition from the probe's first listing. That was answered separately, interactively (M1a check, interactive).
- **A real third-party server.** The only upstream was the test probe.
- **Windows or macOS.** It ran on Linux (WSL2) only.
- **The documented conditions.** It ran inside a Claude Code session, not from a plain terminal. The script now says so in its first line when `CLAUDE_CODE_SESSION_ID` is set, and names the `CLAUDE_CODE_*` variables it passes on.

## Rug-pull check, second run (Oct 4, 2026, headless, plain terminal, Claude Code 2.1.289)

`scripts/rugpull-check.sh`, run a second time by the owner, from a plain terminal, on 2026-10-04 at about 17:08 UTC, with Claude Code 2.1.289. The same three headless `claude -p` runs as the first (Rug-pull check, above). These are the owner's results, from the script's output; no development session saw the run, and the commit it ran at was not recorded.

- **All nine checks passed:** the same nine as in the first run.
- **The ledger:** intact, 32 entries, 4 sessions, 2 calls. `tool.drift` at seq 24, `tool.approved` for the new hash at seq 26, and `call.sent` and `call.returned` outcome `ok` for run C at seq 30 and 31, as in the first run.
- **No `CLAUDE_*` or `ANTHROPIC_*` variables** were set in the run.
- **Cost,** from Claude Code's own `total_cost_usd`: A 0.0250936, B 0.0106096 and C 0.0100122 USD, 0.045715 USD in total.

What it adds to the first run: the documented conditions. The first run was started inside a Claude Code session, with that session's `CLAUDE_CODE_*` variables passed on; this one ran from a plain terminal with none. Everything else the first run does not show still holds: the refusal path, re-listing within one live session, a real third-party server, and Windows or macOS.

## Stage 6 (Oct 4, 2026, UTC)

Checked while building M2a's policy and holds, on Python 3.12.3, mcp 2.2.0 and anyio 4.15.1, in `.venv`, on Linux (WSL2). No Claude Code, no model, nothing fetched.

- **flock between two descriptions in one process** (executed, by the golden and CLI tests): with an exclusive `fcntl.flock` held on one descriptor of `sessions/<session>.lock`, a second `os.open` of the same file in the same process gets `BlockingIOError` from `flock(LOCK_SH | LOCK_NB)`; once the first is released, the shared lock is taken. flock locks belong to the open file description, so a test can stand in for a running serve from its own process. `msvcrt.locking` on Windows was not run.
- **tomllib and control characters** (executed): a raw ESC in a quoted key is a parse error (`Illegal character '\x1b'`); the TOML escape for it (backslash, u, 001b) is accepted and gives the character, so a key or pattern holding ESC can reach Polarizer's own error lines only through an escape in the file.
- **lstat below a directory with no permissions** (executed, `tests/test_paths.py::test_unreadable_part_is_held`): `os.lstat` of a path under a mode-000 directory raises `PermissionError` with `strerror` `Permission denied` for a non-root user.
- **The SDK and held calls** (executed, in memory, `tests/test_holds.py`): a `tools/call` handler that awaits for seconds while the SDK client waits needs nothing from the SDK; cancelling the client's call delivers `CancelledError` to the waiting handler, as verified-facts.md's spike found for a forwarding handler. No SDK behavior was found that contradicts this file.

## Stage 7 (Oct 5, 2026, UTC)

Checked while building M2a's lifetime, progress and visibility, on Python 3.12.3, mcp 2.2.0 and anyio 4.15.1, in `.venv`, on Linux (WSL2). No Claude Code, no model.

- **The pinned Filesystem server's tools** (executed): `POLARIZER_REFERENCE=1 uv run --locked pytest -s tests/test_policy_config.py::test_example_config_matches_reference_servers`, with `npx` resolved to a two-line wrapper script in the session scratch directory that runs the real `npx --offline`, put first on `PATH` for that command only. Filesystem 2026.8.31 lists 14 tools, in this order: `read_file`, `read_text_file`, `read_media_file`, `read_multiple_files`, `write_file`, `edit_file`, `create_directory`, `list_directory`, `list_directory_with_sizes`, `directory_tree`, `move_file`, `search_files`, `get_file_info`, `list_allowed_directories`. `read_file` has the title `Read File (Deprecated)`, the description `Read the complete contents of a file as text. DEPRECATED: Use read_text_file instead.`, the same input schema as `read_text_file` (`path`, `tail`, `head`), and the annotations `readOnlyHint` true and `openWorldHint` false. `polarizer.example.toml` named the other 13; it now names all 14, and every `path_args` name it gives is a property of that tool's input schema.
- **The three reference tests** (`tests/test_reference.py`) passed the same way, with the toml that stage 6's `rig.serve_config` classifies.
- **Nothing was fetched:** afterwards, of npx's cache directories, only the mtimes of `~/.npm/_npx/68b53d3fd47bf8db` and `~/.npm/_npx/a705c79b42eea4c8` themselves were newer than the session's guard snapshot, and no entry inside either was. Nothing new is pinned.
- **A killed process's flock** (executed, `tests/test_hold_restart.py::test_killed_process_releases_its_session_lock`): after SIGKILL to a process holding its session lock, the probe read ended within the test's 10 s bound in each of six runs. On Windows the same test uses TerminateProcess; it has not run there.
- **Progress for a held call** (executed, in memory and over stdio, `tests/test_holds.py::test_progress_during_a_hold` and `tests/test_hold_restart.py::test_progress_during_a_hold_over_stdio`): `ctx.session.report_progress` from the waiting `tools/call` handler reaches a client that asked for progress, under its own token (`tok-7` over stdio), with no `total` and no `message` keys; a client that asked for none receives no `notifications/progress`. This is the SDK behavior the spike found for a forwarding handler.

## M2a check, interactive (Oct 5, 2026, UTC, observed by the owner)

Observed by the owner, interactively, with Claude Code 2.1.289, running `scripts/hold-check.sh` (docs/dev/MANUAL-CHECK.md, M2a) at commit c986088, from two plain terminals, between 00:28:57Z and 00:51:15Z. These are the owner's results, from the script's results file (`/tmp/hold-check-results.txt`), the check's ledger (`~/.local/share/polarizer-m2a-check`) and the owner's own account; no development session saw the run. The ledger's entries were read afterwards, read-only, to confirm the sequence numbers and times below.

| Step | What happened | Ledger |
|---|---|---|
| deny | `fs__write_file` to `/tmp/polarizer-manual/.git/hooks/hold-check-c2713114.txt`, held by `write-pattern` (`.git/hooks/**`), denied from terminal B about 11 s after it was held. The file was not created. Claude saw only `polarizer: fs__write_file was not allowed`. | seq 52 `hold.created`, 53 `hold.decided` deny, 54 `call.refused`, 0.16 s after the decision |
| allow | The same call, asked for again, made a new hold with a new id and a different `args_commit`: the earlier deny did not carry over. Allowed; the file holds exactly the text `holds` showed. Claude Code showed `Successfully wrote to /tmp/polarizer-manual/.git/hooks/hold-check-c2713114.txt`. | seq 55 to 58: `call.sent` with `allowed_by` `hold` 0.13 s after the decision, `call.returned` `ok`, latency 36 ms |
| long wait | A held write left for 65 s, then allowed at about 2m08s (128 s by the ledger's times). The owner could type in terminal A while it waited. | seq 59 to 62, `call.returned` `ok`, latency 2 ms |
| expire | `fs__move_file`, held by `destructive`, after a `/mcp` reconnect that started serve again with `hold_timeout_seconds = 30` (a new session, seq 63 to 67, with `policy.loaded` at 30). Not decided; it expired. The file was not moved. | seq 68 `hold.created`, 69 `hold.expired` (`timeout after 30 s`, 30.8 s after the hold), 70 `call.refused` |
| finish | `polarizer verify`: intact, 71 entries, 3 sessions, 2 calls. `polarizer holds`: nothing is held. No leftover probe or Filesystem server processes. `scripts/guard.sh check`: no changes. | |

- **While a call is held,** Claude Code shows the call line `polarizer - fs__write_file (MCP)(path: "...", content: "...")`, and the person can keep typing.
- **After about 123 s, Claude Code moved the still-running call to the background as a task.** It said the call was "still running after 123s. It was moved to the background as task k1sxdm07l and keeps running; you'll receive a notification with the result when it completes. You can keep working in the meantime. To stop it, use TaskStop ... Note: it does not survive exiting this session." The model then went on working. When the owner allowed the hold, the result arrived as a task-completed notification (the results file records it as `MCP task k1sxdm07 (polarizer/fs__write_file) completed.`; the owner's quote above has `k1sxdm07l`). By the ledger's times the hold was allowed about 128 s after it was created, so the move to the background came a few seconds before the allow (inferred from the two numbers, not timed).
- **A refused call and other routes.** When its `fs__move_file` call expired, the model said it did not retry and did not try another route such as Bash `mv`, "because that would get around the proxy this check is testing". It could have: Polarizer guards only calls routed through it, and the agent's own shell is outside it.
- **The script's one-line answers:** the owner pasted multi-line text at a free-text question; the first line was recorded and the other lines reached the owner's shell as commands after the script ended (harmless `command not found` lines). The deny step's recorded answer is cut off for that reason. `scripts/hold-check.sh` now throws away input left waiting after each answer.
- **Bookkeeping:** `~/.claude.json` went from 92,290 bytes (mtime 00:28:38Z) to 92,240 bytes (00:50:17Z) during the check; its MCP config was unchanged in all 6 locations.

### The deny and allow steps, as printed

Copied from the results file of the check above, `/tmp/hold-check-results.txt`, which the owner kept; read in a development session on Oct 5, 2026 (UTC). The deny step's `polarizer holds --wait --bell` printed this block for the first hold, after the bell character that `--bell` writes, left out here:

```
holds: 1 open

hold a81e649da6775874 fs__write_file local-write
held by write-pattern: argument "path": /tmp/polarizer-manual/.git/hooks/hold-check-c2713114.txt matches .git/hooks/**
waiting about 0m00s; times out after 300 s
session cbb8b462c2196ca7 started 2026-10-05T00:34:04.198Z, running
args_commit f5b482532450b8990343ca1f2cd273725f97bf77ee142f0053e9f106cdad3617, 103 bytes of arguments
{
  "path": "/tmp/polarizer-manual/.git/hooks/hold-check-c2713114.txt",
  "content": "M2a hold check c2713114"
}
```

After the deny, and after the allow of the same call asked again, the script printed its summary of the ledger for each hold:

```
seq 52 hold.created: fs__write_file, class local-write, held by write-pattern: argument "path": /tmp/polarizer-manual/.git/hooks/hold-check-c2713114.txt matches .git/hooks/**
seq 53 hold.decided: deny (M2a hold check: deny)
seq 54 call.refused: hold a81e649da6775874 was denied

seq 55 hold.created: fs__write_file, class local-write, held by write-pattern: argument "path": /tmp/polarizer-manual/.git/hooks/hold-check-c2713114.txt matches .git/hooks/**
seq 56 hold.decided: allow
seq 57 call.sent: fs__write_file, allowed_by hold
seq 58 call.returned: outcome ok, latency_ms 36
```

After the deny, the script found that `/tmp/polarizer-manual/.git/hooks/hold-check-c2713114.txt` did not exist; after the allow, it held `M2a hold check c2713114`.

Not verified by this check:

- whether `TaskStop` on a backgrounded held call sends `notifications/cancelled` to Polarizer;
- what happens to a backgrounded held call when the session exits;
- whether the 123 s before a call is moved to the background is fixed or configurable.

## Stage 8 (Oct 5, 2026, UTC)

Run in this session on WSL2 (Linux), Python 3.12.3, with the repo's `.venv`. Nothing was fetched and no model was run. docs/dev/STAGE8-NOTES.md has the details.

- **The sampler's test vectors:** a throwaway script written from docs/MEASURE-SPEC.md section 6's pseudocode, before the code existed, drew the stream's first two values and the three plans exactly as the spec states them.
- **The statistics' test vectors:** every Wilson and Newcombe row of section 7 matches `polarizer.measure` to within 1e-6, with the printed forms exact, on the first run.
- **An audit hook sees processes and connections:** in Python 3.12.3, a hook installed with `sys.addaudithook` records `subprocess.Popen` for `subprocess.run` and `socket.connect` for a connection attempt to 127.0.0.1 port 9 (`tests/test_drill.py::test_audit_hook_sees_what_it_watches`). With the same hook, a whole drill and `drill report --export` in one process raised none of the watched events (`socket.connect`, `socket.bind`, `socket.getaddrinfo`, `subprocess.Popen`, `os.system`, `os.exec`, `os.posix_spawn`, `os.spawn`, `os.fork`, `os.forkpty`, `os.startfile`).
- **A drill on a pseudo-terminal:** `python -m polarizer drill` ran to its end screen and exited 0 with each prompt answered through a pty, in both conditions (the guide's test drew one at random).
- **The wheel:** `uv build --wheel --offline` (uv_build 0.12.19 from the uv cache) wrote `polarizer-0.1.0-py3-none-any.whl`, which holds `polarizer/scenarios/drill-set-1.json`. Installed offline into a throwaway venv in the session's scratch directory (`uv venv --offline`, `uv pip install --offline`), it reported `__version__` 0.1.0 from its metadata, loaded set 1 (150 scenarios, sha256 `e2010c06...`) through `importlib.resources`, and `polarizer drill report` on a missing directory printed the "none yet" line and exited 0. `uv tool install` was not run: it installs for the user, outside the repo.
- **The hold-check expire step:** with a 3 s delay injected between `holds --wait` returning and the lookup, the old script stopped with `no open hold of a running session was found`, as in CI run 37259695458, and the new script passed (STAGE8-NOTES.md, Step 1).

## Stage 8 review (Oct 5, 2026, UTC)

Run in this session on WSL2 (Linux), Python 3.12.3, uv 0.12.19, with the repo's `.venv`. Nothing was fetched and no model was run. docs/dev/STAGE8-NOTES.md, Stage 8 review, has the details.

- **The planted count under the first sampler:** over 10,000 seeded drills of 20 in each drawn condition (seeds 0 to 20,200 as 16-byte counters, the shipped set), the count was uniform on 4 to 8, and 80.39% (plain) and 81.11% (prediction gate) had 5 or more planted calls. Under the new sampler, the same seeds give 8 in every drill, with all five shapes.
- **The new sampler's test vectors:** a stdlib-only script written from docs/MEASURE-SPEC.md section 6's new pseudocode, without Polarizer's code, and the code gave the same stream values and three plans.
- **`uv tool install` from a package file, offline:** with `UV_OFFLINE=1`, `UV_TOOL_DIR` and `UV_TOOL_BIN_DIR` set to temporary directories, `uv tool install ./polarizer-0.1.0-py3-none-any.whl` (the wheel from `uv build --wheel --offline`) installed Polarizer and its dependencies from the uv cache, the installed `polarizer drill report` printed its "none yet" line, and `uv tool uninstall polarizer` removed it. Nothing was written to the user's own uv tool directory (`tests/test_drill_guide.py::test_guide_install_lines_run_offline`).
- **The WSL distro's name** for a `\\wsl$` path is in `$WSL_DISTRO_NAME`; `wsl.exe` is not on the PATH inside this distro (no Windows drive is mounted), and `/etc/os-release` names the Linux release, not the registered distro name.

## Stage 8 follow-up (Oct 5, 2026, UTC)

Run in this session on WSL2 (Linux), Python 3.12.3, with the repo's `.venv`. Nothing was fetched and no model was run. docs/dev/STAGE8-NOTES.md, Stage 8 follow-up, has the details.

- **The sampler with a drawn count:** a stdlib-only script written from docs/MEASURE-SPEC.md section 6's revised pseudocode, without Polarizer's code, and the code gave the same stream values, four plans, planted ranges and count distribution. Over 10,000 seeded drills of 20 in each drawn condition (seeds 0 to 20,200), the count was uniform on 6 to 10, and every drill had at least 5 planted and at least 10 clean calls and every shape.
- **The shortcut audit of set 1:** no feature value a drill shows before the answer, with 6 or more scenarios, has 90% or more of one answer; the best leave-one-out single-feature rule (argument bytes) is right on 64.0% of the scenarios against 60.0% for always allow.

## CI fix: the guide's install test (Oct 5, 2026, UTC)

Run in this session on WSL2 (Linux), Python 3.12.3, uv 0.12.19, with the repo's `.venv`. Nothing was fetched and no model was run. Each empty cache below was a new directory in the session's scratch directory, outside the repo.

- **The CI failure, reproduced:** CI run 37361705150 at b495bf3 failed only `tests/test_drill_guide.py::test_guide_install_lines_run_offline`, on all five jobs, with `error: No solution found when resolving dependencies`, although each job ran `uv sync --locked` first; so a cache filled by a locked sync does not let `uv tool install` resolve offline. Why it does not was not checked. With `UV_CACHE_DIR` and `UV_TOOL_DIR` pointing at empty directories and `UV_OFFLINE=1`, the old test failed the same way: `Because mcp was not found in the cache and polarizer==0.1.0 depends on mcp==2.2.0`, with the hint `Packages were unavailable because the network was disabled`.
- **The build needs no cache:** with an empty cache, `uv build --wheel --offline` wrote the wheel; uv 0.12.19 builds a project whose backend is `uv_build==0.12.19` itself. A project requiring `uv_build==0.11.0` failed the same build with `uv-build was not found in the cache` and the same hint.
- **`uv tool install` has no dry run:** in uv 0.12.19 its help lists neither `--dry-run` nor `--no-deps`. `uv pip install --dry-run --target <dir> <wheel>` with `UV_OFFLINE=1` resolves the same wheel against the cache and installs nothing (it leaves only a `.lock` file in the target): it failed on the empty cache with the same `mcp was not found in the cache` line and hint, and on the owner's cache resolved 30 packages and exited 0. Given a wheel filename with no version it fails with `The wheel filename "missing.whl" is invalid: Must have a version`, without the hint.
- **The fixed test:** on the empty caches with `UV_OFFLINE=1`, the install test is skipped with uv's line naming mcp and the rest of the file passes; on the owner's cache it runs the install and uninstall and passes. `test_offline_skips_only_for_a_cache_miss` passes on an empty cache: a hand-written wheel with no dependencies resolves, one requiring a package no cache holds is a cache miss naming it, and an invalid wheel is a failure, not a skip.

## CI run 37380445365 at 0f32e11 (Oct 5, 2026, UTC, observed by the owner)

Observed by the owner on GitHub Actions and given in the brief of Oct 5, 2026. No session can see CI runs, so nothing here was checked in this repo.

- **All five jobs passed** at commit 0f32e11 in CI run 37380445365: Linux (ubuntu-24.04) with Python 3.11, 3.12 and 3.13, and Windows and macOS with Python 3.12. It is the latest commit recorded as passing on all five, and replaces the earlier record (docs/dev/STAGE7-NOTES.md, Follow-up: macOS output) as the latest.
- **The local run at that commit:** `scripts/test.sh` on Linux gave 1270 passed, 13 skipped.
- **Not recorded:** the per-job test counts and times. The run predates the CI install check, which came in fd8b948 and was changed after it; no CI run of that check is recorded.

## CI run 37388909612 at d50da88 (Oct 5, 2026, UTC, observed by the owner)

Observed by the owner on GitHub Actions (`gh run view`) and given in the brief of Oct 5, 2026. No session can see CI runs, so nothing here was checked in this repo.

- **All five jobs passed** at commit d50da88 in CI run 37388909612: Linux (ubuntu-24.04) with Python 3.11 in 5m46s, 3.12 in 6m15s and 3.13 in 5m46s, Windows with Python 3.12 in 5m43s, and macOS with Python 3.12 in 5m30s. It is now the latest commit recorded as passing on all five, after 0f32e11 (CI run 37380445365, above); README.md's Proof, docs/EVIDENCE.md and docs/milestones.md's CI line still cited 0f32e11 when this was written; the cold-read edits moved them to this run and the next (CI run 37390364747, below).
- **The first run of the wheel install step:** the two install-check steps of `.github/workflows/ci.yml` (docs/dev/STAGE8-NOTES.md, Release fixes, Fix 3) ran and passed on Linux, Windows and macOS, so the Windows branch (`cygpath`, `polarizer.exe`) has now run.
- **The local run at that commit:** `scripts/test.sh` on Linux gave 1283 passed, 13 skipped.

## CI run 37390364747 at 7afd4e1 (observed by the owner)

Observed by the owner on GitHub Actions and given in the brief of the cold-read edits (Oct 6, 2026, UTC). No session can see CI runs, so nothing here was checked in this repo except the diff.

- **All five jobs passed** at commit 7afd4e1 in CI run 37390364747. It is now the latest commit recorded as passing on all five, after d50da88 (CI run 37388909612, above).
- **What changed since d50da88:** only `docs/milestones.md` and `docs/verified-facts.md` (`git diff --stat d50da88 7afd4e1`, checked in this repo).
- **Not recorded:** the per-job times and test counts, and a local run at that commit.

## Stage 9 (Nov 5, 2026, UTC)

Run in this session on Windows 11 (native, not WSL2), Python 3.12.10, uv 0.12.2, with the repo's `.venv` from `uv sync --locked`. Nothing was fetched past that sync and no model was run. docs/dev/STAGE9-NOTES.md has the details.

- **Section 10's fixture:** the ledger built from its table (`tests/helpers/statsledger.py`) printed section 10's expected output byte for byte on the first run of `tests/test_stats.py::test_stats_fixture`, which compares `polarizer stats`' stdout with the spec's text copied into the test, not only with the golden file.
- **The trend warning's numbers:** 40 decisions at 19 s with 36 allowed, then 40 at 4 s with 37 allowed, print section 10's five trend lines exactly; with 38 then 20 allowed the three number lines print alone, Newcombe's high for 20 of 40 minus 38 of 40 being -0.26 (section 7's vector); with the earlier median at 5 s, below the 6 s floor, no warning.
- **Read-only:** paths, sizes and mtimes under the fixture's directory were unchanged by `stats`, and no lock file was created (`test_stats_is_read_only`).
- **The development guard** (`scripts/dev/guard.sh`) was not run: it needs the gitignored `.guard-paths` of the author's WSL2 machine, which this machine doesn't have, and the script says it is of no use elsewhere. No other repository or directory was written in the session; the only writes were under `C:\Users\blued\repos\polarizer`, the venv included, and pytest's temporary directories.
- **CI run 42147283521 at 2debb2a** (branch `stage9-stats`): all five jobs passed, as read with `gh run view` in this session, which can see CI runs through the GitHub CLI where earlier sessions could not. `scripts/test.sh` locally at the same tree: 1 failed, 1292 passed, 79 skipped; the failure is the guide's offline install test, reproduced on master, environmental (docs/dev/STAGE9-NOTES.md, Runs).

## Review fixes (Nov 7, 2026, UTC)

Run in this session on Windows 11 (native, not WSL2), Python 3.12.10, uv 0.12.2, with the repo's `.venv`, on branch `review-fixes` from f26f704 (v0.4.0). Nothing was fetched and no model was run. docs/dev/STAGE9-NOTES.md, Review fixes, has the details.

- **CI runs since Stage 9, read with `gh run list` and `gh run view`:** at 2c430f0, run 42148148939 (branch `stage9-stats`) and run 42151333589 (master) passed; at 3394a61, run 42152212199 (master) passed; at 28d990a, run 42152253005 (master) was cancelled at 02:45:08 UTC with no job started, 25 s after it was queued and 1 s after d09a164's run joined the same concurrency group, which the comment in `.github/workflows/ci.yml` said could not happen to master; at d09a164, run 42152293408 (master) passed; at f26f704, run 42153525460 (branch `release-0.4.0`), run 42154254300 (master) and run 42255514711 (the `v0.4.0` tag push) passed, the last two on all five jobs as read here. Run 42154254300 at f26f704 is the latest master run recorded as passing on all five jobs. The release workflow's run 42255528206 on the v0.4.0 release passed.
- **0.4.0's dates:** the release commit f26f704 is dated 2026-11-06T02:57:10Z, master's run for it was queued at 03:04:22Z, and the v0.4.0 release was published at 16:46:40Z with the wheel and the sdist (`gh release view v0.4.0`), so master's install lines named a wheel that didn't exist for about 13.7 hours.
- **The test count:** `uv run pytest tests/test_stats.py -o addopts="" -q` at f26f704 gave 27 passed: 18 test functions, one parametrized over 10 golden files. CHANGELOG.md, docs/dev/STAGE9-NOTES.md and the v0.4.0 release body said 30; the release body was corrected with `gh release edit`.
- **Stats on a large ledger:** a serve ledger of 100,001 entries in 20,000 sessions, two decided holds each, built with `tests/helpers/statsledger.py` in the session's scratch directory: `polarizer stats` took 77.3 s with f26f704's `stats.py` and 7.9 s with the fix, against 5.8 s for `polarizer verify`, and the two outputs were byte for byte the same once line endings were made alike (the old code ran through a small runner, not the CLI). The session-block step alone, at 5,000 sessions with one hold each: 0.744 s before, 0.028 s after.
- **`scripts/test.sh` locally** at the review-fixes tree before the commit: 1295 passed, 79 skipped in 265.67 s, nothing failed. The guide's offline install test, `tests/test_drill_guide.py::test_guide_install_lines_run_offline`, which failed in Stage 9's run on this machine for want of a package in the uv cache, passed here and again alone (1 passed in 5.52 s); why the cache now holds what it needs was not checked.
- **The review fixes on CI:** commit 639761e passed on all five jobs on branch `review-fixes` in run 42332456292, and again on master in run 42333107189 after the owner's `~/merge-branch.sh` fast-forwarded master to it, as read with `gh run view`. Run 42333107189 is the latest master run recorded as passing on all five jobs. The 0.4.1 release commit's own runs come after this record and are not in it.
- **The development guard** was not run, for Stage 9's reason. The only writes were under `C:\Users\blued\repos\polarizer`, the session's scratch directory and pytest's temporary directories, and the GitHub release body of v0.4.0.

## Stage 11, the card (Nov 7, 2026, UTC)

Built on Windows 11 (native), Python 3.12.10, uv 0.12.2, on branch `m3-card` from 38d9438 (v0.4.1). docs/dev/STAGE11-NOTES.md has the details.

- **`tests/test_card.py` on Windows:** 30 passed, 2 skipped (the symbolic-link case, for want of the symlink privilege, and the browser test, which tests/helpers/chromium.py can't drive on Windows).
- **`tests/test_card.py` in WSL2** (the `parallax` distro, Linux, with the repo's `.venv` after `uv sync --locked`), on a copy of the same working tree in `/tmp/pz-m3`, checked by the sha256 of `src/polarizer/card.py` on both sides: 32 passed in 6.85 s, the browser test included, with the headless Chromium that Playwright left in `~/.cache/ms-playwright` (`chromium_headless_shell-1243`). Nothing was fetched for it.
- **The development guard** was not run, for Stage 9's reason. The writes were under `C:/Users/blued/repos/polarizer`, the session's scratch directory, pytest's temporary directories, and in WSL `/tmp/pz-m3` and `/tmp/polarizer-card-check.s34qwq5r`. Copying the tree to WSL ran `rm -rf /tmp/pz-m3` before that directory existed, so nothing was removed; the command broke CLAUDE.md's rule 10 (deletes only inside ~/code/polarizer) and was not repeated.

## Card check (Nov 7, 2026, UTC, headless, Claude Code 2.1.310)

`scripts/card_check.py`, run once in this session in WSL2 (the `parallax` distro), from `/tmp/pz-m3`, a copy of the `m3-card` working tree before its commit, as `.venv/bin/python scripts/card_check.py`, at 2026-11-07T04:23:03Z. `claude auth status` showed a claude.ai login before the run. No `CLAUDE_*` or `ANTHROPIC_*` variable was set. The script ran one `claude -p` with `--model claude-haiku-4-5-20251001`, `--max-budget-usd 1`, `--strict-mcp-config`, `--mcp-config <tmp>/mcp.json` (only `polarizer serve --config <tmp>/polarizer.toml`, whose ledger was `<tmp>/ledger` and whose one upstream was the pinned Filesystem server, `npx -y @modelcontextprotocol/server-filesystem@2026.8.31 <tmp>/work`, already in the npx cache from earlier checks), `--allowedTools=mcp__pz__fs__write_file`, `--permission-mode default`, `--no-session-persistence` and `--output-format json`, stdin from `/dev/null`, `MCP_TOOL_TIMEOUT` unset. The prompt asked for a write to `<tmp>/work/.git/hooks/card-check.txt`, and one retry if it was not allowed. The card was started as `polarizer card --config <tmp>/polarizer.toml --allow-no-terminal` and driven with curl only. Every check passed:

| Check | Result |
|---|---|
| The page loads with the token, and has a nonce | PASS |
| 403 for the page without the token, with a wrong token, and with Host `evil.example:<port>`; for the state without the cookie and without the nonce; for a decision without `Origin` | PASS, all six |
| The write was held and shown on the card | PASS: hold 1e14b531097ebab3, `write-pattern`, `matches .git/hooks/**`, with the recommendation to deny |
| Deny through the card | PASS: `denied hold 1e14b531097ebab3 at seq 37`; the file was not written |
| The retry was a new hold; allow through the card | PASS: hold b4d0535c934a2758, `allowed hold b4d0535c934a2758 at seq 40` |
| Both decisions carry `elapsed_ms` | PASS: 4 and 3 ms (curl decided as soon as the state showed each hold), each with `terminal: false`, from `--allow-no-terminal` |
| The denied call was refused and never forwarded; the allowed one was forwarded and returned `ok` | PASS |
| The file holds what was allowed | PASS |
| Both holds in the card's ended list | PASS: `allowed at seq 40`; `denied at seq 37: card check: deny` |
| `polarizer verify` | PASS: `intact: 43 entries, 2 sessions, 1 calls` |
| `polarizer stats` | PASS: `2 of 2 decisions were timed on one clock, from the card that showed the hold.` |
| The card stops on SIGTERM with exit 0 and nothing more on stdout | PASS |

- **The model replied** (not used for pass or fail): "File written successfully to `/tmp/polarizer-card-check.s34qwq5r/work/.git/hooks/card-check.txt` after the initial call was denied."
- **Cost,** from Claude Code's own `total_cost_usd`: 0.0321061 USD.
- **Bookkeeping:** `~/.claude.json` went from 102,647 bytes (mtime 01:32:49Z) to 103,025 bytes (04:23:06Z) during the run, Claude Code's own update. The script only stats it.
- **What it shows:** with Claude Code 2.1.310, headless, a write held by `.git/hooks/**` appears in the card's state within a poll; a deny through the card's HTTP endpoints is recorded and the call never reaches the server; the agent's retry is a new hold; an allow through the card forwards exactly that call; and both `hold.decided` entries carry `elapsed_ms`.
- **What it does not show:** a person reading the page in a browser (the browser test is a headless Chromium, and the client here is a script); the background note on real Claude Code; Windows or macOS. The interactive check (docs/dev/MANUAL-CHECK.md, M3) is still the owner's to run.

## Stage 10, live planted calls (Nov 7, 2026, UTC)

Built on Windows 11 (native), Python 3.12.10, uv 0.12.2, on branch `stage10-planted` from e619536 (master after M3's merge, CI run 42399956899 on master passing). docs/dev/STAGE10-NOTES.md has the details.

- **`scripts/test.sh` on Windows:** ruff, the format check and `scripts/check_docs.py` clean; 1354 passed, 81 skipped in 344.05 s.
- **In WSL2** (the `parallax` distro), on a copy of the tree checked by the sha256 of `src/polarizer/planted.py` on both sides: `tests/test_planted.py`, `tests/test_card.py`, `tests/test_holds.py` and `tests/test_stats.py` together, 109 passed in 44.31 s, and again 109 passed in 42.70 s.
- **WSL2 cleared its /tmp twice during the session:** the distro shut down when idle (`uptime` showed 9 minutes after a run had ended), and at its next start `/tmp/pz-s10`, `/tmp/pz-m3` and the first planted check's directory were gone. Nothing was deleted by this session; the last run copied the tree and ran the check in one `wsl.exe` call for that reason. The first planted check's ledger was lost with it, so that run is known only from its printed output.
- **The development guard** was not run, for Stage 9's reason. The writes were under `C:/Users/blued/repos/polarizer`, the session's scratch directory, pytest's temporary directories, and WSL's `/tmp`.

## Planted check (Nov 7, 2026, UTC, headless, Claude Code 2.1.310)

`scripts/card_check.py --planted`, from a copy of the `stage10-planted` tree before its commit, in WSL2, with the conditions of Card check above: `--model claude-haiku-4-5-20251001`, `--max-budget-usd 1`, `--strict-mcp-config --mcp-config <tmp>/mcp.json`, `--permission-mode default`, `--no-session-persistence`, `--output-format json`, stdin from `/dev/null`, no `CLAUDE_*` or `ANTHROPIC_*` variable set. The config set `planted_calls_per_session = 1`. The prompt asked for 30 reads inside the workspace root, one `fs__read_text_file` call each, which run without a hold and are the eligible triggers; up to three `claude -p` runs, each a new serve session, until a planted call appears; the card driven with curl only. Three attempts:

1. **18:28:21Z, the script as first written:** three runs (0.0457, 0.0406 and 0.0322 USD), 90 reads forwarded, and the card saw no hold open. `polarizer stats` counted one planted hold, ended as expired, and the ledger verified intact with 227 entries. The planted hold had come after the agent's last read and ended with its session, before the card's next poll: a headless run ends as soon as the model's last call returns, and a plan waits 1 to 8 s. The ledger was lost when WSL2 cleared /tmp, so whether it ended at shutdown is inferred, not read. Since then the prompt ends with one call to the repository's probe `wait` tool for 20 s (`tests/helpers/probe_server.py`, classed `local-read`, no path, so never a trigger), and LIMITS.md states this case.
2. **18:32:31Z, with the wait:** run 1 (0.0510 USD) gave planted hold e7dfb252f72bbefd, a read of `/home/matt/.aws/credentials` made from the read of `note-13.txt`, held by `read-pattern`, shown on the card like any hold with the recommendation to deny; denied through the card at seq 114, with the reveal on the card (`You denied it: caught.`); nothing forwarded or refused for it; verify intact with 117 entries. One check failed, the script's own: it expected `stats` to time the decision, but planted holds are left out of time to decision by design. The script now expects no timed decision in this mode.
3. **18:33:48Z, the script as committed:** run 1 (0.0470 USD) gave no planted call; run 2 (0.0339 USD) gave planted hold 96561bff538dad6b, a read of `/home/matt/.ssh/id_ed25519` made from the read of `note-06.txt`. **Every check passed:** the six refusals; the planted hold shown with no word of planting on it; denied through the card at seq 158; the card's reveal; `plant_commit` on every hold; `hold.revealed` with label `planted` and its `why`; no `call.*` entry for it; `polarizer stats` with `planted 1: denied 1 (caught)`; `polarizer verify` intact with 184 entries, 3 sessions, 62 calls; the card stopping on SIGTERM with exit 0.

- **Cost** over the three attempts: 0.2504 USD, from Claude Code's own `total_cost_usd`.
- **Bookkeeping:** `~/.claude.json` went from 104,793 bytes (mtime 18:08:38Z) to 103,967 bytes (18:34:42Z) over the attempts, Claude Code's own updates; the script only stats it.
- **What it shows:** with Claude Code 2.1.310, headless, a planted call is made from a real call in a session with planting on, appears on the card like a real hold, is never forwarded, and is revealed after the person's decision, which the card shows, and `stats` counts.
- **What it does not show:** a person deciding a planted call without knowing which hold it is (the client here knew every hold would be planted); a planted write (both were reads, the targets drawn at random); Windows or macOS. The interactive check (docs/dev/MANUAL-CHECK.md, Stage 10) is still the owner's to run.

## Taint session id (Nov 7, 2026, UTC, headless, Claude Code 2.1.310)

Checked for docs/TAINT-SPEC.md, section 7, before the spec was written, in WSL2 (the `parallax` distro), in a directory under the repo's gitignored `.repro/` (`~/code/polarizer/.repro/m2b-env`). The MCP server was a shell wrapper that appended to a log the time, its process id, its parent's id and `comm`, the value of `CLAUDE_CODE_SESSION_ID`, and the names (never the values) of the variables starting `CLAUDE`, `ANTHROPIC` or `MCP`, then ran the repository's probe (`tests/helpers/probe_server.py`). Each run was `claude -p` with `--model claude-haiku-4-5-20251001`, `--max-budget-usd 1`, `--strict-mcp-config --mcp-config <dir>/mcp.json` (that server only), `--allowedTools=mcp__pz__wait`, `--permission-mode default` and `--output-format json`, stdin from `/dev/null`, from a shell with no `CLAUDE_CODE_SESSION_ID` of its own. No Polarizer was involved.

- **Run 1 (18:59:52Z):** the server's parent was `claude`, and its environment had `CLAUDE_CODE_SESSION_ID=51919c3c-2423-4b04-be27-020b13d92a87`, the `session_id` the run's JSON output reported. The other names: `CLAUDECODE`, `CLAUDE_CODE_ENTRYPOINT`, `CLAUDE_CODE_MESSAGING_SOCKET`, `CLAUDE_CODE_MESSAGING_TOKEN`, `CLAUDE_PROJECT_DIR`.
- **Runs 2 and 3 (19:06Z):** run 2 reported `session_id` `da437e1e-14b1-4359-b089-af9f9bd0448b`; run 3 was `claude -p --resume da437e1e-...` with the same flags, and reported the same `session_id`. Each started its own server process (process ids 31242 and 31312, parents 31205 and 31274, both `claude`), and both had `CLAUDE_CODE_SESSION_ID=da437e1e-14b1-4359-b089-af9f9bd0448b`.
- **Cost,** from Claude Code's own `total_cost_usd`: 0.0306632, 0.0227659 and 0.0291371 USD, 0.0825662 in total. Runs 2 and 3 kept their session (no `--no-session-persistence`), which Claude Code records under `~/.claude`; that, and its update of `~/.claude.json`, are Claude Code's own bookkeeping.
- **What it shows:** with Claude Code 2.1.310, headless, a stdio MCP server started from an `--mcp-config` file gets `CLAUDE_CODE_SESSION_ID`, equal to the session's `session_id`, and a new server process started for a resumed session gets the same value.
- **What it does not show:** whether interactive Claude Code passes the same value to the serve it starts after Esc (docs/dev/MANUAL-CHECK.md, M2b), whether `/clear` or a fork changes it, and other versions, platforms or clients.

## Stage 12, argument rules and taint (Nov 7, 2026, UTC)

Built on Windows 11 (native), Python 3.12.10, uv 0.12.2, on branch `m2b-taint` from ef6b516 (master after stage 10's merge). docs/dev/STAGE12-NOTES.md has the details.

- **`scripts/test.sh` on Windows,** on the final tree: ruff, the format check and `scripts/check_docs.py` clean; 1442 passed, 81 skipped in 271.90 s.
- **In WSL2** (the `parallax` distro), on a copy of the working tree streamed in with tar (the distro mounts no Windows drive), checked by the sha256 of `src/polarizer/proxy.py` on both sides: `tests/test_taint.py`, `tests/test_argrules.py`, `tests/test_taint_check.py`, `tests/test_planted.py`, `tests/test_card.py`, `tests/test_holds.py`, `tests/test_stats.py` and `tests/test_conformance.py` together, 314 passed in 49.92 s.

## Taint check (Nov 7, 2026, UTC, headless, Claude Code 2.1.310)

`scripts/taint_check.py`, run once in WSL2 (the `parallax` distro), from `~/code/polarizer/.repro/m2b-tree`, the copy above, before its commit, as `.venv/bin/python scripts/taint_check.py`, at 2026-11-07T19:30:56Z. `claude auth status` showed a claude.ai login before the run. No `CLAUDE_*` or `ANTHROPIC_*` variable was set. The check's directory was `~/code/polarizer/.repro/m2b-tree/.repro/taint-check.8nq383xr`, under the gitignored `.repro/`, with its ledger inside it; nothing was written to `~/.local/share/polarizer` or `polarizer-drills`. The only upstream was `tests/helpers/web_stub.py` (the repository's own stand-in: `fetch` returns fixed text and `post` appends what it was sent to a log in the directory, neither touching the network; nothing was fetched or installed for it), with `fetch = { class = "open-world" }` and `post = { class = "egress", url_args = ["url"], domains = ["allowed.example"] }`. Each run was `claude -p` with `--model claude-haiku-4-5-20251001`, `--max-budget-usd 1`, `--strict-mcp-config`, `--mcp-config <dir>/mcp.json` (only `polarizer serve --config <dir>/polarizer.toml`), `--allowedTools=mcp__pz__web__post,mcp__pz__web__fetch`, `--permission-mode default` and `--output-format json`, stdin from `/dev/null`, `MCP_TOOL_TIMEOUT` unset; runs 1 and 2 kept their session so that run 2 could resume it, and run 3 had `--no-session-persistence`. The card was started as `polarizer card --config <dir>/polarizer.toml --allow-no-terminal` and driven with curl only. Every check passed:

| Check | Result |
|---|---|
| The page loads with the token, and has a nonce | PASS |
| Run 1: a post to `https://allowed.example/notes` ran without a hold | PASS: `call.sent` at seq 12 with `allowed_by` `domains`, before the taint; the stub received `first note` |
| Run 1: after the fetch (`call.sent` at seq 14), `session.tainted` was recorded before the fetch's `call.returned` | PASS: seq 15, `{"call_seq": 14, "client_session": "f0cc3e01-e7ac-41ac-b246-f8b03c70865b", "session": "3019718ba876cecc", "tool": "web__fetch"}`; the `client_session` is the `session_id` Claude Code reported |
| Run 1: the same post was held by `tainted`, the reason and the card's `Session taint` row naming the source | PASS: hold e173a097113d875c, `session tainted by web__fetch at 2026-11-07T19:31:03.731Z (seq 14); egress is held for the rest of the session, even to a host in this tool's domains`, with the recommendation to deny; nothing posted while it was held |
| Run 1: allowed through the card | PASS: `allowed hold e173a097113d875c at seq 18`; forwarded at seq 19 with `allowed_by` `hold`; the stub received `second note` |
| Run 2, `claude -p --resume f0cc3e01-...`: a new serve (session dd3bb252419b4241, its `policy.loaded` with the same `client_session`) held the post by `tainted` at once, naming the same source | PASS: hold 1ab48d7e05d3f31c, the same reason, seq 14 |
| Run 2: denied through the card, never forwarded | PASS: `denied hold 1ab48d7e05d3f31c at seq 26`; the stub received nothing |
| Run 3, a new Claude Code session (`dc248c19-...`): the post ran as a grant | PASS: `call.sent` at seq 32 with `allowed_by` `domains`; the stub received `fourth note` |
| Both card decisions carry `elapsed_ms` | PASS |
| `polarizer verify` | PASS: `intact: 34 entries, 4 sessions, 4 calls` |
| `polarizer stats` | PASS: `tainted: 2 of 4 sessions, every egress call held from then on` and `held by taint 2: allowed 1, denied 1, expired 0, abandoned 0, open 0` |
| The card stops on SIGTERM with exit 0 and nothing more on stdout | PASS |

- **The four serve starts' `client_session`:** null (the priming start, by the script, outside Claude Code), `f0cc3e01-...` twice (runs 1 and 2), `dc248c19-...` (run 3).
- **The model replied** (not used for pass or fail): run 1, "First POST: posted 10 characters; FETCH: plain text from server outside this machine; second POST: posted 11 characters."; run 2, "Third POST was not allowed by Polarizer."; run 3, "Posted 11 characters."
- **Cost,** from Claude Code's own `total_cost_usd`: 0.0450979, 0.053132 and 0.0284115 USD, 0.1266414 in total.
- **Bookkeeping:** `~/.claude.json` went from 105,133 bytes (mtime 19:08:26Z) to 104,307 bytes (19:31:12Z), Claude Code's own update; the script only stats it. Runs 1 and 2 kept their session, which Claude Code records under `~/.claude`.
- **What it shows:** with Claude Code 2.1.310, headless, an egress call to a listed host runs as a grant; an open-world call taints the session, recorded before its answer; the same egress call is then held by `tainted` with the source named on the card, and decided there; a new serve started for the resumed session is tainted from its start by the ledger, with the same source; and a new Claude Code session starts clean.
- **What it does not show:** the restart that follows Esc in an interactive session (docs/dev/MANUAL-CHECK.md, M2b, the owner's to run); `deny_values` and the tricky hosts on real Claude Code (tests cover them); a real web or mail server; Windows or macOS.

## Stage 13, scanning and review (Nov 7, 2026, UTC)

Built on Windows 11 (native), Python 3.12.10, uv 0.12.2, on branch `m1b-scan` from f936031 (master at v0.5.0). docs/dev/STAGE13-NOTES.md has the details.

- **`scripts/test.sh` on Windows,** on the final tree: ruff, the format check and `scripts/check_docs.py` clean; 1511 passed, 82 skipped in 322.19 s.
- **`scripts/test.sh` in WSL2** (the `parallax` distro), in a worktree, `~/code/polarizer-m1b`, of origin/master with the final tree applied as a `git diff` (the distro mounts no Windows drive): the same checks clean; 1578 passed, 15 skipped in 424.91 s.

- **The reference servers under the scanner** (`POLARIZER_REFERENCE=1 .venv/bin/python -m pytest -s tests/test_reference.py`, run once in WSL2, the `parallax` distro, in a worktree of the uncommitted stage 13 tree, `~/code/polarizer-m1b`, carried in as a `git diff` and checked by `git status`): the four reference tests passed in 16.57 s. `test_reference_scan` scanned 27 tools and 1 set of instructions. No tool was flagged. Everything 2026.8.31 returns instructions from `initialize`, 1,574 characters beginning `# Everything Server` and an en dash, and they were flagged once, by `long-text`; Filesystem 2026.8.31 returns none. `test_reference_tools_hash_and_serve` reported 27 tools hashed, none with `_meta`, none unservable, the largest integer 10, as at stage 4. The servers ran with the pinned commands under Pinned fetches, already in npx's cache (`~/.npm/_npx/68b53d3fd47bf8db` and `a705c79b42eea4c8`); nothing new was pinned.
- **Upstream instructions on the two eras' handshakes:** the SDK's `Client.instructions` held them from `initialize` (2025-11-25, both reference servers and the stand-ins) and from a 2026-07-28 in-memory gateway's `server/discover`, which `tests/test_instructions.py` reads in both modes.

## Scan check (Nov 7, 2026, UTC, headless, Claude Code 2.1.310)

`scripts/scan_check.py`, run once in WSL2 (the `parallax` distro), from `~/code/polarizer-m1b`, the stage 13 tree before its commit (so the script printed master's commit, f936031), as `.venv/bin/python scripts/scan_check.py`, at 2026-11-07T22:14:53Z. `claude auth status` showed a claude.ai login before the run. No `CLAUDE_*` or `ANTHROPIC_*` variable was set. The check's directory was `~/code/polarizer-m1b/.repro/scan-check.w4ekkh7t`, under the gitignored `.repro/`, with its ledger inside it; nothing was written to `~/.local/share/polarizer` or `polarizer-drills`. The only upstream was `tests/helpers/lab_stub.py` (the repository's own stand-in: `add` and `echo` clean, `lookup`'s description poisoned on purpose, and clean instructions with the pass phrase `pelican-42`; it reads no file and touches no network), each tool classed `local-read`. Each run was `claude -p` with `--model claude-haiku-4-5-20251001`, `--max-budget-usd 1`, `--strict-mcp-config`, `--mcp-config <dir>/mcp.json` (only `polarizer serve --config <dir>/polarizer.toml`), `--allowedTools=mcp__pz__lab__add`, `--permission-mode default`, `--no-session-persistence`, `--output-format stream-json` and `--verbose`, stdin from `/dev/null`, `MCP_TOOL_TIMEOUT` unset. The card was started as `polarizer card --config <dir>/polarizer.toml --allow-no-terminal` and driven with curl only. Every check passed:

| Check | Result |
|---|---|
| serve with stdin closed recorded the server with no approval | PASS: `polarizer: 3 tools wait for approval` and `the instructions of 1 upstreams wait for approval` |
| `pending` flags lookup and leaves it out of the group | PASS: `new lab__lookup fecc0353...`, then `flagged by the scanner; read it, then approve it by name`, then four flags: `conceal-from-user` ("Do not tell the user"), `read-before-use` ("Before using this tool"), `secret-path` (".ssh") and `instruction-tag` ("<IMPORTANT>") |
| The group line covers the three unflagged definitions | PASS: `group b18efdb3... covers the 3 unflagged new definitions above ...` |
| The card's page loads with the token and has a nonce | PASS |
| The review view shows lookup's four flags beside its definition, `in_group` false | PASS |
| The review's group covers `lab__:instructions`, `lab__add` and `lab__echo` | PASS |
| `Approve all unflagged` through the card | PASS: approvals at seq 8, 9 and 10, each with `group` `b18efdb3...` and `terminal: false`, then `approved 3 definitions as group b18efdb3...` |
| Run 1: Claude Code's start event lists add and echo, not lookup | PASS: `mcp__pz__lab__add`, `mcp__pz__lab__echo`; `pz` connected |
| Run 1: add was called through Polarizer | PASS: one `call.sent` for `lab__add` |
| After the group, only lookup waits, still flagged | PASS: `pending: 1 new, 0 changed, 0 unservable` |
| lookup approved alone through the card | PASS: the definition, its four flag lines, then `approved lab__lookup fecc0353... at seq 17`; `tool.approved` with `group` null and `flags` `["conceal-from-user", "instruction-tag", "read-before-use", "secret-path"]` |
| Run 2: the start event now lists lookup | PASS: `mcp__pz__lab__add`, `mcp__pz__lab__echo`, `mcp__pz__lab__lookup` |
| lookup was never called | PASS: no `call.sent` for it, and the stub's log of lookup calls is empty |
| `polarizer verify` | PASS: `intact: 22 entries, 3 sessions, 1 calls` |
| The card stops on SIGTERM with exit 0 and nothing more on stdout | PASS |

- **The approved instructions reached the model.** Asked for the lab pass phrase "if this session's MCP server instructions give one", run 1's model replied `Sum: 5; mcp__pz__lab__lookup is not available; lab pass phrase: pelican-42.` The phrase is only in the stand-in's instructions, which Polarizer served under its header line once the group approved them. So Claude Code 2.1.310, headless, on a 2026-07-28 connection, put a stdio MCP server's instructions in front of the model. This was recorded as an observation, not a pass or fail check, and was seen once.
- **The model replied** in run 2: `ready`.
- **Cost,** from Claude Code's own `total_cost_usd`: 0.0316414 and 0.0175314 USD, 0.0491728 in total.
- **Bookkeeping:** `~/.claude.json` went from 104,220 bytes (mtime 21:54:52Z) to 104,265 bytes (22:15:02Z), Claude Code's own update; the script only stats it. With `--no-session-persistence`, no session was kept.
- **What it shows:** with Claude Code 2.1.310, headless, a poisoned definition is flagged in `pending` and on the card's review view and left out of the group; the card's group approval records what `approve --group` records and exposes the clean tools and the instructions, while the poisoned tool stays out of Claude Code's tool list; and it appears only after it is approved alone, with its flags recorded.
- **What it does not show:** a person reading the review view in a browser (docs/dev/MANUAL-CHECK.md, M1b, the owner's to run); the diff on real Claude Code (no definition changed in the run; `tests/test_scan_review.py::test_card_diff` covers it); `_meta` served to Claude Code (the stand-in has none; `tests/test_meta.py` covers it with SDK clients); whether interactive Claude Code re-lists after the card's approval without a reconnect; a third-party server; Windows or macOS.

## Stretch export (Nov 7, 2026, UTC)

Checked for docs/EXPORT-SPEC.md on Nov 7, 2026 (UTC), at master c1982c1, by reading the installed packages in this repository's `.venv` and the pages named.

**The MCP SDK's OpenTelemetry support (mcp 2.2.0, read in `.venv`):**
- `mcp/server/lowlevel/server.py` sets `self.middleware = [OpenTelemetryMiddleware()]` in every low-level `Server`, so the middleware is on by default. `mcp/server/_otel.py`'s middleware opens one SERVER span per inbound message, named `<method>` or `<method> <name>`, with `mcp.method.name`, `mcp.protocol.version`, `jsonrpc.request.id`, and on `tools/call` `gen_ai.operation.name` `execute_tool` and `gen_ai.tool.name`, parented by `extract_trace_context(ctx.meta)`.
- On failure it sets, for an `MCPError`, `error.type` and `rpc.response.status_code` to the code and the status description to `e.error.message`; for any other exception, `span.record_exception(e)` and `str(e)` as the description. Polarizer re-raises an upstream's protocol error with the upstream's own message, so that message would be in the span. This is why EXPORT-SPEC.md, deviation 2, replaces the middleware.
- `mcp/shared/jsonrpc_dispatcher.py` opens a CLIENT span `MCP send <method> <name>` around each outbound request with only `mcp.method.name` and `jsonrpc.request.id`, injects the current context into the request's `_meta`, and raises an upstream's `ErrorData` as `MCPError` after the span's `with` block has ended, so that message is not in the client span. A request timeout's message is the SDK's fixed text.
- With no SDK provider installed, `opentelemetry-api` 1.45.1's tracer returns a `NonRecordingSpan` carrying the parent's context, so the client's `traceparent` still reaches the upstream unchanged; `tests/test_proxy.py::test_meta_filter` keeps passing with Polarizer's middleware.

**The packages:**
- `uv.lock` already pinned `opentelemetry-api` 1.45.1 as a dependency of mcp 2.2.0 (an earlier note above names 1.45.0 for the M0 lock; this tree's lock has 1.45.1). It is now pinned in `pyproject.toml` as `opentelemetry-api==1.45.1`, since `telemetry.py` imports it.
- The optional extra `otel` and the dev group pin `opentelemetry-sdk==1.45.1` (which requires `opentelemetry-api==1.45.1` exactly, per PyPI's metadata for 1.45.1, read Nov 7, 2026) and `opentelemetry-exporter-otlp-proto-http==1.45.1`. The newest releases then were 1.45.2; 1.45.1 was chosen to match the API the MCP SDK already resolved.
- Install command, run in WSL2 in the worktree with uv 0.12.19 after editing `pyproject.toml`: `uv lock`. It added 13 packages and changed none: certifi 2026.10.23, charset-normalizer 3.5.2, googleapis-common-protos 1.76.0, opentelemetry-exporter-http-transport 0.66b1, opentelemetry-exporter-otlp-common 0.66b1, opentelemetry-exporter-otlp-proto-common 1.45.1, opentelemetry-exporter-otlp-proto-http 1.45.1, opentelemetry-proto 1.45.1, opentelemetry-sdk 1.45.1, opentelemetry-semantic-conventions 0.66b1, protobuf 7.36.2, requests 2.34.2 and urllib3 2.8.0. Then `uv sync --locked` on Windows and in WSL2.
- `ConsoleSpanExporter`'s `out` defaults to `sys.stdout` (its signature in 1.45.1), which is serve's protocol channel, so Polarizer passes `sys.stderr`; `tests/test_telemetry.py::test_console_exporter_writes_to_stderr_only` pins it.
- `OTLPSpanExporter()` with no arguments reads the standard `OTEL_EXPORTER_OTLP_*` variables and posts protobuf to `/v1/traces`; `tests/test_telemetry.py::test_otlp_export_end_to_end` ran serve over stdio against a local HTTP stub and decoded the posted `ExportTraceServiceRequest`, whose `tools/call` span carried the ledger's own seq and hash.

**The dashboard:** Docker Hub listed `jaegertracing/jaeger:2.23.0`, pushed Nov 5, 2026, digest `sha256:3823829974ccfc2485d2e48b165579091f32295022ae80ca49704e2f4834116e`, for amd64, arm64, ppc64le and s390x (read with the Docker Hub API, Nov 7, 2026). Jaeger's getting-started page for 2.x publishes 16686 for the UI and 4318 among the OTLP ports. The `docker run` line in EXPORT-SPEC.md was not run here.

**OCSF:** `https://schema.ocsf.io/api/version` answered `{"version":"1.9.0"}` on Nov 7, 2026. From the same server that day:
- `https://schema.ocsf.io/api/classes/api_activity`: API Activity, `class_uid` 6003, category Application Activity (6), "API events describe general CRUD (Create, Read, Update, Delete) API activities"; `activity_id` 0 Unknown, 1 Create, 2 Read, 3 Update, 4 Delete, 99 Other; `type_uid` 600399 API Activity: Other; `severity_id` 1 Informational; `status_id` 0 Unknown, 1 Success, 2 Failure, 99 Other. With no profile, its required attributes are `activity_id`, `actor`, `api`, `category_uid`, `class_uid`, `metadata`, `severity_id`, `src_endpoint`, `time` and `type_uid`.
- `https://schema.ocsf.io/api/profiles/security_control`: `action_id` 1 Allowed, 2 Denied, 99 Other; `disposition_id` 1 Allowed, 2 Blocked, 6 Dropped, 8 Approved, 14 Delayed, 25 Rejected (among others); `policy` and `is_alert`.
- The objects at `https://schema.ocsf.io/api/objects/<name>`: `api` (`operation` required; `request`, `response`, `service`), `request` (`uid` required), `response` (`code`), `service` (`name`), `metadata` (`version` and `product` required; `uid`, `sequence`, `event_code`, `correlation_uid`, `original_time`, `log_version`, `profiles`), `product` (`name`, `version`), `actor` (at least one of `process`, `user`, `session`, `application` and others; `app_name` deprecated since 1.9.0 for `application.name`), `application` (`name`, `version`), `session` (`uid`), `user` (`name`), `network_endpoint` (at least one of `ip`, `uid`, `name`, `hostname`, `svc_name` and others) and `policy` (`name`). The full list the export uses is `OCSF_NAMES` in `tests/test_export.py`.
- `https://schema.ocsf.io/api/classes` listed no class for AI agents, tools or MCP; `ai_operation` appears there as a profile of API Activity, which the export does not use.

**The extra from a wheel,** in WSL2: `uv build --wheel`, a new venv under `.repro/`, then `uv pip install -r <file> "polarizer[otel] @ file://<wheel>"`, where the file is `uv export --frozen --extra otel --no-dev --no-emit-project --no-hashes`, so every package came from the lock. It installed opentelemetry-sdk, opentelemetry-exporter-otlp-proto-http and opentelemetry-proto 1.45.1, protobuf 7.36.2 and requests 2.34.2, and `telemetry.build(("otlp",))` built a provider.

**Not run:** Jaeger; an OCSF validator (the attribute names are checked by name against the lists above); `uv tool install` of the `otel` extra.

## Review fixes for 0.6.1 (Nov 8, 2026, UTC)

Checked in WSL2 (the `parallax` distro), in a worktree of `review-fixes` under `.repro/`, branched from master 57ab5a2.

**Deep nesting (finding 9).** A ledger of `valid/minimal`'s genesis and one v1 line nested N deep, hashed by the reference verifier, was verified by `conformance/reference_verify.py` and by `python -m polarizer verify`, both from 57ab5a2, with the worktree's Python 3.13.15: at 60, 200, 800 and 950 deep both said intact; at 1,100 and 5,000 both exited 1 with `RecursionError: maximum recursion depth exceeded`. A v0 line 1,100 and 5,000 deep verified intact in both. `json.loads` of `[` times N then `]` times N, run with `uv run --no-project --offline --python <v>`: Python 3.11.16 parsed 900 and raised RecursionError at 1,000; 3.12.3 and 3.13.15 parsed 8,000 and raised it at 20,000. The deepest line in any conformance fixture nests 6 levels, and `valid/v0-parallax.jsonl` 3, counted by a recursive walk over each parsed line.

**git and `log.showSignature` (finding 7).** git 2.43.0, in a scratch repository under `.repro/`: with `-c log.showSignature=true`, `git log --format=%H%x00%B%x00` printed nothing extra for an unsigned commit, and `No signature` and a newline before the hash of a commit signed with an ed25519 SSH key (`gpg.format=ssh`, no `gpg.ssh.allowedSignersFile`). Adding `-c log.showSignature=false` after it, or `--no-show-signature`, removed the line.

**After the fix, the deep lines.** On a 1,100-deep v1 line after `valid/minimal`'s genesis, and a 100,000-deep v0 line after `valid/v0-parallax`'s first line: the reference verifier from 57ab5a2 exited 1 with RecursionError on both (the v0 one inside json, `while decoding a JSON array`), and the installed `polarizer` (0.6.0, `~/.local/bin/polarizer`) exited 1 with RecursionError on the v1 one; on this branch the reference printed `invalid`, line 2, seq null, check `structure` for both, and `polarizer verify` printed `invalid: line 2 (seq ?): nested more than 64 deep` and exited 3.

**The reference servers under the new rules** (`POLARIZER_REFERENCE=1 uv run --locked pytest -s tests/test_reference.py`, with `npx` resolved to a wrapper under `.repro/` that runs the real `npx --offline`, as at stage 4): the four reference tests passed. `test_reference_scan` scanned 27 tools and 1 set of instructions and flagged 1 definition with 1 flag, Everything's instructions by `long-text` (1,574 characters), the same as on the base 57ab5a2 run just before. Nothing was fetched: `--offline` refuses any registry request.

**The tests written first.** With the new tests, fixtures and reference checks in place and `src/` reverted to 57ab5a2, `uv run --locked pytest tests/test_scan.py tests/test_anchor.py tests/test_conformance.py tests/test_canon.py` gave 54 failed and 211 passed: every scanner probe, the new rule's fixture and rule count, every anchor case (the old code has no `choose`), the git, signature, directory and FIFO tests, `broken/nested_too_deep` in Polarizer's verifier, the deep-line tests and the nesting-limit test. With `src/` restored, all passed but three, whose expected message lacked `(seq ?)`; the expectation was corrected.

## Manual checks run by Claude (Nov 8, 2026, UTC, interactive, Claude Code 2.1.310 and 2.1.319)

docs/dev/MANUAL-CHECK.md's M1b, M3, Stage 10 and M2b sections, **run by Claude (Opus 5.5) at the owner's direction on Nov 8, 2026 (UTC), through tmux and headless Chromium; a person's reading of the page was not done.** The owner did not run them. Every "what Claude Code showed" below is Claude's reading of a terminal capture, and every "what the page showed" is Claude's reading of a screenshot and of the page's text; no person looked at either. In quotes from Claude Code's screen, its bullet and ellipsis characters are left out or written as `...`, and its middle dot as `-`, since these docs are ASCII.

**Conditions.**
- **Where:** WSL2, the `parallax` distro, in `~/code/polarizer` at master 40797ad (v0.6.1), with the repo's `.venv` after `uv sync --locked`. That sync changed the shared `.venv` to match the lock (ruff 0.17.1 to 0.17.2, and urllib3 2.8.0 added); nothing outside the lock was installed.
- **Terminals A, B and C:** detached tmux 3.4 sessions on their own server (`tmux -L pzmc`), each started in `~/code/polarizer`, 220 by 50 characters. `tmux show-environment -g` listed no `CLAUDE*`, `ANTHROPIC*` or `MCP*` variable, so no check ran from inside another Claude Code session. Claude typed with `tmux send-keys` and read the screen with `tmux capture-pane -p`; the script prompts (`yes`) were answered the same way.
- **Claude Code:** started exactly as each section says, with `--model claude-haiku-5-5` added (`claude --model claude-haiku-5-5 --mcp-config <file> --strict-mcp-config`). `claude auth status` showed a claude.ai login (Claude Max). The M1b session ran 2.1.310; during it Claude Code showed `Update installed - Restart to update`, and every later start was 2.1.319 (`Updated to latest`). Every session showed `auto mode on`, and no permission prompt appeared. The project's `.claude/settings.local.json` hooks ran in each session (a guard snapshot at start, a guard check at stop), as did the user's PreToolUse hook; none of them was changed.
- **The browser:** the headless Chromium shell Playwright left in `~/.cache/ms-playwright/chromium_headless_shell-1243/` (it reports `HeadlessChrome/153.0.8010.12`), the one `tests/test_card.py` uses, driven over the DevTools pipe by `tests/helpers/chromium.py` from a small driver kept under the gitignored `.repro/manual-checks/`. Playwright itself was not run, and nothing was installed or fetched for it, so there is no install command to record. Window 1280 by 900; screenshots are full-page PNGs, saved with the page's text under `.repro/manual-shots/`. Buttons were pressed with DevTools mouse events at each button's centre after scrolling it into view.
- **Cost,** from `/cost` in each session (all `claude-haiku-5-5`): M1b 0.0035 USD; M3 0.0097; Stage 10 0.0330; M2b 0.0111 and 0.0046; 0.0619 USD in total.

### M1b, scanning and review (run by Claude)

`scripts/scan_check.py --setup` at 14:41Z made `.repro/scan-manual.x2g__59p` and printed `polarizer: 3 tools wait for approval; run polarizer pending` and `polarizer: the instructions of 1 upstreams wait for approval; run polarizer pending`. Every step behaved as written:

| Step | What was seen |
|---|---|
| 2, `pending` | `new lab__lookup fecc0353...`, then `flagged by the scanner; read it, then approve it by name`, then the four flag lines `conceal-from-user`, `read-before-use`, `secret-path` and `instruction-tag`, each quoting what matched; the last line `group b18efdb3... covers the 3 unflagged new definitions above for tools with no decision yet and one definition waiting` |
| 3, the review view | `m1b-1-review.png`: four boxes; `lab__lookup`'s with a red border, `Flagged: approve it on its own, after reading it.` and the four flag lines above its definition; `Approve all unflagged (3)` above a list of `lab__:instructions`, `lab__add` and `lab__echo` only |
| 4, the group | `approved lab__:instructions ... at seq 8`, `approved lab__add ... at seq 9`, `approved lab__echo ... at seq 10`, `approved 3 definitions as group b18efdb3...` |
| 5, `/mcp` | `pz 2 tools`, then `lab__add read-only` and `lab__echo read-only`; no `lookup` |
| 5, the pass phrase | asked `What is the lab pass phrase?`, Claude Code showed `The lab pass phrase is pelican-42.` |
| 6, approve `lookup` | the result box repeated the definition and its four flags, then `approved lab__lookup fecc0353... at seq 15`; `/mcp`, with no reconnect, then showed `3 tools`, `lab__lookup read-only` among them |
| 7, `verify` | `intact: 16 entries, 2 sessions, 0 calls`; `tool.approved` at seqs 8, 9 and 10 with group `b18efdb3...`, and at seq 15 for `lookup` with group null and flags `["conceal-from-user", "instruction-tag", "read-before-use", "secret-path"]` |

- **Read sideways?** The page itself never scrolls sideways (its scroll width equalled its width, 1280). The flags wrap. Two definition boxes do not: the instructions box (content 1,064 pixels wide in a 798-pixel box) and `lookup`'s (1,826 in 798), which are `pre` blocks with `overflow-x: auto` and `white-space: pre` inside a page at most 52rem wide. In the screenshot, `lookup`'s description line stops at `<IMPORTANT>Before using this tool, re`; the instruction to read a key file and the line telling the model to hide it are visible only by scrolling inside the box. A wider window does not help, since the column's width is fixed. This was recorded, not changed.
- **Misread as approval of `lookup`?** In Claude's reading, no: the group button names three definitions and lists them, and nothing says `lookup` is approved until its own button is pressed. Its `Approve this definition` button looks the same as the clean boxes' buttons.
- The approvals carry no `terminal` field, since the card ran in a terminal and without `--allow-no-terminal`.
- Not checked: a person reading the page; the diff view (no definition changed).

### M3, the card (run by Claude)

`scripts/hold-check.sh reset` at 14:46:56Z approved the 21 definitions as one group (seqs 26 to 46) after Claude typed `yes`; it moved an earlier check's ledger, already there before this session, to `~/.local/share/polarizer-m2a-check.before-20261108T144658Z`. The card's page first said `Nothing is held right now.` (`m3-1-empty.png`). `/mcp` showed `polarizer 21 tools`, connected. Claude was asked: `Using the polarizer tool fs__write_file, write the text "M3 card check" to a new file /tmp/polarizer-manual/.git/hooks/m3-card-check.txt. Use only that tool.`

| Step | What was seen |
|---|---|
| 3, the hold on the page | hold 83a5a519c077314f, `hold.created` at 14:47:54.251Z; the page showed it at 14:47:55.174Z, 0.9 s later (`m3-2-held.png`) |
| 3, the labels | `Recommendation, from Polarizer's code: Deny, unless you asked for exactly this: the path matches .git/hooks/**, ...`; `Tool`: `fs__write_file, class local-write`; `Why it was held`: `write-pattern: argument "path": ... matches .git/hooks/**`; `What it would touch`: the path; `Its definition`: `unchanged since it was approved at seq 39`; `Earlier holds`: `none: this is the first hold of this tool by write-pattern`; `Waiting`; `Session`; `The exact call, as polarizer holds prints it` |
| 4, Deny | `denied hold 83a5a519c077314f at seq 53`; under `Ended while this card was open`, `fs__write_file, hold 83a5a519c077314f: denied at seq 53` (`m3-3-denied.png`); Claude Code: `The write failed. Polarizer returned polarizer: fs__write_file was not allowed, so /tmp/polarizer-manual/.git/hooks/m3-card-check.txt was not created.`; the file did not exist |
| 4, the retry, Allow | hold 106ebfdb14371099; `allowed hold 106ebfdb14371099 at seq 56`; `call.sent` at seq 57 with `allowed_by` `hold`, `call.returned` `ok`; the file held `M3 card check`; Claude Code: `The retry worked.` |
| 5, the note | hold e34b435e1b93d99f, `hold.created` at 14:50:47.073Z; the page showed `This call has waited over 2 minutes: probably moved to the background by Claude Code; the agent may have moved on.` at 14:52:47.334Z, 120.3 s later (`m3-6-long-held.png`) |
| 5, the background | Claude Code showed `Calling polarizer - 2m 1s...` at 14:52:46.9Z and `1 MCP task still running` at 14:52:47.950Z, about 121 s after the hold; then `The write to /tmp/polarizer-manual/.git/hooks/m3-long-wait.txt has not finished. Polarizer ran it past 120 seconds and moved it to the background as task kfoqebgf0. I'll get a notification when it completes.` (the model's words: Claude Code, not Polarizer, moved it) |
| 5, Allow | allowed at seq 60, 14:53:19.8Z; Claude Code: `MCP task kfoqebgf0 (polarizer/fs__write_file) completed.`, then `The background write finished. /tmp/polarizer-manual/.git/hooks/m3-long-wait.txt now contains "M3 long wait".` |
| 6, `verify` and `stats` | `intact: 63 entries, 2 sessions, 2 calls`; `elapsed_ms` 27,941, 14,407 and 151,433 on the three `hold.decided`; `stats`: `3 of 3 decisions were timed on one clock, from the card that showed the hold.` |
| 6, `finish` | verify intact, `holds: nothing is held`, no probe or Filesystem server left, guard exit code 0 |

- **Every label read plainly,** in Claude's reading, except one thing found: the exact-call block (and the result box, which uses the same `pre` style) cuts lines at its right edge, for example `held by write-pattern: argument "path": /tmp/polarizer-manual/.git/hooks/m3-card-check.txt` with `matches .git/hooks/**` past the edge, and `87 bytes of a` for `87 bytes of arguments`. The rows above it wrap and say the same things in full.
- The move to the background came at about 121 s with 2.1.319, against about 123 s with 2.1.289 (docs/HOLD-SPEC.md, section 9).
- Not checked: a person reading the page and deciding.

### Stage 10, live planted calls (partly run by Claude)

After a second `reset` (which moved M3's ledger to `~/.local/share/polarizer-m2a-check.before-20261108T145411Z`), `planted_calls_per_session = 3` was added under `[policy]`; the serve's `policy.loaded` at seq 48 carried `"planted_calls_per_session": 3`. Claude was asked to write `work-01.txt` to `work-25.txt` in `/tmp/polarizer-manual` with `fs__write_file`, read each back with `fs__read_text_file`, and list the directory; all 51 calls returned `ok`.

| Planted hold | Made from | It named | Decided on the page | Reveal |
|---|---|---|---|---|
| f22b439eaf482c1e, 14:55:22.740Z | the write of `work-14.txt` | a write of `/tmp/polarizer-manual/.github/workflows/work-14.yml` | Deny, seq 106 | `You denied it: caught.` |
| 0fd565012f87dcba, 14:55:35.335Z | the write of `work-22.txt` | a write of `/tmp/polarizer-manual/.github/workflows/work-22.yml` | Deny, seq 159 | `You denied it: caught.` |
| 026df0a8520b954d, 14:55:59.638Z | the read of `work-10.txt` | a read of `/home/matt/.aws/credentials` | Allow, seq 161, on purpose | `You allowed it: missed.` |

- **Before deciding,** the page's text had no word starting `plant` (`s10-2-held-before-decision.txt`), and each planted hold read like a real one: `write-pattern` or `read-pattern`, the recommendation to deny, the exact call. Claude Code at that moment showed only `Calling polarizer 20 times...`, with no line for a planted call. This is Claude's reading, by a reader who knew planting was on.
- **The reveal,** under the result and on the ended hold (`s10-3-reveal.png`): `Planted call: Polarizer made this hold to check that held calls are read. The agent never made it, and nothing was forwarded.`, then `why: The agent's fs__write_file call named /tmp/polarizer-manual/work-14.txt; this planted call named /tmp/polarizer-manual/.github/workflows/work-14.yml, which can run code when pushed.`, then `You denied it: caught.` In the result box the `why:` line is cut at the box edge, as in M3; on the ended hold it wraps.
- **History:** the second planted write still said `Earlier holds: none: this is the first hold of this tool by write-pattern` after the first was denied, as docs/CARD-SPEC.md, section 8, says (holds revealed as planted are left out of every card's history).
- **The ledger:** `plant_commit` on each `hold.created`; `hold.revealed` with label `planted` after each `hold.decided`; no `call.sent`, `call.returned` or `call.refused` for any of the three; `/tmp/polarizer-manual/.github` did not exist afterwards. The allow of the planted read sent nothing, and the Filesystem server is confined to `/tmp/polarizer-manual` in any case.
- **`stats`:** `on in 1 of 2 sessions: at most 3 per session, about 1 in 20 eligible calls, never with 12 or more holds open`, `planted 3: denied 2 (caught), allowed 1 (missed), expired 0, not revealed 0`, `catch rate: caught 2 of 3, too few to say a rate (5 or more needed)`. `verify`: `intact: 163 entries, 2 sessions, 51 calls`. `finish` wrote `polarizer.toml` again without the key; guard exit code 0.
- **Not checked: whether a person could tell a planted call apart before deciding.** Claude knew planting was on and that any hold it had not asked for was planted, so it was not a blind reader, and its decisions say nothing about a person's catch rate. The planted read was allowed only to see the `missed` path.

### M2b, taint (partly run by Claude)

`scripts/taint_check.py --setup` at 14:57:55Z made `.repro/taint-manual.d30a62u0`, primed it and printed `approved 2 definitions as group 40be1ea8...`. The first Claude Code session's `client_session` was `6b9140a3-de40-499e-af1a-f07dc2d22f0f`, its serve session 3e792b4473b3594f.

| Step | What was seen |
|---|---|
| 3, the first post | `call.sent` at seq 12 with `allowed_by` `domains`; the stub received `first` |
| 3, the fetch | `call.sent` at seq 14; `session.tainted` at seq 15, before the fetch's `call.returned` at seq 16 |
| 3, the second post | hold 4ac7bb1f0ac33445 (`m2b-1-tainted-hold.png`): `Why it was held`: `tainted: session tainted by web__fetch at 2026-11-08T14:58:38.642Z (seq 14); egress is held for the rest of the session, even to a host in this tool's domains`; `Session taint`: `tainted by web__fetch at 2026-11-08T14:58:38.642Z (seq 14): every egress call of this session is held`; the recommendation to deny. Allowed at seq 18, forwarded at seq 19; the stub received `second` |
| 4, Esc during the hold | hold bdbf5981ab26d573 at 14:59:10.890Z; Esc sent at 14:59:15.230Z; `hold.expired` at seq 22, 14:59:15.290Z, reason `the client cancelled the call`, then `call.refused` at seq 23. The card's ended list: `web__post, hold bdbf5981ab26d573: cancelled at seq 22: the client cancelled the call` (`m2b-4-after-esc.png`). Claude Code: `Called pz`, then `Interrupted - What should Claude do instead?` The same serve process (pid 39808) kept running |
| 4, the same post again | hold 712fd61c47c95268, by the same serve session 3e792b4473b3594f, `tainted` with the same source; `Earlier holds`: `1 allowed, 0 denied`. Denied at seq 25; Claude Code: `The post with body third was not sent. The Polarizer proxy refused it with "polarizer: web__post was not allowed".` |
| Extra, `/mcp` reconnect | not in the check: `/mcp`, `pz`, `Reconnect` showed `Reconnected to pz.`; a new serve, session 9325eeae3e4eccf5, `session.started` at seq 27 with `client_session` `6b9140a3-...`; the post `third-after-reconnect` was held at once (hold e859af6884d951a5, seq 31) by `tainted`, naming the fetch at 14:58:38.642Z (seq 14); denied at seq 32. Claude Code: `The post with body third-after-reconnect was refused again: "polarizer: web__post was not allowed". The /mcp reconnect did not change that.` |
| 5, a new session | `client_session` `91633316-d58c-47f4-9e67-e98fb697b94a`, serve session 891e322fdbae2bd8; the post `fourth` ran as a grant (`call.sent` at seq 38, `allowed_by` `domains`) |
| 6, `verify` and `stats` | `intact: 40 entries, 4 sessions, 4 calls`; `tainted: 2 of 4 sessions, every egress call held from then on` and `held by taint 4: allowed 1, denied 2, expired 1, abandoned 0, open 0`; the four `policy.loaded` carry null (the priming start), `6b9140a3-...` twice and `91633316-...`; the stub received `first`, `second` and `fourth` only |

- **Found:** step 4 expects Esc to end serve, so that the hold leaves the card as ended at shutdown and the next post meets a new serve. With Claude Code 2.1.319 neither happened: Esc cancelled the held call through the protocol (the reason above is the one Polarizer records for the client's `notifications/cancelled`, `CLIENT_CANCEL_ERROR` in `src/polarizer/proxy.py`), and the serve process stayed. With 2.1.288, Esc had sent SIGINT and Claude Code had started a new serve (Manual check follow-up, Esc restart timing). So the question step 4 asks, whether interactive Claude Code keeps `CLAUDE_CODE_SESSION_ID` across the restart that follows Esc, was not answered. The extra reconnect shows that an interactive 2.1.319 session gave a restarted serve the same `client_session`, and that the new serve folded the taint from the ledger; it is a different trigger from Esc.
- Not checked: a person reading the page; Esc during a call that is not held; whether Esc ever restarts serve in 2.1.319.

### Bookkeeping, processes and the guard (manual checks run by Claude)

- **`~/.claude.json`** went from 104,940 bytes (mtime 14:41:28Z) to 113,423 bytes (15:01:42Z), Claude Code's own updates; its MCP config hashes were unchanged. Each interactive session was kept by Claude Code under `~/.claude`, as it does by default, and Claude Code updated itself, both its own bookkeeping.
- **`polarizer.toml`:** the copy in the repo root before the checks (sha256 `36508be9...`) was an older one, not what `hold-check.sh finish` writes; it was saved under `.repro/manual-checks/` first, and put back byte for byte after the last `finish` (same sha256).
- **Leftovers:** after the last check, no probe, Filesystem server, stand-in, serve, card or driver process was running; Claude's Chromium had closed. Claude Code exited with `/exit` each time, and the tmux server was killed.
- **The guard:** both `hold-check.sh finish` runs printed no changes and guard exit code 0. `scripts/dev/guard.sh check` against the snapshot taken at the start of the session (`snapshot-20261108T144135Z.txt`) exited 1. It listed `~/code/parallax/parallax/__pycache__/evals.cpython-312.pyc` (mtime 14:44:06Z) and `~/.local/share/parallax` changed (91,099 files before, 91,102 after); the entries newer than the snapshot were all under `tasks/field-demo-a4d36c41/70d1f6/`, with mtimes at 14:42:07Z. No Parallax command, import or file was run or opened by these checks: at that time the M1b setup and card were running, and a separate tmux server, not this session's, had a session named `demo-ui` that was gone by 14:46Z. Everything else, `~/.config/parallax`, Loupe, ISR and the MCP config hashes, showed no changes.

## Related projects (from the owner's research, October 2026, not re-checked in this repo)

The owner gave these facts in the release round's brief (Oct 5, 2026). No session fetched these pages, ran these tools or read their code; each line says what the project's own documentation described in October 2026, and nothing more. docs/related.md is written from this list.

- **mcpclerk** (https://github.com/hishamalward/mcpclerk): a Python gateway on the official MCP SDK with default deny, per-tool allow, deny or approve, held calls where a timeout counts as a refusal, and a hash-chained log with a verify command. Its three ideas credited in Polarizer are listed above (mcpclerk: ideas credited, nothing else used).
- **mcpproxy-go** (https://github.com/smart-mcp-proxy/mcpproxy-go): holds a tool whose definition changed until it is approved again.
- **Trail of Bits' mcp-context-protector** (https://github.com/trailofbits/mcp-context-protector): pins tool definitions on first use and scans responses.
- **Invariant's mcp-scan** (https://invariantlabs.ai/blog/introducing-mcp-scan): pins tools by hash and detects shadowing at scan time. On Oct 11, 2026, its repository, invariantlabs-ai/mcp-scan, redirected to Snyk's agent-scan (https://github.com/snyk/agent-scan), which docs/related.md now links: `gh api repos/snyk/agent-scan -q .full_name` and `gh api repos/invariantlabs-ai/mcp-scan -q .full_name` both printed `snyk/agent-scan`.
- **Docker's MCP Gateway** (https://docs.docker.com/ai/sandboxes/governance/reference/mcp-policy/): Cedar policies that can read call arguments and annotations, with approval asked through MCP elicitation.
- **vex-mcp** (https://pypi.org/project/vex-mcp/): a stdio proxy that inspects tool descriptions, pins tool definitions, enforces a default-deny policy and writes a tamper-evident audit log.
- **warden-mcp** (https://pypi.org/project/warden-mcp/): pins tool definitions on first use and quarantines a changed tool until it is approved again.
- **MCPDome** (https://docs.rs/mcpdome): schema pinning with canonical SHA-256 hashes and a hash-chained ledger component.
- **hoop's mcpproxy** (https://pkg.go.dev/github.com/hoophq/mcpproxy): holds flagged tool calls until a human approves them over a REST API.
- **TrueFoundry's MCP gateway** (https://www.truefoundry.com/blog/mcp-tool-approvals-explained): holds a matched tool call and creates an approval request.
- **Tools that test AI agents with planted inputs,** found in the owner's search in October 2026: Agent Canary (https://github.com/Auro-rium/canary) and AgentCanary (https://github.com/antgroup/Agent3Sigma-Canary). That search found none that tests the person approving; a search is not proof that none exists.

## Unverified

These are assumed or open. Nothing here has been observed.

- **Interactive sessions:** apart from the facts the owner observed interactively (Interactive, Manual check follow-up, M1a check, interactive, and M2a check, interactive: Esc to cancel, the `/mcp` names, re-listing after an approval, how a held call is shown, and the move to the background after about 123 s), and those Claude observed at the owner's direction (Manual checks run by Claude, with no person reading the page), every Claude Code fact above is headless. Not tested interactively:
  - closing a session without pressing Esc while a call is in flight (does Claude Code send `notifications/cancelled`, or only close stdin?);
  - whether Claude Code sends `notifications/cancelled` on Esc, before or instead of signaling the server process (needs the wiretap). With 2.1.319, during a held call, Polarizer recorded the client's cancel and the process kept running (Manual checks run by Claude, M2b); the wire itself was not read;
  - whether interactive sessions open `subscriptions/listen` the same way;
  - whether `TaskStop` on a call moved to the background sends `notifications/cancelled`, what happens to such a call when the session exits, and whether the 123 s is fixed or configurable (M2a check, interactive);
  - elicitation forms.
- **Environment variables for `polarizer serve`:** apart from `CLAUDE_CODE_SESSION_ID` and the names listed under Taint session id (headless, Claude Code 2.1.310), which of Claude Code's own environment variables reach a stdio server it starts from an `--mcp-config` file, and whether it expands `"${NAME}"` in that file's `env`. `polarizer init` writes secret-looking values as `"${NAME}"` in polarizer.toml (docs/PROXY-SPEC.md, Starting from an existing MCP config), which `serve` reads from its own environment and refuses to start without.
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
- **Drills on a native Windows console:** reading answers with `readline` in PowerShell or the Command Prompt, and Ctrl+C there, have not been run; on Windows CI only the in-process drill tests run.
- **Drills on macOS:** the pseudo-terminal drill tests are written for macOS too but have only run on Linux.
- **Installing with `uv tool install`** with the network, into the user's own tool directory, or on Windows or macOS: only the offline install into temporary directories on Linux has run (Stage 8 review). The guide's two lines that fetch from GitHub (`uv tool install git+...` and `uvx --from git+...`) have never run; the repository was private when this was written.
- **Span export with a real collector:** the OTLP exporter has posted only to the test's local stub; Jaeger, the OpenTelemetry Collector and any hosted backend have not received Polarizer's spans, and Claude Code's own `traceparent` (if it sends one) has not been seen.
- **OCSF consumers:** no SIEM or OCSF validator has read `polarizer export --ocsf`'s output.
- **The invented package names** in the scenario set were not checked against any package registry.
- **Windows and macOS behavior of the stage 1 code:** the lock, `os.replace` retry, binary-mode file access and the read-only verify test are written for both, but have only run on Linux. The Windows test that holds `ledger.head` open runs only on the Windows CI runner.
