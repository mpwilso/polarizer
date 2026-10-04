# Proxy spec (M0)

This is the behavior contract for `polarizer serve` and the command line. The ledger format is in LEDGER-SPEC.md. M1a's pins change what `serve` lists, refuses and does at shutdown, and add `pending`, `approve` and `reject`. Each change is summarized below, marked (M1a), and PIN-SPEC.md is the full contract for them. The facts this relies on, and how each was checked, are in verified-facts.md.

## Scope

M0 proxies tools only.

- **Handlers:** the proxy registers `tools/list`, `tools/call` and `subscriptions/listen`, and nothing for resources, prompts, completions or logging.
- **Advertised capabilities:** in 2026-07-28 exactly `{"tools": {"listChanged": true}}`. In 2025-11-25 the same, plus the empty `"experimental": {}` the SDK always adds. A test pins both.
- **Requests from an upstream to the client** (elicitation, sampling, roots, or an `InputRequiredResult`) are never forwarded. Polarizer's upstream clients register no sampling, elicitation or roots callbacks, so they advertise none of those capabilities, and a well-behaved upstream won't ask. What happens if one asks anyway is under Calls.

Draft README text for the limits:

> Polarizer 0.1 proxies tools only. Resources, prompts and completions from upstream servers are not exposed. When an upstream server asks the client for input (elicitation, sampling or roots), Polarizer doesn't pass the request on, and the tool call fails. A server on the 2026-07-28 protocol gets a one-line error naming it. A server on an older protocol gets the error it returns itself, recorded as a protocol error, because Polarizer can't see those requests without advertising support for them. Upstreams start once per session; one that fails to start stays off until Claude Code restarts Polarizer. Results pass through the MCP Python SDK, which drops fields the protocol doesn't define.

## Configuration: polarizer.toml

`polarizer serve --config <absolute path>` reads one TOML file with `tomllib`. `--config` is required, and there is no default. A missing `--config` or a relative path is an error (below). A complete example:

```toml
# Where the ledger, ledger.head and argument side files live. Optional; this is the default.
ledger_dir = "~/.local/share/polarizer"

# Directories the ledger must never be inside. Optional. These are added to Parallax's
# runtime and config directories, which are always forbidden.
ledger_forbidden_paths = ["~/code/parallax", "~/code/loupe", "~/code/isr"]

[upstream.probe]
command = "/home/<you>/code/polarizer/.venv/bin/python"
args = ["/home/<you>/code/polarizer/tests/helpers/probe_server.py"]
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
- **Top level:**
  - `ledger_dir`: string, optional; `~` is expanded, and the result must be absolute;
  - `ledger_forbidden_paths`: list of strings, optional; `~` is expanded in each, and each result must be absolute. They are added to `~/.local/share/parallax` and `~/.config/parallax`, which are always forbidden (LEDGER-SPEC.md, Location and permissions). The name is deliberately not "protected paths", which is reserved for M2a's tool-call policy;
  - one or more `[upstream.<prefix>]` tables.
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

**Who reads it.** `serve`, and `verify` and `repair` when given `--config`, read the file through the same parser (`config.py`) and apply every rule below. The one exception: `verify` and `repair` don't require `${NAME}` variables to be set, so they don't need upstream secrets in the shell. Parsing never starts an upstream.

**Errors.** Each goes to stderr as one line, and the command exits 2 (`serve` without starting any upstream). The file's own name (normally `polarizer.toml`) starts each file error:

```
polarizer: serve needs --config <absolute path to polarizer.toml>
polarizer: --config must be an absolute path, got polarizer.toml
polarizer.toml: not found at /path/polarizer.toml
polarizer.toml: cannot read /path/polarizer.toml: <the operating system's message>
polarizer.toml: line 7: <tomllib's message>
polarizer.toml: unknown top-level key "ledger_path"
polarizer.toml: no upstreams configured
polarizer.toml: ledger_dir must be a string
polarizer.toml: ledger_dir must be an absolute path, got data/ledger
polarizer.toml: ledger_forbidden_paths must be a list of absolute paths
polarizer.toml: "upstream" must be a table of [upstream.<prefix>] tables
polarizer.toml: [upstream.notes] must be a table
polarizer.toml: upstream prefix "my__srv" must be 1 to 32 characters of letters, digits, "-" and "_", with no "__" and no "_" at either end
polarizer.toml: [upstream.notes] unknown key "timeout"
polarizer.toml: [upstream.notes] is missing "command"
polarizer.toml: [upstream.notes] "command" must be a string
polarizer.toml: [upstream.notes] "args" must be a list of strings
polarizer.toml: [upstream.notes] "env" must be a table of strings
polarizer.toml: [upstream.notes] "connect_timeout_seconds" must be a number from 1 to 20
polarizer.toml: [upstream.notes] env NOTES_TOKEN: "${NOTES_TOKEN}" is not set in Polarizer's environment
polarizer.toml: [upstream.notes] env NOTES_URL: only a whole "${NAME}" value is expanded
```

## Startup

Everything below happens before the proxy answers Claude Code's first message. Claude Code's server startup limit is `MCP_TIMEOUT`: 30 s by default, read from the binary, and observed firing headless when set lower.

1. **Read the config.** Any error stops here, as above.
2. **Check `ledger_dir`'s location** (LEDGER-SPEC.md, Location and permissions). `repair` runs the same check, with the same messages.
   - **Refuse**, with exit 2, inside any `ledger_forbidden_paths` entry or inside `~/.local/share/parallax` or `~/.config/parallax`, comparing resolved real paths by components: `polarizer: ledger_dir /path is inside /protected/path, which Polarizer must not write to`.
   - **Letter case** follows the platform's path rules. On Windows, paths compare case-insensitively, so a differently cased `ledger_dir` (`~/.CONFIG/Parallax/x`) is refused. On Linux they are different paths, and it is not refused. On macOS, a differently cased path is refused when the forbidden directory exists on a case-insensitive volume (the default), through the same-directory check, and is otherwise compared case-sensitively. Tests pin all three.
   - **Warn**, and continue, inside any other git working tree: `polarizer: warning: ledger_dir /path is inside the git working tree /repo`.
3. **Open the ledger** (LEDGER-SPEC.md Part 2): wait up to 2 s for the lock, create the genesis entry on first run, verify, and check or rebuild `ledger.head`.
   - Any status other than `intact` exits with that status's code and one line on stderr: `polarizer: <verify's first line>; run polarizer verify`. For a torn tail, whose line already ends `; run polarizer repair`, nothing is added.
   - A v0 ledger exits 3: `polarizer: ledger at <dir> is v0 (Parallax's format); Polarizer only writes v1`.
4. **Append `session.started`** with Polarizer's version and the config hash. The client's name, version and protocol aren't known yet: in 2026-07-28 there's no `initialize`, and they arrive in each request's `_meta`. They are recorded as `session.client` exactly once per process, on the first request or `initialize` that carries them, even when two requests arrive at the same moment. A flag set before the write, under one lock, decides which request writes it.
5. **Connect every upstream in parallel,** each within its own `connect_timeout_seconds`. Connecting means opening the SDK `Client` and listing the tools (see Listing).
   - Each upstream gets an `upstream.connected` entry, with its protocol version and tool count, or its error (`timeout after 10 s`, the exception's message made safe as in Calls, Upstream text, or a listing limit).
   - A failed or hung upstream is skipped for the session, and the others serve.
   - There is no retry in M0. A skipped upstream returns only when Claude Code restarts Polarizer.
   - (M1a) Each connected upstream's list is then hashed, stored and checked against the pins, and the results recorded (PIN-SPEC.md, section 6). Nothing is exposed that isn't approved.
   - (M1a) End of input during startup doesn't cancel it. With stdin closed, `serve` finishes steps 3 to 5, fsyncs and exits 0, which is how a ledger is primed before the first session (PIN-SPEC.md, sections 7 and 8).
6. **Serve.** With the default timeouts, steps 3 to 5 take at most about 12 s plus ledger verification, well under 30 s.

## Listing

**Exposed names.** Each upstream tool is exposed as `<prefix>__<tool>`. A tool whose exposed name would not match `^[A-Za-z0-9._-]{1,128}$` is left out, and its name is recorded in `upstream.connected` under `skipped_tools`.

**Pins (M1a).** Only tools in the approved state are listed, each served from its stored copy, which has no `_meta` (PIN-SPEC.md, sections 4 and 5). A definition whose canonical form is larger than 262144 bytes is never stored or served, and one that can't be hashed is recorded with a fixed reason, never the exception's text (PIN-SPEC.md, section 2). Nothing turns pins off; M0's behavior is the code at commit 4eaa61a (PIN-SPEC.md, section 7).

**Paging.** `tools/list` follows `next_cursor` for up to 100 pages and 1,000 tools per upstream. Past either limit, that upstream is refused for the session, with the error `more than 100 pages` or `more than 1000 tools` recorded.

**Freshness.** Every `tools/list` from Claude Code lists each connected upstream again with `cache_mode="refresh"`, so a missed change notice costs nothing. Polarizer sets no cache hints of its own. (M1a) A failed refresh hides that upstream's tools and records `upstream.refresh_failed`, instead of keeping its last list. When a later refresh succeeds, a tool whose hash is still the approved one comes back with no new approval. While the list is unknown because a refresh failed, `serve` retries on a timer from stage 5: after 30 s, doubling up to 5 minutes, each attempt bounded by `connect_timeout_seconds`, reset on success. A lost connection is not retried; its tools stay hidden until the server restarts (PIN-SPEC.md, section 6).

**Change notices.**
- **From 2026-07-28 upstreams:** Polarizer holds `client.listen(tools_list_changed=True)` open and republishes each event as `ToolsListChanged()` on its own `subscriptions/listen` bus.
- **From older-era upstreams:** it receives `notifications/tools/list_changed` through the `Client`'s `message_handler` and republishes the same way. This path is unverified.
- **To older-era clients:** the server is created with `NotificationOptions(tools_changed=True)`.
- **(M1a)** An upstream's notice no longer passes straight through. It triggers a refresh of that upstream, and the client is told only when the list Polarizer exposes changes, at most once per second. A decision made by `polarizer approve` or `reject` in another process is noticed the same way (PIN-SPEC.md, sections 6 and 8). Claude Code re-listed on that notice headless, and interactively in the owner's check on 2026-10-04 with Claude Code 2.1.289, without a reconnect (verified-facts.md, M1a check, interactive). If `/mcp` doesn't show a tool after approving, the documented fallback is to reconnect the server in `/mcp` (PIN-SPEC.md, section 8).

## Calls

**Routing.** A call to `<prefix>__<tool>` is split at its first `__`. The left part is the prefix, and the rest, which may itself contain `__`, is the upstream's tool name. The call goes to that upstream's client.

**Unknown tools.** A name with no `__`, an unknown prefix, an upstream that didn't connect, or a tool it didn't list is refused:
- **The ledger** gets one `call.refused` entry with `session`, `tool` (the name as called) and `reason`, one of `not a prefixed name`, `no upstream with prefix "<p>"`, `upstream <p> did not connect` or `upstream <p> has no tool "<t>"`. There is no side file, `call.sent` or `call.returned`.
- **The client** gets a `tools/call` result with `isError` and one line: `polarizer: no tool named <name>`. This is deliberate, not a JSON-RPC error: a result reaches the model, which can see the mistake and recover, for example by listing tools again.

**Hidden tools (M1a).** A listed tool that isn't in the approved state is refused the same way: one `call.refused`, no side file, no upstream call. The reasons are `upstream <p> tool "<t>" is pending approval`, `... was rejected`, `... changed after approval`, `... cannot be served: <problem>`, and `upstream <p> tool list could not be refreshed`. The client's line is `polarizer: <name> is not available: <why>` (PIN-SPEC.md, section 6).

**Request `_meta`.**
- **Forwarded:** `traceparent` and `tracestate`.
- **Recorded:** `call.sent` records the names of all other dropped keys in `meta_dropped`, except `io.modelcontextprotocol/*`, which every 2026-07-28 request carries. It records the value of `claudecode/toolUseId` as `client_call_id`, or `null` when the client didn't send one. Its `tool` is the full exposed name, `<prefix>__<tool>`.
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

`call.returned` records the outcome, plus `error` (the one line) for every outcome except `ok`, and `code` for `protocol-error`. It also records:
- **`latency_ms`:** always an integer: whole milliseconds on a monotonic clock, from just before the upstream call to just after it ends, rounded down.
- **`result_bytes`:** the length of the compact UTF-8 JSON of the result as sent to the client, that is `len(json.dumps(result.model_dump(by_alias=True, mode="json", exclude_none=True), separators=(",", ":"), ensure_ascii=False).encode("utf-8"))`. It is 0 when nothing was returned: for `cancelled`, and for `protocol-error`, where the client gets a JSON-RPC error instead of a result.

Notes on the outcomes:
- **Shutdown (M1a).** SIGINT, SIGTERM and end of input start one shutdown (end of input during startup lets startup finish first): in-flight calls are recorded as `cancelled` with the error `polarizer shut down during the call`, upstream clients get at most 1 s to close, and the writer is closed; then the process exits 0 at once. A second signal skips the wait (PIN-SPEC.md, section 8).
- **Timeouts.** M0 has no transport timeout, and "timeout" is not part of `transport-error`. Polarizer sets no per-call read timeout in M0, so the SDK's local -32001 can't occur. A call is bounded only by Claude Code's own limit, which arrives as `cancelled`.
- **Unreadable results.** A result the SDK can't parse into its models (for example a content block of an unknown `type`) is a `transport-error`, with the fixed line `polarizer: upstream <prefix> failed: result did not match the MCP schema` for the client and the ledger. The SDK's own message quotes the result, so it is never passed on or recorded (Results).
- **Upstream text** (stage 5 follow-up). Text an upstream sent, or an exception built from it, reaches serve's stderr and the ledger only through one function, `polarizer.text.safe`: whitespace folded to one space, every other character outside printable ASCII escaped as `\xNN` or `\uNNNN`, cut to 200 characters (`...` marks a cut). That covers the `transport-error` line (so the client gets the safe line too), `upstream.connected`'s `error` and `skipped_tools`, `upstream.refresh_failed`'s `error`, serve's stderr lines about upstreams, and the SDK's own log records, which serve writes as one line each with no traceback. A message the SDK can't parse is described as `a message did not match the MCP schema: <type> at <location>`, from pydantic's error type and location, never its quoted input. By design, the client gets a `protocol-error`'s message unchanged; the ledger's copy, `call.returned`'s `error`, goes through `safe()` too, cut to 1 KiB instead of 200 characters. A result also reaches the client unchanged, and never enters the ledger. An upstream's own stderr is not filtered: the SDK gives each upstream serve's stderr as its own.
- **Dead upstreams.** The SDK reports them as `MCPError` -32000, with nothing marking the error as local. An upstream that sends -32000 itself is therefore recorded as a transport error. That's rare, and harmless apart from the label.
- **Older-era requests to the client.** "unsupported" is reliable only for an `InputRequiredResult`. In the older era, an upstream's elicit, sample or roots request is refused by the SDK (-32600 "... not supported") before Polarizer could see it. Detecting it would mean registering callbacks, which would advertise those capabilities and invite the requests. The upstream then usually returns -32600, recorded as `protocol-error`. A 2026-07-28 upstream that calls `ctx.session.elicit_form` instead of returning `InputRequiredResult` fails on its own side (`NoBackChannelError`, -32600), also recorded as `protocol-error`.

## Results

Polarizer hands the client the result object the SDK's upstream client parsed, unchanged; its own code edits nothing in it. The SDK still changes a result, or a tool definition, in the ways below. These are stated limits of M0. `tests/test_fidelity.py` pins each one exactly with fakes in every run, and `tests/test_reference.py` checks them against the Everything and Filesystem reference servers locally (verified-facts.md, Stage 3).

1. **Unknown fields are dropped.** The SDK parses each upstream result into its own models, which ignore fields the protocol doesn't define, at every level: the result, each content block, an embedded resource and annotations. Tool definitions lose unknown fields the same way (verified-facts.md, the SDK's `Tool`).
2. **Results follow the client's protocol version.** On a 2026-07-28 client connection, the SDK adds `resultType` and stamps `_meta["io.modelcontextprotocol/serverInfo"]` on every result, as that version requires. A result from an older-era upstream gets Polarizer's own stamp; a 2026-07-28 upstream's stamp is kept. A 2025-11-25 client in front of a 2026-07-28 upstream receives that upstream's stamp, which it would not get directly.
3. **`isError: false` is added** when an upstream leaves `isError` out. The protocol reads an absent `isError` as false.
4. **No `execution` in 2026-07-28 tool definitions.** A tool's `execution` (task support) exists only in 2025-11-25, so a 2026-07-28 client never sees it. Polarizer doesn't proxy tasks in any era. (M1a) No client sees it: tools are served from stored copies in the 2026-07-28 form (PIN-SPEC.md, section 2).
5. **A result the SDK can't parse becomes an error**, recorded as `transport-error` with a fixed line (Calls, Notes on the outcomes).

**(M1a) "Only the names change" no longer holds literally for tool definitions.** Tools are served from stored copies in the 2026-07-28 form, so no client in any era gets `execution`, and none gets a tool's `_meta` (PIN-SPEC.md, sections 2 and 5). Polarizer's own code makes this change, not the SDK, and it is a stated limit of M1a, like the five above. Results are not affected.

Everything else arrives as the upstream sent it, at every level: text, image, audio, embedded resources, resource links, annotations, `_meta`, `structuredContent` (floats included) and `isError`.

## Output streams

- **`serve`:** its stdout carries only the protocol. Its logs and one-line errors go to stderr.
- **Every other command** (`verify`, `repair`, and in M1a `pending`, `approve` and `reject`) prints its results to stdout. Usage errors, config errors, unreadable files, forbidden-path refusals and the git-tree warning go to stderr, each as one line starting `polarizer: ` (or the config file's name), and every error among them exits 2.

## Command line

```
polarizer verify (--config <absolute path> | --ledger-dir <absolute path>) [--args]
polarizer repair (--config <absolute path> | --ledger-dir <absolute path>)
```

M1a adds these; their output, refusals and golden files are in PIN-SPEC.md, section 7. `serve` keeps its M0 syntax.

```
polarizer pending (--config <absolute path> | --ledger-dir <absolute path>) [--upstream <prefix>]
polarizer approve (--config <absolute path> | --ledger-dir <absolute path>) <prefix> <tool> <def_hash> [--allow-no-terminal]
polarizer approve (--config <absolute path> | --ledger-dir <absolute path>) --group <group id> [--upstream <prefix>] [--allow-no-terminal]
polarizer reject (--config <absolute path> | --ledger-dir <absolute path>) <prefix> <tool> <def_hash> --reason <text> [--allow-no-terminal]
```

`approve` and `reject` refuse to run when stdin is not a terminal, unless `--allow-no-terminal` is given: one line on stderr, exit 2, nothing written. It is a speed bump against accidents, not a barrier, since an agent can pass the flag.

Each command takes exactly one of `--config` (the directory is `ledger_dir` from that file, or the default `~/.local/share/polarizer`) or `--ledger-dir`. There is no current-directory default. These errors go to stderr as one line and exit 2:

```
polarizer: verify needs exactly one of --config <absolute path> or --ledger-dir <absolute path>
polarizer: repair needs exactly one of --config <absolute path> or --ledger-dir <absolute path>
polarizer: --config must be an absolute path, got <path>
polarizer: --ledger-dir must be an absolute path, got <path>
polarizer: cannot read <path>: <the operating system's message>
polarizer: <argparse's message>
```

The last is for any other argument error, such as an unknown option. Config errors are under Configuration, and also exit 2.

The output below is exact; placeholders are in angle brackets. Every exit code, and the order in which problems are reported, is in LEDGER-SPEC.md (Statuses).

**`verify`**

| Status | stdout | Exit |
|---|---|---|
| intact (v1) | `intact: <n> entries, <s> sessions, <c> calls`, then `chain <chain_id>, head <64 hex> at seq <seq>`, then `ledger.head: missing; serve will rebuild it` only when `ledger.head` is missing | 0 |
| intact (v0) | `intact (v0): <n> entries`, then `head <64 hex> at line <n>` | 0 |
| tampered (a line) | `tampered: line <l> (seq <q>): <reason>`, where the reason is `seq is <a>, expected <b>`, `prev is not 64 zeros` (line 1), `prev does not match line <l-1>` or `hash does not match the entry` | 1 |
| tampered (the head) | `tampered: ledger.head: hash does not match seq <h>` | 1 |
| invalid (a line) | `invalid: line <l> (seq <q>): <rule>`, with the rules below | 3 |
| invalid (the head) | `invalid: ledger.head: not a valid head record` or `invalid: ledger.head: chain_id does not match the ledger` | 3 |
| not canonical | `not canonical: line <l> (seq <q>): stored bytes are not the canonical form` | 4 |
| torn tail | `torn tail: <b> bytes after line <l> (seq <q>); run polarizer repair` | 5 |
| truncated | `truncated: ledger ends at seq <q> but ledger.head records seq <h>`, or `truncated: ledger has no complete entries but ledger.head records seq <h>` | 6 |
| locked | `locked: ledger is locked by another process (waited 2 s); try again or close other Polarizer sessions` | 7 |
| no ledger | `no ledger at <dir>` (also when `ledger.jsonl` is empty) | 2 |

**Details of the placeholders.**
- **`(seq <q>)`:** the line's own `seq` when the line parsed into an object with an integer `seq`, otherwise `(seq ?)`. A torn tail with no complete line before it prints `after line 0 (seq ?)`.
- **Invalid rules,** in the order they are checked within a line: `not valid UTF-8`, `not valid JSON`, `not a JSON object`, `v0 and v1 entries mixed`, `line longer than 16 KiB`, `unknown key "<k>"` (the first in the line's own order), `missing key "<k>"` (the first in the order of the keys table), `"<k>" has the wrong type`, `v is not 1`, then the subset rules `float at <path>`, `integer out of range at <path>`, `non-ASCII key at <path>` and `lone surrogate at <path>` (the first offending value in document order), then `first entry is not a genesis`, `second genesis` and `genesis chain_id is not 32 lowercase hex`.
- **`<path>`** is a JSON pointer such as `/data/latency_ms`. In printed keys, `~` is `~0`, `/` is `~1`, and any character outside printable ASCII is printed as a `\uXXXX` escape, so the line stays ASCII.
- **Counting:** in the first `intact` line, sessions are `session.started` entries and calls are `call.sent` entries.
- **v0 ledgers:** messages drop the `(seq <q>)` part, since v0 has no seq. The not-canonical reason is `stored bytes are not the v0 line form`, and a torn tail prints `torn tail: <b> bytes after line <l>` with no repair hint, because repair refuses v0 ledgers. Only the first four invalid rules, `unknown key` and `missing key` apply to v0.

**`verify --args`** prints the chain result first, including the `ledger.head: missing` line when there is one. If the chain is intact, it adds one summary line and then one line per side file that isn't matching: missing and tampered files in seq order, then orphans by name.

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
| a line problem | `refused: ledger is <status> at line <l>; repair only fixes a torn tail` | that status's code |
| an invalid or tampered head | `refused: ledger is <status> at ledger.head; repair only fixes a torn tail` | 3 or 1 |
| truncated | `refused: ledger is truncated (ledger.head records seq <h>); repair only fixes a torn tail` | 6 |
| a v0 ledger | `refused: ledger is v0 (Parallax's format); Polarizer only writes v1` | 3 |
| locked | the `locked:` line above | 7 |
| no ledger | the `no ledger at` line above | 2 |
| inside a forbidden path | nothing; the refusal line from Startup goes to stderr | 2 |

Every row in these tables gets a golden-file test (`tests/golden/`), which runs the command on a fixture and compares stdout byte for byte, along with the exit code.

## Where the ledger lives

`serve` and `repair` refuse a `ledger_dir` inside a forbidden path (every `ledger_forbidden_paths` entry, plus Parallax's two directories, always) and warn inside any other git working tree (startup step 2). `verify` only reads, never writes, and checks no locations.

The guard (`scripts/guard.sh`) is a development script, not part of the installed tool. It reads `.guard-paths`, and it is the backstop if a `polarizer.toml` lacks a forbidden path. It watches other tools' directories by metadata only, never opening contents:
- `~/.local/share/parallax` as a summary (file count, total size, newest mtime and one hash);
- `~/.config/parallax` and `~/isr-notes` line by line. It also watches `~/.claude/settings.json` by metadata, and `~/.claude.json` through hashes of its MCP config. A search by name found no Loupe or ISR directories under `~/.local/share`, `~/.config` or `~/.cache`. Polarizer's own `ledger_dir` is not guarded; it's Polarizer's to write.
