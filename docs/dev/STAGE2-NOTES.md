# Stage 2 notes

Read after docs/dev/STAGE1-NOTES.md. This file records what stage 2 (the proxy) decided on its own: the deviations from the specs, the guesses where the specs are silent, what could not be run, and a claims table. Where these notes and the specs differ, the specs win until a spec is changed.

Stage 2 added `src/polarizer/upstream.py` and `src/polarizer/proxy.py`, added `serve` to `src/polarizer/cli.py`, and added the test upstreams under `tests/helpers/`. No contradiction between the specs needed a decision from the owner. SDK 2.2.0 behaved as `docs/verified-facts.md` describes everywhere stage 2 touched it.

## Deviations

1. **The probe's location.** It is `tests/helpers/probe_server.py`, not `tests/probe_server.py`, as the stage 2 prompt asked. m0-plan.md step 6 and the docs/PROXY-SPEC.md example config now say so.
2. **The probe's tools.** Besides `wait(seconds)`, it has `crash` (exits mid-call), `env` (returns its environment as JSON text) and `fail` (a tool error). It writes a `start <pid>` line to `PROBE_LOG` and `probe: started` to its stderr.
3. **Where two claims are tested.** `InputRequiredResult` and the older-era requests to the client are tested in `tests/test_outcomes.py`, not `tests/test_scope.py`. `serve`'s usage errors are in `tests/test_golden.py` with the other usage errors; its config, location and v0 errors are in `tests/test_config.py`. The m0-plan.md claims table now names those files.
4. **The progress token is not in `meta_dropped`.** docs/PROXY-SPEC.md says `meta_dropped` lists "all other dropped keys". The token is not dropped in effect: progress is relayed under the client's own token through `report_progress`. The SDK also names it differently by transport (`progress_token` over stdio, absent in memory), so recording it would make the ledger depend on the transport. Pinned by `test_meta_filter` and `test_meta_filter_over_stdio`.
5. **A call the ledger can't record is not forwarded.** If the side file can't be written or `call.sent` can't be appended (the writer has stopped, or the entry was refused), the client gets an isError result `polarizer: <name> was not called: the ledger could not record it`, stderr gets `polarizer: refused <name>: could not record it: <reason>`, and the upstream is never called. A side file written before the failure stays as an orphan, which `verify --args` reports. docs/LEDGER-SPEC.md says a stopped writer refuses every later call but not what the client sees. Pinned by `test_stopped_ledger_refuses_calls`.
6. **A message not in the spec:** `polarizer: cannot open <path>: <the operating system's message>`, exit 2, when `serve` can't create or open the ledger directory.
7. **Older-era clients get `notifications/tools/list_changed`.** docs/PROXY-SPEC.md says only that the server is created with `NotificationOptions(tools_changed=True)`. The gateway keeps the session of each older-era `initialize` and sends `send_tool_list_changed()` to it when an upstream's list changes, so the advertised capability is honored. A session whose send fails is dropped. Pinned by `test_eras`.
8. **Handlers beyond the three named.** The server also answers `ping` and `server/discover`, which the SDK registers by default. It registers nothing for resources, prompts, completions or logging, and no notification handlers. `test_registered_handlers_are_tools_only` pins the exact list.
9. **stderr lines not in the spec**, all from `serve`:
   - `polarizer: upstream <p> did not connect: <error>`;
   - `polarizer: upstream <p>: skipped tool '<name>': name not allowed`;
   - `polarizer: upstream <p>: <n> more skipped names not recorded`;
   - `polarizer: upstream <p>: listing failed (<why>); kept its last list`;
   - `polarizer: upstream <p>: listing timed out; kept its last list`;
   - `polarizer: upstream <p>: change notices stopped: <why>`;
   - `polarizer: upstream <p> stopped: <why>`;
   - `polarizer: could not record <kind>: <why>`.
10. **docs/verified-facts.md, Unverified.** The two older-era list-change items now say stage 2 ran them SDK to SDK. Neither has run with Claude Code or a third-party server.

## Guesses

1. **`error` for each outcome.** `tool-error`: the fixed line `upstream <p> returned a tool error`, so none of the tool's own result text enters the ledger. `cancelled`: `the client cancelled the call`. `transport-error` and `unsupported`: the same line the client gets. `protocol-error`: the upstream's message. Each is folded to one line and cut to 1 KiB.
2. **Refresh failures.** If re-listing an upstream on a client `tools/list` fails, or passes the page or tool limit, the upstream keeps its last good list and stderr gets one line. It is not refused for the session; that rule applies at connect. Each refresh is bounded by the upstream's `connect_timeout_seconds`, so one hung upstream can't hang `tools/list`.
3. **An upstream that dies after connecting** stays listed. Calls to it are recorded as `transport-error` with the SDK's message (`Connection closed`). There is no retry.
4. **Lengths and characters**, so every entry stays inside the subset and under 16 KiB:
   - lone surrogates become U+FFFD;
   - `client_name` and `client_version` are cut to 256 bytes, protocol versions to 64;
   - `meta_dropped` keeps the first 32 key names in sorted order, each cut to 128 bytes;
   - `client_call_id` is cut to 256 bytes, and recorded only when the client sent a string (anything else is `null`);
   - a refused name is cut to 1 KiB, and the tool part of a refusal reason to 512 bytes;
   - `skipped_tools` holds names of up to 256 bytes within an 8 KiB budget; the rest are named only on stderr.
5. **`meta_dropped` is sorted,** not in arrival order.
6. **A protocol error's `code` outside the subset's integer range** would be recorded as a string. No test covers it.
7. **`session.client`'s `protocol_version`:** in the older era, the version from the `initialize` result (the negotiated one); in 2026-07-28, the request's `io.modelcontextprotocol/protocolVersion`. A missing name or version is `null`.
8. **`<kinds>` in the `unsupported` line:** `elicitation`, `sampling` or `roots`, from the methods of the embedded requests, sorted and joined with `, `. An unknown method appears as itself.
9. **`result_bytes`** uses docs/PROXY-SPEC.md's formula on the result Polarizer hands the SDK. On the 2026-07-28 wire the SDK then stamps its own `serverInfo` into the `_meta` of results Polarizer wrote, which isn't counted. A result passed through from a 2026-07-28 upstream already carries that upstream's stamp, which is counted, and the client receives it unchanged. The SDK client treats the stamp as display-only.
10. **Order of `upstream.connected` entries:** config order, after every upstream has connected, failed or timed out; not in the order they finish.
11. **Order of exposed tools:** upstreams in config order, each upstream's tools in its own order. If one upstream lists the same name twice, the first wins.
12. **Polarizer's upstream clients identify themselves** as `polarizer` with Polarizer's version.
13. **Shutdown.** When the client closes stdin, in-flight calls are cancelled and recorded as `cancelled`, and the SDK stops each upstream process (its own wait of up to 2 s, in parallel).

## Not run

- **CI**, and therefore **Windows and macOS runs** of every stage 2 test. Only the Linux (WSL2, Python 3.12.3) runs below happened.
- **Claude Code, and any model.** Nothing in stage 2 ran `claude`. `scripts/live-check.sh` and `docs/dev/MANUAL-CHECK.md` are stage 3, so every Claude Code behavior through Polarizer's own proxy is still untested, including Esc to cancel, `/mcp` names, and the cancel timing in the live check.
- **A third-party upstream.** The Everything server was not run in stage 2. Nothing was fetched by `npx`, `npm` or `pip`, so no new pinned commands were recorded.
- **Load.** The timing tests ran on an idle machine. Their bounds (two 1 s calls under 1.8 s; startup under 5 s) may need room on slow CI runners.

## Windows and macOS hazards in the new code

- **serve's stdout on Windows.** The SDK's `stdio_server` wraps stdout in a text wrapper, which may write `\r\n`. JSON parsing tolerates the `\r`, and `test_stdout_clean` would still pass; the bytes weren't checked.
- **Stopping upstream processes.** Hung and crashed probes are stopped by the SDK's own termination code, which uses job objects on Windows. Untested there.
- **The minimal environment test.** It allows `LC_CTYPE` (Python sets it when it coerces the C locale) and `__CF_*` names (macOS). Another platform may add a name the test doesn't expect.
- **Paths in TOML.** The tests write paths as JSON strings, whose escapes are valid TOML, so backslashes survive.

## Claims

"Yes" means run locally on Linux (WSL2, Python 3.12.3, mcp 2.2.0), in the final run of `scripts/test.sh`: 389 passed, 4 skipped (the 4 are stage 1's platform skips). The stage 2 files then passed three more times in a row.

| Claim | Command | Run? |
|---|---|---|
| A 2026-07-28 upstream works through the proxy: versions, a list change through listen, a fresh list despite a TTL hint, progress, cancel reaching the upstream | `pytest tests/test_eras.py::test_modern_upstream` (memory, and stdio through `polarizer serve`) | yes |
| A 2025-11-25 upstream works through the proxy with a 2026-07-28 client: progress, its own cancel within 2 s, a list change through the message handler | `pytest tests/test_eras.py::test_handshake_upstream` | yes |
| Clients on both protocol versions list, call, get progress and get list changes through one proxy | `pytest tests/test_eras.py::test_eras` | yes |
| Advertised capabilities are exactly tools in each era, and resources, prompts, completion and logging answer -32601 | `pytest tests/test_scope.py::test_capabilities_only_tools` | yes |
| Only the tools, listen, ping and discover handlers are registered | `pytest tests/test_scope.py::test_registered_handlers_are_tools_only` | yes |
| stdout of `serve` carries only JSON-RPC; the upstream's stderr and Polarizer's logs go to stderr | `pytest tests/test_stdout.py::test_stdout_clean` | yes |
| Progress is relayed with `report_progress`, in both client eras, from memory and stdio upstreams | `pytest tests/test_proxy.py -k progress_relay` | yes |
| Two 1 s calls run concurrently, about 1 s in total, from memory and stdio upstreams | `pytest tests/test_proxy.py::test_two_concurrent_calls` | yes |
| A missing command, a hung handshake and a crash mid-call each stay in their own upstream | `pytest tests/test_proxy.py::test_upstream_isolation` | yes |
| One `call.sent` and one `call.returned` per call, for ok, tool-error, protocol-error, transport-error (two ways), unsupported and cancelled, with the specified `latency_ms`, `result_bytes`, `error` and `code` | `pytest tests/test_outcomes.py::test_one_sent_one_returned_per_call` | yes |
| An upstream's request to the client is refused before Polarizer sees it, in both upstream eras, and recorded as protocol-error | `pytest tests/test_outcomes.py::test_older_era_upstream_requests` | yes |
| A call the ledger can't record is refused and never reaches the upstream | `pytest tests/test_outcomes.py::test_stopped_ledger_refuses_calls` | yes |
| Only `traceparent` and `tracestate` are forwarded; other keys are recorded in `meta_dropped`, and `claudecode/toolUseId` as `client_call_id` | `pytest tests/test_proxy.py -k meta_filter` | yes |
| Prefix rules in the config, exposed names, and names over 128 characters or with other characters skipped and recorded | `pytest tests/test_proxy.py::test_prefix_rules` | yes |
| A tool whose own name contains `__` lists and calls correctly | `pytest tests/test_proxy.py::test_double_underscore_tool_name` | yes |
| Arguments reach the upstream exactly, and the side file holds them | `pytest tests/test_proxy.py::test_exact_arguments` | yes |
| An unknown tool gets an isError result and one `call.refused` with each of the four reasons, and no side file | `pytest tests/test_proxy.py::test_unknown_tool` | yes |
| `session.client` is written exactly once when eight clients connect at once | `pytest tests/test_proxy.py::test_session_client_once` | yes |
| The ledger verifies as intact after a session | `pytest tests/test_proxy.py::test_ledger_verifies_after_a_session` | yes |
| Paging follows the cursor; 100 pages and 1,000 tools pass, one more is refused | `pytest tests/test_listing.py` | yes |
| Every client `tools/list` lists each upstream again; a failed refresh keeps the last list | `pytest tests/test_listing.py` | yes |
| Upstreams connect in parallel, a hung one times out at its own timeout, entries come in config order, and nothing is retried | `pytest tests/test_startup.py` | yes |
| `serve` without `--config`, or with a relative path, prints one line and exits 2 | `pytest tests/test_golden.py -k usage_errors` | yes |
| `serve` config errors, a forbidden `ledger_dir` and an unset `${NAME}` exit 2 before any upstream starts; a v0 ledger exits 3 | `pytest tests/test_config.py -k serve` | yes |
| The upstream gets only the minimal environment plus its `env` | `pytest tests/test_config.py::test_upstream_gets_minimal_environment` | yes |
| All of the above on Windows and macOS | CI | no |
| End to end with Claude Code | `scripts/live-check.sh` (stage 3) | no |
