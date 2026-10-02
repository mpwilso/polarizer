# Proxy spec (M0)

This is the behavior contract for `polarizer serve` and the command line. The ledger format is in LEDGER-SPEC.md. The facts this relies on, and how each was checked, are in verified-facts.md.

## Scope

M0 proxies tools only.

- **Handlers:** the proxy registers `tools/list`, `tools/call` and `subscriptions/listen`, and nothing for resources, prompts, completions or logging.
- **Advertised capabilities:** in 2026-07-28 exactly `{"tools": {"listChanged": true}}`. In 2025-11-25 the same, plus the empty `"experimental": {}` the SDK always adds. A test pins both.
- **Requests from an upstream to the client** (elicitation, sampling, roots, or an `InputRequiredResult`) are never forwarded. Polarizer's upstream clients register no sampling, elicitation or roots callbacks, so they advertise none of those capabilities, and a well-behaved upstream won't ask. What happens if one asks anyway is under Calls.

Draft README text for the limits:

> Polarizer 0.1 proxies tools only. Resources, prompts and completions from upstream servers are not exposed. When an upstream server asks the client for input (elicitation, sampling or roots), Polarizer doesn't pass the request on, and the tool call fails. A server on the 2026-07-28 protocol gets a one-line error naming it. A server on an older protocol gets the error it returns itself, recorded as a protocol error, because Polarizer can't see those requests without advertising support for them. Upstreams start once per session; one that fails to start stays off until Claude Code restarts Polarizer.

## Configuration: polarizer.toml

`polarizer serve --config <absolute path>` reads one TOML file with `tomllib`. `--config` is required, and there is no default. A missing `--config` or a relative path is an error (below). A complete example:

```toml
# Where the ledger, ledger.head and argument side files live. Optional; this is the default.
ledger_dir = "~/.local/share/polarizer"

[upstream.probe]
command = "/home/<you>/code/polarizer/.venv/bin/python"
args = ["/home/<you>/code/polarizer/tests/probe_server.py"]
connect_timeout_seconds = 5

[upstream.every]
command = "npx"
args = ["-y", "@modelcontextprotocol/server-everything@2026.8.31", "stdio"]

[upstream.notes]
command = "/opt/notes-mcp/bin/notes-mcp"
args = ["--stdio"]
env = { NOTES_TOKEN = "${NOTES_TOKEN}", NOTES_MODE = "read-only" }
```

**Keys:**
- **Top level:** `ledger_dir` (string, optional; `~` is expanded) and one or more `[upstream.<prefix>]` tables.
- **Each upstream:**
  - `command`: string, required;
  - `args`: list of strings, default empty;
  - `env`: table of strings, default empty;
  - `connect_timeout_seconds`: number from 1 to 20, default 10.
- **Unknown keys** at either level are an error.
- **Relative paths:** `command` and `args` are passed to the operating system as written. Relative paths resolve against Polarizer's working directory, which Claude Code sets, so use absolute paths.

**The prefix** is the table name. It must be 1 to 32 characters of letters, digits, `-` and `_`, with no `__` anywhere and no `_` at the start or end. `__` is the separator in exposed names, so a prefix that contains it or ends in `_` would split ambiguously.

So a prefix matches `[A-Za-z0-9_-]`, is 1 to 32 characters, contains no `__`, and neither starts nor ends with `_`. The 32-character cap leaves room for upstream tool names under SEP-986's 128 characters. Claude Code accepted a 109-character full name headless, so its own limit is above that. An upstream tool's own name may contain `__`. The exposed name is split at its first `__`, which is always the end of the prefix.

**The upstream's environment** is the SDK's minimal default plus the listed keys, never Polarizer's full environment.
- **The default** copies only `HOME`, `LOGNAME`, `PATH`, `SHELL`, `TERM` and `USER` on POSIX. On Windows it copies `APPDATA`, `HOMEDRIVE`, `HOMEPATH`, `LOCALAPPDATA`, `PATH`, `PATHEXT`, `PROCESSOR_ARCHITECTURE`, `SYSTEMDRIVE`, `SYSTEMROOT`, `TEMP`, `USERNAME` and `USERPROFILE`.
- **A value that is exactly `"${NAME}"`** copies that one variable from Polarizer's environment.
- **Any other value** is literal, and a value that contains `${` anywhere else is an error, so nothing is expanded by surprise.

**Errors.** Each goes to stderr as one line, and `serve` exits 2 without starting any upstream:

```
polarizer: serve needs --config <absolute path to polarizer.toml>
polarizer: --config must be an absolute path, got polarizer.toml
polarizer.toml: not found at /path/polarizer.toml
polarizer.toml: line 7: <tomllib's message>
polarizer.toml: unknown top-level key "ledger_path"
polarizer.toml: no upstreams configured
polarizer.toml: ledger_dir must be a string
polarizer.toml: upstream prefix "my__srv" must be 1 to 32 characters of letters, digits, "-" and "_", with no "__" and no "_" at either end
polarizer.toml: [upstream.notes] unknown key "timeout"
polarizer.toml: [upstream.notes] is missing "command"
polarizer.toml: [upstream.notes] "args" must be a list of strings
polarizer.toml: [upstream.notes] "env" must be a table of strings
polarizer.toml: [upstream.notes] "connect_timeout_seconds" must be a number from 1 to 20
polarizer.toml: [upstream.notes] env NOTES_TOKEN: "${NOTES_TOKEN}" is not set in Polarizer's environment
polarizer.toml: [upstream.notes] env NOTES_URL: only a whole "${NAME}" value is expanded
```

## Startup

Everything below happens before the proxy answers Claude Code's first message. Claude Code's server startup limit is `MCP_TIMEOUT`: 30 s by default, read from the binary, and observed firing headless when set lower.

1. **Read the config.** Any error stops here, as above.
2. **Check `ledger_dir`'s location.**
   - **Refuse**, with exit 2, inside a path listed in `.guard-paths` or inside `~/.local/share/parallax` or `~/.config/parallax`: `polarizer: ledger_dir /path is inside /guarded/path, which Polarizer must not write to`.
   - **Warn**, and continue, inside any other git working tree: `polarizer: warning: ledger_dir /path is inside the git working tree /repo`.
3. **Open the ledger** (LEDGER-SPEC.md Part 2): wait up to 2 s for the lock, create the genesis entry on first run, verify, and check or rebuild `ledger.head`. Any status other than `intact` exits with that status's code and one line on stderr.
4. **Append `session.started`** with Polarizer's version and the config hash. The client's name, version and protocol aren't known yet: in 2026-07-28 there's no `initialize`, and they arrive in each request's `_meta`. They are recorded as `session.client` exactly once per process, on the first request or `initialize` that carries them, even when two requests arrive at the same moment. A flag set before the write, under one lock, decides which request writes it.
5. **Connect every upstream in parallel,** each within its own `connect_timeout_seconds`. Connecting means opening the SDK `Client` and listing the tools (see Listing).
   - Each upstream gets an `upstream.connected` entry, with its protocol version and tool count, or its error (`timeout after 10 s`, the exception's one-line message, or a listing limit).
   - A failed or hung upstream is skipped for the session, and the others serve.
   - There is no retry in M0. A skipped upstream returns only when Claude Code restarts Polarizer.
6. **Serve.** With the default timeouts, steps 3 to 5 take at most about 12 s plus ledger verification, well under 30 s.

## Listing

**Exposed names.** Each upstream tool is exposed as `<prefix>__<tool>`. A tool whose exposed name would not match `^[A-Za-z0-9._-]{1,128}$` is left out, and its name is recorded in `upstream.connected` under `skipped_tools`.

**Paging.** `tools/list` follows `next_cursor` for up to 100 pages and 1,000 tools per upstream. Past either limit, that upstream is refused for the session, with the error `more than 100 pages` or `more than 1000 tools` recorded.

**Freshness.** Every `tools/list` from Claude Code lists each connected upstream again with `cache_mode="refresh"`, so a missed change notice costs nothing. Polarizer sets no cache hints of its own.

**Change notices.**
- **From 2026-07-28 upstreams:** Polarizer holds `client.listen(tools_list_changed=True)` open and republishes each event as `ToolsListChanged()` on its own `subscriptions/listen` bus.
- **From older-era upstreams:** it receives `notifications/tools/list_changed` through the `Client`'s `message_handler` and republishes the same way. This path is unverified.
- **To older-era clients:** the server is created with `NotificationOptions(tools_changed=True)`.

## Calls

**Routing.** A call to `<prefix>__<tool>` is split at its first `__`. The left part is the prefix, and the rest, which may itself contain `__`, is the upstream's tool name. The call goes to that upstream's client. An unknown prefix, an upstream that didn't connect, or a tool it didn't list gets a `tools/call` result with `isError` and one line: `polarizer: no tool named <name>`.

**Request `_meta`.**
- **Forwarded:** `traceparent` and `tracestate`.
- **Recorded:** `call.sent` records the names of all other dropped keys in `meta_dropped`, except `io.modelcontextprotocol/*`, which every 2026-07-28 request carries. It also records the value of `claudecode/toolUseId` as `client_call_id`.
- `params.meta` is a plain dict, and over stdio the SDK presents the progress token there as `progress_token`. Polarizer never reads the token.

**Progress** is relayed by passing a `progress_callback` to the upstream call that runs `ctx.session.report_progress(progress, total, message)`. That reports under the caller's own token on any transport, and does nothing if the caller asked for no progress.

**Order.**
1. Write the argument side file.
2. Append `call.sent` (written, not fsynced; LEDGER-SPEC.md).
3. Call the upstream through `client.session.call_tool(name, arguments, progress_callback=..., allow_input_required=True)`, with no `read_timeout_seconds`.
4. Append `call.returned`.

**Outcomes.** These were checked against SDK 2.2 in verified-facts.md:

| Outcome | What the SDK gives Polarizer | What Claude Code gets |
|---|---|---|
| `ok` | a `CallToolResult` without `is_error` | the result, unchanged |
| `tool-error` | a `CallToolResult` with `is_error` true | the result, unchanged |
| `protocol-error` | `MCPError` with any code except -32000 | the same code and message, re-raised as a JSON-RPC error. The upstream's `data` is dropped. |
| `transport-error` | `MCPError` -32000 (the SDK's `CONNECTION_CLOSED`), or any other exception | a result with `isError` and one line: `polarizer: upstream <prefix> failed: <message>` |
| `cancelled` | `CancelledError` in the handler, from Claude Code's `notifications/cancelled` or its timeout | nothing: the SDK has already abandoned the request. Polarizer records the entry in a shielded scope, then re-raises. The SDK's upstream client sends its own `notifications/cancelled` upstream. |
| `unsupported` | an `InputRequiredResult` | a result with `isError` and one line: `polarizer: upstream <prefix> asked the client for <kinds>; Polarizer 0.1 does not forward these` |

`call.returned` records the outcome, plus `error` (the one line) for every outcome except `ok`, and `code` for `protocol-error`.

Notes on the outcomes:
- **Timeouts.** M0 has no transport timeout, and "timeout" is not part of `transport-error`. Polarizer sets no per-call read timeout in M0, so the SDK's local -32001 can't occur. A call is bounded only by Claude Code's own limit, which arrives as `cancelled`.
- **Dead upstreams.** The SDK reports them as `MCPError` -32000, with nothing marking the error as local. An upstream that sends -32000 itself is therefore recorded as a transport error. That's rare, and harmless apart from the label.
- **Older-era requests to the client.** "unsupported" is reliable only for an `InputRequiredResult`. In the older era, an upstream's elicit, sample or roots request is refused by the SDK (-32600 "... not supported") before Polarizer could see it. Detecting it would mean registering callbacks, which would advertise those capabilities and invite the requests. The upstream then usually returns -32600, recorded as `protocol-error`. A 2026-07-28 upstream that calls `ctx.session.elicit_form` instead of returning `InputRequiredResult` fails on its own side (`NoBackChannelError`, -32600), also recorded as `protocol-error`.

## Output streams

- **`serve`:** its stdout carries only the protocol. Its logs and one-line errors go to stderr.
- **Every other command** (`verify`, `repair`) prints its results to stdout. Usage errors from argument parsing go to stderr, with exit code 2.

## Command line

`polarizer verify [--ledger-dir DIR] [--args]` and `polarizer repair [--ledger-dir DIR]`. Without `--ledger-dir`, the directory comes from `ledger_dir` in `./polarizer.toml` if that file exists, and otherwise defaults to `~/.local/share/polarizer`. The output below is exact; placeholders are in angle brackets.

**`verify`**

| Status | stdout | Exit |
|---|---|---|
| intact (v1) | `intact: <n> entries, <s> sessions, <c> calls` and then `chain <chain_id>, head <64 hex> at seq <seq>` | 0 |
| intact (v0) | `intact (v0): <n> entries` and then `head <64 hex> at line <n>` | 0 |
| tampered | `tampered: line <l> (seq <q>): <reason>`, where the reason is `hash does not match the entry`, `prev does not match line <l-1>` or `seq is <a>, expected <b>` | 1 |
| invalid | `invalid: line <l> (seq <q>): <rule>`, where the rule is one of `not valid UTF-8`, `not valid JSON`, `not a JSON object`, `unknown key "<k>"`, `missing key "<k>"`, `float at <path>`, `integer out of range at <path>`, `non-ASCII key at <path>`, `line longer than 16 KiB`, `second genesis`, `first entry is not a genesis`, `v0 and v1 entries mixed`, `v is not 1` | 3 |
| not canonical | `not canonical: line <l> (seq <q>): stored bytes are not the canonical form` | 4 |
| torn tail | `torn tail: <b> bytes after line <l> (seq <q>); run polarizer repair` | 5 |
| truncated | `truncated: ledger ends at seq <q> but ledger.head records seq <h>` | 6 |
| locked | `locked: ledger is locked by another process (waited 2 s); try again or close other Polarizer sessions` | 7 |
| no ledger | `no ledger at <dir>` | 2 |

For `(seq <q>)`, a line that doesn't parse prints `(seq ?)`. `<path>` is a JSON pointer such as `/data/latency_ms`. In the first `intact` line, sessions are counted as `session.started` entries and calls as `call.sent` entries. Within a line, checks run in the order given in LEDGER-SPEC.md (Statuses), and the first problem wins.

**`verify --args`** prints the chain result first. If the chain is intact, it adds one summary line and then one line per side file that isn't matching: missing and tampered files in seq order, then orphans by name.

```
args: <m> matching, <x> missing, <t> tampered, <o> orphaned
missing   seq <q>  args/<commit>.bin
tampered  seq <q>  args/<commit>.bin
orphaned           args/<name>
```

- **Exit codes:** 8 if any side file is tampered; otherwise the chain's code (0 when intact). Missing and orphaned files don't fail it: deletion is how secrets are removed.
- **When the chain isn't intact:** the summary is skipped, and the exit code is the chain's.

**`repair`**

| Situation | stdout | Exit |
|---|---|---|
| torn tail, repaired | `repaired: moved <b> bytes to ledger.jsonl.torn-<seq>-<sha12>; appended ledger.repaired at seq <q>` | 0 |
| already intact | `nothing to repair: ledger is intact` | 0 |
| any other status | `refused: ledger is <status> at line <l>; repair only fixes a torn tail` | that status's code |
| locked | the `locked:` line above | 7 |
| no ledger | the `no ledger at` line above | 2 |

Every row in these tables gets a golden-file test (`tests/golden/`), which runs the command on a fixture and compares stdout byte for byte, along with the exit code.

## Where the ledger lives

Startup refuses a `ledger_dir` inside a guarded repo or Parallax's directories, and warns inside any other git working tree (startup step 2).

The guard (`scripts/guard.sh`) watches other tools' directories by metadata only, never opening contents:
- `~/.local/share/parallax` as a summary (file count, total size, newest mtime and one hash);
- `~/.config/parallax` and `~/isr-notes` line by line. It also watches `~/.claude/settings.json` by metadata, and `~/.claude.json` through hashes of its MCP config. A search by name found no Loupe or ISR directories under `~/.local/share`, `~/.config` or `~/.cache`. Polarizer's own `ledger_dir` is not guarded; it's Polarizer's to write.
