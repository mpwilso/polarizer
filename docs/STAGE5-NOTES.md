# Stage 5 notes

Read after docs/STAGE4-NOTES.md. This file records what stage 5 (M1a, pins in motion and process lifetime) decided on its own: what step 0 found, the deviations, the guesses, the time bounds in the new tests, the repeated runs, what could not be run, the platform hazards, and a claims table. Where these notes and the specs differ, the specs win until a spec is changed. PIN-SPEC.md deviations 38 to 45 summarize the decisions below that change the spec.

The session first fixed the stage 4 review items (two commits; docs/STAGE4-NOTES.md, Review fixes), then built stage 5 in one commit, "stage5: pins in motion and shutdown". No change to the ledger format, the hash chain, a status or an exit code. A follow-up commit made upstream text safe on stderr and in the ledger, and looked at shutdown and upstream processes (Follow-up, below).

## What step 0 found

Nothing needed the owner. The gaps, each decided here:

1. **There is no README.** The prompt asked for "the README text" to state that M1a is pins only. The README texts that exist are drafts: PIN-SPEC.md section 11 and docs/QUICKSTART-DRAFT.md. Both gained the line.
2. **The 1 s bound on closing upstreams can't be kept by waiting.** The SDK's stdio client shuts an upstream down inside a cancel shield (close stdin, wait 2 s, SIGTERM, 2 s, reap up to 2 s), and anyio waits for every task. Decided: `serve` waits at most 1 s, closes its writer, and ends with `os._exit(0)` (deviation 2).
3. **The SDK's stdio server reads stdin on an anyio worker thread** that a cancel can't interrupt and that isn't a daemon thread. After a signal, with Claude Code still holding stdin open, shutdown would wait until stdin closed. Decided: `serve` reads stdin itself (deviation 1).
4. **"At most one refresh per upstream at a time"** (PIN-SPEC.md section 6) was stated for change notices only. Decided: it holds across triggers, with notices coalesced on top (deviation 3).
5. **`test_approval_fsynced_before_exposed`'s claim** said the watch "runs repeatedly" while the approving fsync is held. It can't, because the approving writer holds the ledger lock through its fsync and the watch's catch-up waits for that lock; the waiting is what keeps the tool hidden. The claim was reworded in PIN-SPEC.md section 10.
6. **`test_approve_from_second_process` says "within 2 s"**, and the stage 5 prompt asks for every wall-clock bound to be at least 5 times what the operation needs. With the default 1 s watch, 2 s is twice. Decided: that test runs the watch every 0.25 s.

## Deviations

1. **serve reads stdin with `os.read` on a daemon thread** (`cli._StdinLines`) and passes the lines to `stdio_server(stdin=...)`. A buffered reader would make the interpreter abort at exit while the thread is blocked in it: seen as exit code -6, then reproduced on its own (verified-facts.md, Stage 5). The reader starts the shutdown path at end of input before the SDK sees it, so the calls in flight are recorded as `polarizer shut down during the call`. With an explicit `stdin`, the SDK no longer points file descriptor 0 at the null device while serving; upstreams have their own pipes, and nothing else reads it.
2. **serve ends with `os._exit(0)`** right after its writer closes (`cli._exit_now`), on every shutdown. Besides the 1 s bound, it fixes an exit code: when shutdown finished within the 100 ms between SIGINT and SIGTERM, a normal exit tore down anyio's signal receiver first, which restores SIGTERM's default action, and the late SIGTERM ended the process with -15 instead of 0. stdout and stderr are flushed first. Nothing in-process runs `serve` that far: the tests that call `cli.main(["serve", ...])` in the test process all stop before the event loop starts.
3. **One refresh per upstream at a time, whatever triggered it.** `Upstream.refresh` takes a per-upstream lock, so listings of one upstream (a client's, a notice's, the retry timer's) are applied in the order they started. Notices during a notice-driven refresh cause exactly one more. A notice that arrives during startup is acted on once startup has finished, so a refresh never records anything before `upstream.connected`.
4. **The retry timer records nothing.** A retry only follows a recorded first failure, and a success ends the run, so `upstream.refresh_failed`'s `trigger` keeps its three values. Each failed retry still writes its stderr line. The timer stops when the upstream's connection is lost, and when another trigger's refresh succeeds.
5. **The writer stops if its idle fsync fails**, with `polarizer: stopped writing the ledger: could not fsync the ledger: <why>; run polarizer verify`. Before, the exception ended the writer thread and every later append waited forever. `test_serve_fsyncs_adopted_approval` needed this, since it makes serve's fsync fail.
6. **`LedgerWriter.close()` may be called twice,** and an append or catch-up after it fails at once with `Stopped`, instead of waiting for a thread that has gone. `serve` closes the writer in its shutdown path and again in `cli.serve`'s `finally`.
7. **The writer stopping still sends a notice at once.** Notices are otherwise compared and limited to one a second; a stopped writer's notice (PIN-SPEC.md section 4, While running) goes out directly, as in stage 4, because the client's own `tools/list` that found it may already have returned the empty list.
8. **Windows Ctrl+C** reaches the shutdown path through `signal.signal(SIGINT, ...)` and `loop.call_soon_threadsafe`, because anyio has no signal receiver on Windows. Untested (Not run).
9. **The probe's `change` tool sets** `wait`'s description to the changed text, rather than toggling it, so calling it twice changes it once. `PROBE_RUGPULL` uses the same text.
10. **`FakeUpstream` gained** `rug_pull()`, `fail_list` (tools/list answers with JSON-RPC error -32603) and `list_times`. `definitions=` and `FAKE_DEFINITIONS` were stage 4's.
11. **`Gateway` gained** `watch_interval`, `notice_interval` and `retry` keyword arguments (defaults 1 s, 1 s, and 30 s doubling to 300 s), a replaceable `sleep` for the retry timer, `start(tasks)` (startup in a task group the caller owns), `begin_shutdown()` and `close_upstreams(bound)`. `Upstream` gained `close()`, `closed`, `started` and the refresh lock. `Decider.open` takes `ops=`.
12. **`rig.gateway()` and `rig.proxied()`** pass `ops=`, `state=` and the gateway options through; `rig` gained `next_notice`, `no_notice` and `until`.
13. **`scripts/live_check.py prime`** imports Polarizer, so the script is no longer standard library only; `live-check.sh` runs it with the repo's `.venv` python, which has Polarizer installed.
14. **Tests beyond PIN-SPEC.md section 10:** `test_shutdown::test_signal_during_startup`, `test_pins::test_notices_at_most_once_per_second` and `test_live_check::test_prime_exposes_the_probe_tool`.
15. **Existing tests changed:**
    - `test_pins`: `test_approve_exposes` and `test_reject_hides_and_needs_reason` gained their change-notice assertions; `test_drift_once_per_pair_and_sticky` now drives the change and the revert with `rug_pull()`, each with its own notice, instead of changing the fake behind a client `tools/list`; `test_flip_flop_is_bounded` gained a notice phase driven by 26 rug pulls.
    - `test_pin_startup::test_prime_with_closed_stdin` and `test_stdout`: the probe's seventh tool, `change` (14 tools wait instead of 12; 7 per probe).
    - `test_eras`: `_next_event` and the older-era notice wait went from 2 s to 10 s, because notices are now limited to one a second, so one can need about 1 s (Time bounds).
16. **docs/MANUAL-CHECK.md's M1a section** runs on its own ledger directory, `~/.local/share/polarizer-m1a-check`, set by a `sed` line in step M1, so earlier manual-check ledgers don't change what `pending` shows; M6 puts `polarizer.toml` back.

## Guesses

1. **One "last announced" exposed list per process.** A client's `tools/list` response counts as announced for every connected client. Claude Code is one client; with several, one could miss a notice for a change another client's listing returned.
2. **The notifier's first notice goes out at once,** and later ones at least `notice_interval` after the previous one. A change inside that interval is folded into the notice at its end, which compares the exposed list again then; a change and its reverse inside one interval send nothing.
3. **A refresh started by the retry timer** uses the same per-upstream lock and observation as any other; a client `tools/list` during the timer's sleep that succeeds ends the run, and the sleeping timer then stops without listing.
4. **Startup's exposed list is the first "announced" one,** before any client has listed, so the first notice is for a change after startup.
5. **`close_upstreams` waits only for upstreams whose task was started;** a signal during the `session.started` append, before any upstream task exists, goes straight to closing the writer.
6. **Mid-session, an upstream that changes to a definition that can't be hashed or is too large** hides its tool as unservable and records no drift, as at rest in stage 4. If it then returns to the approved definition, the tool comes back without a new decision: not sticky, unlike a change the model could have seen. The model never saw the unservable definition. PIN-SPEC.md doesn't say otherwise; noted here because it differs from the changed state's stickiness.

## Time bounds in the new and changed tests

Lower bounds can't fail under load. Each upper bound is at least 5 times what the operation needs.

| Test | Bound | The operation needs |
|---|---|---|
| `rig.next_notice` (most notice waits) | 10 s | up to about 2 s: up to 1 s for the watch, up to 1 s for the one-a-second limit |
| `test_eras` (`_next_event`, the older-era notice) | 10 s | up to about 1 s |
| `test_pin_durability::test_approve_from_second_process` | the notice within 2 s of the command returning | about 0.25 s (watch every 0.25 s) |
| `test_pins::test_notices_at_most_once_per_second` | second notice at least 0.95 s after the first (lower) | 1 s |
| `test_pins::test_notice_only_when_exposed_list_changes` | no notice within 1.5 s (a window, not a bound: a late notice would make it pass, not fail) | none expected |
| `test_pins::test_failed_refresh_retries_on_timer` | the retry run within 30 s | well under 1 s (the sleep is replaced, 10 ms each) plus one 0.5 s timeout |
| `rig.until` (default) | 5 s | milliseconds to about 0.1 s |
| `test_shutdown` (process exit, probe log) | 30 s each | about 1 s |
| `test_shutdown::test_sigterm_alone_during_call` | exits within 30 s (the call's own length) | about 1 s |
| `test_shutdown::test_signal_during_startup` | exits within 10 s | about 1 s, the bound on closing upstreams |
| `test_pin_durability::test_approval_fsynced_before_exposed` | the held fsync within 30 s; exposure within 5 s of release | milliseconds; about 0.05 s |
| `test_pin_durability::test_watch_reads_only_new_bytes` | ten watch ticks within 5 s | 0.1 s |

## Repeated runs

The new and changed test files (`test_defhash`, `test_pin_cli`, `test_golden`, `test_listing`, `test_pins`, `test_pin_durability`, `test_shutdown`, `test_pin_startup`, `test_stdout`, `test_live_check`, `test_eras`, `test_proxy`, `test_outcomes`, `test_fidelity`, `test_startup` and `test_writer`, 217 tests), on the final code:
- **Five times in a row:** 217 passed each time, in 85.5 to 85.8 s.
- **Once under load,** while two busy-loop Python processes ran, started and stopped by their own PIDs: 217 passed in 86.5 s, and both PIDs were gone afterwards. The machine has 24 logical CPUs, so two busy loops are a light load.

An earlier series, before the notice waits went to 10 s and before the stdin reader treated a read error as end of input, gave the same: 5 of 5 and 1 of 1 under load.

## Not run

- **CI,** so nothing from stage 5 or the stage 4 review fixes has run on Windows or macOS, or on Python 3.11 or 3.13. Only Linux (WSL2, Python 3.12.3) ran it.
- **Claude Code, any model, and an interactive session.** Nothing in this session ran `claude`. `scripts/live-check.sh` is primed but not run; the owner runs it from a plain terminal. docs/MANUAL-CHECK.md's M1a section is for the owner, and it answers decision 9: whether interactive Claude Code lists the tools again after Polarizer's change notice without a reconnect.
- **The Windows Ctrl+C path** (deviation 8), and the four signal tests anywhere but Linux; three of them are POSIX only by design.
- **A third-party upstream changing its definitions mid-session.** The drift tests use `FakeUpstream` and the repo's probe; the reference servers never change.
- **The reference tests after stage 5's changes.** The two stage 4 reference tests ran during the review fixes (STAGE4-NOTES.md, Review fixes), before stage 5; they don't touch notices or shutdown, but they do start and stop `serve`, whose shutdown changed.

## Windows and macOS hazards in the new code

- **stdin on a daemon thread:** `os.read(0, ...)` on a pipe works in a thread on Windows, but end of input there may come as an error (a broken pipe) instead of an empty read. The reader treats any `OSError` from the read as end of input, so either way it starts the shutdown path. Only the empty read has been seen (Linux). `test_stdin_closed_during_call` runs on Windows in CI.
- **`os._exit(0)`** skips Python's own teardown on every platform; serve flushes stdout and stderr first. On Windows the SDK's job objects for upstream processes are closed by the operating system at exit.
- **Signals:** `test_sigint_then_sigterm_during_call`, `test_sigterm_alone_during_call`, `test_kill_then_restart` and `test_signal_during_startup` are skipped on Windows, with the reason in the spec. On macOS they run; `anyio.open_signal_receiver` works there as on Linux.
- **The retry, watch and notice timers** use anyio's clock; nothing in them is platform-specific. The tests' lower bound on the notice interval (0.95 s) may meet a coarse timer on Windows (about 15.6 ms resolution), well inside the margin.
- **fsync failures** are simulated through `FileOps`; how Windows reports a failed `os.fsync` (`FlushFileBuffers`) wasn't tried.
- **`test_watch_reads_only_new_bytes`** builds a 10,000-entry ledger, about 1 s on WSL2; slower disks take longer, with no time bound on that part.

## Follow-up: upstream text and shutdown cleanup

One more commit after the stage 5 commit, "stage5: upstream text safety and shutdown cleanup". No change to the ledger format, the hash chain, a status or an exit code. One recorded value changes form: a skipped tool name is now escaped and cut to 200 characters, like all upstream text (`caf\xe9`, not the raw name), and `test_proxy::test_prefix_rules` was changed to match. Line numbers below are in the follow-up's code.

### Upstream text: every place it could reach output

"Upstream text" is text an upstream sent, or an exception message built from it. Stage 4's review listed most of these (STAGE4-NOTES.md, Review fixes) and left them open. Each now goes through one function, `polarizer.text.safe` (`src/polarizer/text.py:34`): whitespace folded to one space, every other character outside printable ASCII escaped as `\xNN` or `\uNNNN`, cut to 200 characters with `...` marking the cut, never inside an escape. `describe()` (`src/polarizer/upstream.py:50`) returns its result, and describes a pydantic error by a fixed text plus pydantic's error type and location (`a message did not match the MCP schema: dict_type at tools.0.inputSchema`), never its quoted input.

| # | Where | Text | Reached | Now |
|---|---|---|---|---|
| 1 | `upstream.py:192` (`Upstream._run`) | a failed connect: an upstream's JSON-RPC error, pydantic's listing error, or the SDK's `Unsupported protocol version from the server: <version>` (`mcp/client/session.py:667`) | stderr, `did not connect: <error>` (`proxy.py:235`); `upstream.connected`'s `error` (`proxy.py:236`) | `describe()`, and `safe()` again at the ledger |
| 2 | `upstream.py:194` and `:240`, into `lose()` (`:216`, `:217`) | a lost connection: an `MCPError` -32000 message, which an upstream can send itself | stderr, `stopped: <why>`; `upstream.refresh_failed`, `connection-lost` (`proxy.py:454`) | `describe()`; `safe()` at the ledger |
| 3 | `upstream.py:267` (`_refresh`) | a failed refresh, as in 1 | stderr, `listing failed (<why>)` (`:269`); `upstream.refresh_failed` (`proxy.py:454`) | `describe()`; `safe()` at the ledger |
| 4 | `upstream.py:296` and `:298` | a failed listen stream | stderr | `describe()` |
| 5 | `proxy.py:239` and `:249` | a tool name that isn't allowed | `upstream.connected`'s `skipped_tools`; stderr, `skipped tool <name>`, written with `repr` and never cut | `safe()` |
| 6 | `proxy.py:713` and `:718` (`_call_tool`) | a transport error: a -32000 message, or any other exception (pydantic's already got the fixed `UNREADABLE` line) | `call.returned`'s `error` and the client's one-line result (`_reply_error`, `proxy.py:739`) | `describe()` |
| 7 | the SDK's log records, among them `mcp/client/stdio.py:166` and `:223` and `mcp/client/session.py:1302`, `:1463` and `:1477` | messages and exceptions that quote an upstream: pydantic's message for a line the SDK can't parse quotes the line | stderr, through Python's last-resort handler, with a traceback of several lines | `SafeLog` (`upstream.py:71`), installed by `serve` (`cli.py:285`): one line per record at WARNING and above, through `safe()`, the exception through `describe()` |
| 8 | an upstream's own stderr | anything | serve's stderr: the SDK starts each upstream with serve's stderr as its own (`stdio_client`'s `errlog`, `mcp/client/stdio.py:115`; `Client` passes none, `mcp/client/client.py:396`) | not changed; Polarizer would have to start upstreams itself to filter it. A stated limit |

Not upstream text, made safe too because the same terminal shows it: the client's tool name in serve's stderr lines `refused <name>: could not record it` (`proxy.py:628` and `:668`).

**Fixed reasons, unchanged, because a fixed reason is enough:** `timeout after <n> s` at connect and on a refresh (`upstream.py:206` and `:261`), `more than 100 pages` and `more than 1000 tools`, the `UNREADABLE` line for a result the SDK can't parse, `upstream <prefix> returned a tool error`, the `unsupported` line (its kinds come from the SDK's `Literal` methods, never from the upstream), stage 4's four `cannot be hashed` reasons, and `upstream.connected`'s `protocol_version` (only a version the SDK accepted).

**Kept whole by design (one line, at most 1 KiB), because they are the tool's own text:**
1. **A `protocol-error`'s message** (`proxy.py:711` and `:712`, recorded through `one_line()` at `:686`). It is the tool's own answer to the call. The client gets it unchanged, with its code (PROXY-SPEC.md, Calls: "the same code and message"), and the ledger records what the client got: folded to one line, lone surrogates replaced, cut to 1 KiB, not escaped. The ledger is JSON, and canonical JSON writes characters below 0x20 as `\u00XX`, so a raw ESC never reaches the file; C1 controls (0x80 to 0x9f) and bidi characters are stored as UTF-8, so `cat` or `tail` on the ledger passes them to the terminal. No test sends one with hostile text.
2. **Results** (`ok` and `tool-error`) go to the client unchanged and never enter the ledger (`result_bytes` only).
3. **Tool definitions** go to the client from their stored copies. `pending` and `approve` print them escaped (`defhash.render`), and they never reach stderr.

**stdout:** serve's stdout carries only JSON-RPC (`test_stdout`), so upstream text is there only inside protocol messages, as in the three items above. `pending` escapes all it prints (stage 4), and `approve` prints names that serve recorded only after they matched `^[A-Za-z0-9._-]{1,128}$`.

**Tests** (`tests/test_upstream_text.py`): `safe()` and `describe()` on their own, `SafeLog`, a hostile upstream in memory and five hostile upstreams over stdio through `polarizer serve`. The hostile text has ESC sequences, a bell, a newline that starts a fake stderr line, a carriage return, 5,000 characters, a bidi override and a lone surrogate. Every stderr line, every string in the ledger and the client's reply must be printable ASCII, without the long run, and no stderr line may start with the fake one. Over stdio a lone surrogate can't arrive in a value the SDK parses: its JSON parser refuses the whole line (verified-facts.md, Stage 5 follow-up). So `tests/helpers/hostile_server.py` sends one in a line of its own, and the SDK's log of that line is what is checked; the in-memory test carries a raw lone surrogate through the error paths. With `describe()` put back as it was and the log handler off, four of the five tests failed (checked once, then restored).

### Shutdown order

Every path ends in the same three steps of `cli._serve`, and `os._exit` runs only after `ledger.close()` has returned:
- **End of input:** `_StdinLines._read` (`cli.py:368`) gets an empty read, or an `OSError`, and calls `stop.request()` on the event loop (`:390`) before it ends its stream. That sets the shutdown flag, cancels the background work and cancels the serving scope. The SDK's server cancels each call in flight, each handler records `call.returned` in a shielded scope, and `server.run` returns only after every handler has ended.
- **SIGINT or SIGTERM (POSIX):** `_signals` calls `stop.signal()`, which does the same. During startup it cancels startup, and serving is skipped.
- **Windows Ctrl+C:** a `signal.signal` handler calls `stop.signal` through `loop.call_soon_threadsafe`, then as above (not run).
- **Then:** step 2, `close_upstreams(1.0)`, in a scope of its own. Step 3 (`cli.py:429`), `ledger.close()` on a worker thread inside a shielded scope: the writer thread writes everything queued, every `call.returned` above included, `close()` fsyncs if anything is unsynced, and releases the ledger, its reader and the lock. Then `_exit_now()` (`cli.py:430`) flushes stdout and stderr and calls `os._exit(0)`.
- **A second signal** sets `hurry` and cancels step 2's scope, so step 3 starts at once. A signal during step 3 cancels nothing: its scope is shielded, and `close()` runs on a thread a cancel can't stop.

Three paths don't reach `os._exit`. None is changed here, because each would change an exit code or needs the owner:
- **The final fsync fails:** `close()` raises, `_exit_now` is never reached, and serve ends by normal teardown. `cli.serve`'s `finally` calls `close()` again, which does nothing, the exception ends the process with a traceback and exit code 1, and anyio first waits for the SDK's shielded upstream shutdown (up to about 6 s). Read, not run.
- **An exception escapes `_serve` before step 3** (a bug): steps 2 and 3 are skipped, `cli.serve`'s `finally` still closes and fsyncs the writer, and the same teardown follows.
- **SIGKILL, or `TerminateProcess` on Windows:** nothing runs. The ledger is as the last write left it, which section 8 of PIN-SPEC.md already covers.

### Upstream processes at shutdown

- **They end on their own** if they exit at end of input, which the SDK sends within about 0.5 s of step 2 and serve's exit sends anyway, or when their stdout breaks, at their next write after serve exits. The probe and the SDK's servers do the first, and so does the Filesystem server, which returns at once with stdin at `/dev/null` (MANUAL-CHECK.md, step 1); the Everything server wasn't checked.
- **They can be left running** if they ignore end of input and never write again: `sleep 3600` in MANUAL-CHECK.md step 6, a server that its own threads keep alive, and anything such a server started. The SDK's sequence (close stdin, 2 s, SIGTERM to the process group, 2 s, SIGKILL) is cut off by `os._exit` after 1 s, before its SIGTERM. On POSIX each upstream runs in a session of its own, so no terminal hangup or signal to serve reaches it. A leftover also keeps serve's stderr open, because it writes its stderr to serve's: `subprocess.run(..., capture_output=True)` on such a serve returned after 60.7 s, when the probe's 60 s linger ended, though serve itself had exited long before (the test below waits for serve's own exit, and takes about 1.7 s in all).
- **A bounded terminate step isn't possible with the SDK's handles.** `stdio_client` keeps the process in a local variable, and `Client` exposes neither it nor its pid (verified-facts.md, Stage 5 follow-up). Finding the process by name or by walking the process table would be a pattern, which the brief rules out. So it is a stated limit, in PIN-SPEC.md section 8, step 2, and MANUAL-CHECK.md steps 6 and 8 and `scripts/m1a-check.sh finish`, with the commands that list leftovers. Two ways the owner could choose later, neither done: wrap the SDK's private `_create_platform_compatible_process` to keep the handles, at the cost of depending on a private function of a pinned SDK, or start upstreams with a transport of Polarizer's own.
- **The test,** `test_shutdown::test_upstream_ignoring_end_of_input_is_left_running` (POSIX only): the probe with `PROBE_LINGER=60` ignores end of input for 60 s. serve, with stdin closed, exits 0 well before that, the probe logs end of input and `lingering`, and it is still running afterwards; the test then kills it by the pid it logged.
- **On Windows** the SDK puts each upstream in a job object that is killed when its last handle closes, so the upstream should end with serve. Not run.

### Leaked probes, found while testing this (fixed)

`test_signal_during_startup` left one probe running on every run since stage 5. serve, stopped during startup, had already written `server/discover` into the probe's stdin pipe. The probe, still in its 5 s delay, read it afterwards and answered into a stdout nobody read; the `BrokenPipeError` ended its reader thread, and its main thread then waited forever. Nothing sent it SIGTERM, because serve exited at its 1 s bound: the leftover case above.
- **Found:** 22 such probes were running when this session started. They started between 01:05 and 01:42 UTC on Oct 4, about 86 s apart, one per suite run, which matches stage 5's repeated runs. This session's first two runs of `tests/test_shutdown.py` added two more, which were stopped by their pids. The 22 older ones were not started in this session and are left for the owner: `pgrep -a -f 'tests/helpers/probe_server.py'` lists them, and `kill` with the pids it prints stops them. They hold no repo files open; each waits on a pipe.
- **Fixed:** the probe now exits, logging `stdout closed`, when a write to stdout fails. `test_signal_during_startup` checks that the probe ends on its own and kills it by pid in a `finally`. No test upstream from this session's later runs was left running (counted after each run of the repeated runs and of `scripts/test.sh`).

### Windows hazards in the stdin reader and the shutdown path

Nothing here has run on Windows.

| What | What differs on Windows | Test | That test on Windows |
|---|---|---|---|
| `os.read(0, ...)` on a daemon thread | Works on an anonymous pipe, which is what a client gives serve. End of input may come as `ERROR_BROKEN_PIPE`, an `OSError`, rather than an empty read; the reader treats both as end of input. A console as stdin would hand over lines after Enter and end at Ctrl+Z, which no client does. A daemon thread blocked in the read doesn't stop `os._exit`. | `test_shutdown::test_stdin_closed_during_call`; `test_pin_startup::test_prime_with_closed_stdin` and `test_upstream_text::test_hostile_upstream_over_stdio` also end serve by end of input | Expected to run as on Linux |
| Line endings on stdin | A client that writes `\r\n` leaves `\r` at the end of each line, since the reader splits on `\n`. JSON allows it as whitespace. | none | None |
| Signal handling | anyio has no signal receiver on Windows. Only SIGINT is handled, through `signal.signal` and `loop.call_soon_threadsafe`, and it comes only from Ctrl+C in a shared console. `os.kill(pid, SIGTERM)` is `TerminateProcess`, so no shutdown path runs and a call in flight reads as outcome unknown. Ctrl+Break (SIGBREAK) isn't handled and ends the process the same way. Whether the handler runs promptly while the proactor event loop waits is unverified. | the four signal tests in `tests/test_shutdown.py` | Skipped (POSIX only), as PIN-SPEC.md section 10 says; the Ctrl+C path has no test |
| `os._exit(0)` | Ends the process at once on every platform; the operating system closes every handle (the ledger, the lock, the upstreams' pipes). The order is the same: the writer is closed and flushed (`os.fsync` is `FlushFileBuffers`) before it. | `test_shutdown::test_stdin_closed_during_call` (exit 0, ledger intact) | Expected to run |
| Ending upstream processes | The SDK runs each upstream in a job object with `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`, so closing serve's handle at exit should end the upstream and its children: no leftovers. The SDK's own stop there closes stdin, waits 2 s, then kills the job outright, with no SIGTERM step. | `test_shutdown::test_upstream_ignoring_end_of_input_is_left_running` | Skipped: on Windows it would need the opposite assertion, which isn't written. Not run |
| Upstream stderr | Inherited as on POSIX, so a leftover would keep it open, if one could be left. | `test_upstream_text::test_hostile_upstream_over_stdio` (its stub writes no stderr) | Expected to run |
| The probe's broken stdout | A write to a closed pipe may raise `OSError` with `EINVAL` rather than `BrokenPipeError`; the probe catches any `OSError`. | `test_shutdown::test_signal_during_startup` | Skipped (POSIX only) |

### Time bounds in the follow-up's tests

| Test | Bound | The operation needs |
|---|---|---|
| `test_shutdown::test_upstream_ignoring_end_of_input_is_left_running` | serve exits within 30 s; `lingering` logged within 30 s | about 1.5 s; milliseconds after serve exits |
| `test_shutdown::test_signal_during_startup` (added) | `stdout closed` within 30 s, then the probe gone within 30 s | about 5 s (the probe's delay); milliseconds |
| `test_shutdown` cleanup (`_stop`) | gone within 10 s of SIGKILL (no assertion) | milliseconds |
| `test_upstream_text::test_hostile_upstream_over_stdio` | `run_serve`'s 120 s; the stopped serve within 30 s (`RawClient.close`) | about 2 s (the `cg` stub's 1 s connect timeout, then the 1 s bound); about 1 s |
| `test_upstream_text::test_hostile_upstream_in_memory` | `rig`'s defaults | milliseconds |

### Repeated runs

The changed test files (`test_upstream_text`, `test_shutdown` and `test_proxy`), and the files that exercise the changed helpers and stderr lines most (`test_defhash`, `test_listing`, `test_stdout` and `test_pins`), 64 tests, on the final code, five times in a row: 64 passed each time, in 57.7 to 71.0 s. After each run, the number of test upstream processes still running was 22, the older probes above, so none of the five left one. Not run under load this time.

### Pre-push scan

Every tracked file in the commit (`git grep --cached`), and the commit's message, author and committer, searched case-insensitively for each of the eight private strings the owner listed for this commit: two paths, a username, a mail domain and four names. They aren't repeated here, or the scan would find this line. Counts only: all eight were 0 in the files, and 0 in the message, author and committer.

### Not run in the follow-up

- CI, so nothing here has run on Windows or macOS, or on Python 3.11 or 3.13.
- Claude Code, any model, an interactive session and `scripts/live-check.sh`.
- The reference tests. They don't exercise any changed path except serve's stderr, which now goes through `SafeLog` too.
- A hostile `protocol-error` message: kept whole by design, and not tested with hostile text.

## The M1a check script

`scripts/m1a-check.sh` (parsing in `scripts/m1a_check.py`) replaces MANUAL-CHECK.md's M1a steps, which had placeholders for the group id and hashes, with five commands that find them from `polarizer pending`, ask the owner only what `/mcp` and Claude showed, and record everything in `/tmp/m1a-check-results.txt`; its claims are the table's last rows, run in the final `scripts/test.sh` of that change (524 passed, 7 skipped, in 121.5 s).

## The rug-pull check script

`scripts/rugpull-check.sh` (helpers in `scripts/rugpull_check.py`) replaces MANUAL-CHECK.md's M1a steps with one headless run that asks nothing. `scripts/m1a-check.sh` stays, marked superseded; the owner ran it interactively on 2026-10-04 (verified-facts.md, M1a check, interactive).

- **Why:** the probe's `PROBE_RUGPULL` switched on its second start, so any extra start (a reconnect, a priming run, a second session) changed what the check saw. The probe now also takes `PROBE_PHASE` (`original` or `changed`), which takes precedence over `PROBE_RUGPULL` when set; `PROBE_RUGPULL` alone behaves as before.
- **How:** one temp directory with its own `polarizer.toml` (the probe as the only upstream, `PROBE_PHASE = "${PROBE_PHASE}"`) and two mcp configs that differ only in `PROBE_PHASE`. Priming reuses `live_check.py`'s; the approval between runs B and C uses `Decider.approve_one`, as `polarizer approve probe wait <hash>` does. Three `claude -p` runs (haiku, `--strict-mcp-config`, `--allowedTools=mcp__pz__probe__wait`, `--permission-mode default`, `--no-session-persistence`, stdin from `/dev/null`, `MCP_TOOL_TIMEOUT` unset).
- **What decides the result:** nine checks read from the ledger, the probe's log and `polarizer verify`, each run's part being what was appended between its start and end marks. The model's replies are printed only. A run whose claude exits non-zero fails its checks with the first lines of its output, and a run B in which Polarizer never started fails its two no-call checks instead of passing them.
- **Tests:** a stub claude (`tests/helpers/fake_claude.py`, named by `CLAUDE_BIN` only under `POLARIZER_CHECK_TESTING=1`) starts the Polarizer its config names and calls `probe__wait` only if it is listed. Its modes make B reach the probe (it approves the new definition mid-run, standing in for a Polarizer that lets it through), stop the drift (the probe kept in its original phase), tamper with the ledger after run C, and exit 1 in run B.
- **Repeated runs:** `tests/test_rugpull_check.py`, five times in a row: 12 passed each time, in 32.4 to 32.6 s, with no probe or serve process left running after any of them.
- **Pre-push scan:** every tracked file in the commit (`git grep --cached`), and the commit's message, author and committer, searched case-insensitively for the eight private strings the owner listed. Counts only: all 0.
- **Run once with real Claude Code** by the owner, after the commit, from inside a Claude Code session: all nine checks passed, 0.045214 USD. It shows that a changed definition is hidden from a real agent until it is approved. It does not show the refusal path (in run B the model never named the hidden tool, so the ledger has no `call.refused`), re-listing within one live session (answered by the interactive check), a real third-party server, or Windows and macOS (verified-facts.md, Rug-pull check).
- **Not run:** CI, and shellcheck (not installed; `bash -n` passed).

## Follow-up: the rug-pull result, re-listing notes and the guard's exit code

- **The rug-pull result** is recorded above and in verified-facts.md, Rug-pull check, with what it does and does not show.
- **Stale re-listing notes:** PIN-SPEC.md (the Telling Claude Code table, the paragraph after it, and decision 9), PROXY-SPEC.md (M1a list changes), QUICKSTART-DRAFT.md (the approval steps) and verified-facts.md (Unverified, Interactive sessions) said or implied that interactive re-listing was unverified. They now cite the owner's interactive check of 2026-10-04 (verified-facts.md, M1a check, interactive), and keep the `/mcp` reconnect as the fallback. STAGE5-NOTES.md, Not run, and PIN-SPEC.md section 9's steps describe that check before it ran and are unchanged.
- **The guard's exit 1 at snapshot:** not the guard's. The earlier session ran `scripts/guard.sh snapshot && ls ... && wc -l ... scripts/*`, and `wc` exited 1 on reaching the directory `scripts/__pycache__`; the shell reports the last command's code. Run alone three times, and once under `bash -x`, snapshot exited 0.
- **A real guard problem found while looking:** if `find` could not read an entry under a guarded directory, snapshot exited 1 with no output and left a truncated `snapshot-*.txt`, which check would then use as its baseline; check exited 1, which also means a change, after comparing nothing. Now `find`'s error is shown, the state is read under `inherit_errexit` so check stops too, snapshot writes `partial-<timestamp>.txt` and renames it only once complete, and either command prints a `guard: stopped` line and exits 2. The script's header lists the exit codes. No check was removed or loosened.
- **In-session notice:** `scripts/rugpull-check.sh` prints, as its first line, a notice when `CLAUDE_CODE_SESSION_ID` is set: the documented conditions are a plain terminal, and these `CLAUDE_CODE_*` variables (names only) are passed on. What it checks is unchanged. The test fixture now drops inherited `CLAUDE_CODE_*` variables, so the tests run alike inside a session or not.

## Claims

"Yes" means run locally on Linux (WSL2, Python 3.12.3, mcp 2.2.0), in the final run of `scripts/test.sh` (509 passed, 7 skipped: stage 1's 4 platform skips and the 3 reference tests), unless the row says otherwise. The rows from "Upstream text" on are the follow-up's, run in its final `scripts/test.sh` (515 passed, 7 skipped, in 112.8 s).

| Claim | Command | Run? |
|---|---|---|
| The watch applies another process's approval within about a second, with a change notice, without a client `tools/list` | `pytest tests/test_pins.py::test_approve_exposes` | yes |
| A rejection hides an approved tool and the client gets a notice | `pytest tests/test_pins.py::test_reject_hides_and_needs_reason` | yes |
| While the approving writer's fsync is held, the tool stays hidden; once released, it is exposed | `pytest tests/test_pin_durability.py::test_approval_fsynced_before_exposed` | yes |
| serve fsyncs the ledger itself right before an adopted approval reaches its pin state; if that fsync fails, nothing is exposed and the writer stops | `pytest tests/test_pin_durability.py::test_serve_fsyncs_adopted_approval` | yes |
| `python -m polarizer approve` in another process exposes the tool within 2 s, with a notice | `pytest tests/test_pin_durability.py::test_approve_from_second_process` | yes |
| The watch reads nothing while the file hasn't grown, and exactly the new bytes when it has, at 100 and 10,000 entries | `pytest tests/test_pin_durability.py::test_watch_reads_only_new_bytes` | yes |
| A 2026-07-28 upstream's rug pull hides its tools through a listen event, records one drift per tool, and notifies the client | `pytest tests/test_pins.py::test_drift_mid_session_modern` | yes |
| The same through a 2025-11-25 upstream's list_changed, in memory and with the stdio probe's `change` tool | `pytest tests/test_pins.py::test_drift_mid_session_handshake` | yes |
| A rug pull records one drift however often it is listed; a revert keeps the tool hidden and records nothing | `pytest tests/test_pins.py::test_drift_once_per_pair_and_sticky` | yes |
| A flip-flopping upstream records at most 16 observations and one cap entry, and its notices give the client exactly one notice | `pytest tests/test_pins.py::test_flip_flop_is_bounded` | yes |
| An upstream notice that changes nothing exposed reaches neither client era; one that does reaches both | `pytest tests/test_pins.py::test_notice_only_when_exposed_list_changes` | yes |
| Notices are at least a second apart | `pytest tests/test_pins.py::test_notices_at_most_once_per_second` | yes |
| A failed refresh is retried after 30, 60, 120, 240 s, then every 300 s, each attempt bounded by the connect timeout; success exposes the tools again and resets the interval; a lost connection is never retried; one record per run of failures | `pytest tests/test_pins.py::test_failed_refresh_retries_on_timer` | yes |
| SIGINT then SIGTERM 100 ms later, mid-call: serve exits 0, the ledger verifies, one `call.returned` cancelled with `polarizer shut down during the call`, and the upstream gets its cancel | `pytest tests/test_shutdown.py::test_sigint_then_sigterm_during_call` | yes (POSIX only) |
| The same with SIGTERM alone, the probe closed in step 2 | `pytest tests/test_shutdown.py::test_sigterm_alone_during_call` | yes (POSIX only) |
| Closing stdin mid-call gives the same ledger result | `pytest tests/test_shutdown.py::test_stdin_closed_during_call` | yes |
| SIGKILL mid-call leaves an intact ledger with a lone `call.sent`, and a new serve exposes the same tools | `pytest tests/test_shutdown.py::test_kill_then_restart` | yes (POSIX only) |
| A signal during startup shuts down at once and exits 0 | `pytest tests/test_shutdown.py::test_signal_during_startup` | yes (POSIX only) |
| End of input during startup still finishes startup (priming) | `pytest tests/test_pin_startup.py::test_prime_with_closed_stdin` | yes |
| live-check's priming records and approves the probe's 7 tools with no model | `pytest tests/test_live_check.py::test_prime_exposes_the_probe_tool` | yes (POSIX only) |
| The probe serves `change`, and `serve`'s stdout carries only JSON-RPC | `pytest tests/test_stdout.py` | yes |
| The new and changed tests pass five times in a row and once under load | the loop in Repeated runs | yes, 5 of 5 and 1 of 1 |
| The whole suite, ruff and the docs check | `scripts/test.sh` | yes: 509 passed, 7 skipped |
| The live check passes after priming | `scripts/live-check.sh` | no: for the owner, from a plain terminal |
| Interactive Claude Code lists the tools again after an approval, without a reconnect | `scripts/m1a-check.sh approve` and `approve-changed` | yes, by the owner, interactively, on 2026-10-04 with Claude Code 2.1.289: `y` and `y` (verified-facts.md, M1a check, interactive) |
| The rug-pull check in an interactive session | `scripts/m1a-check.sh rugpull` | no: the owner skipped it on 2026-10-04; `scripts/rugpull-check.sh` checks the rug pull headless instead |
| All of the above on Windows and macOS | CI | no |
| Upstream text: `safe()` escapes, folds, cuts to 200 characters, never splits an escape, and changes nothing the second time; `describe()` never quotes pydantic's input | `pytest tests/test_upstream_text.py::test_safe_escapes_folds_and_cuts tests/test_upstream_text.py::test_describe_is_safe_and_quotes_no_pydantic_input` | yes |
| A log record, the SDK's included, reaches stderr as one safe line with no traceback | `pytest tests/test_upstream_text.py::test_log_records_are_one_safe_line` | yes |
| A hostile upstream that fails at connect, fails a refresh and drops its connection (ESC, a newline, 5,000 characters, a raw lone surrogate) leaves nothing raw on stderr, in the ledger or in the client's transport-error line | `pytest tests/test_upstream_text.py::test_hostile_upstream_in_memory` | yes |
| The same over stdio through `polarizer serve`, both runs' stderr, with the SDK's log of lines it can't parse, a pydantic listing error and a hostile skipped name | `pytest tests/test_upstream_text.py::test_hostile_upstream_over_stdio` | yes |
| The new tests fail on the old `describe()` with the log handler off | the same file, with `describe()` reverted by hand | yes, once: 4 of 5 failed |
| serve closes and fsyncs its writer before `os._exit` on every shutdown path (end of input, SIGINT, SIGTERM, a second signal) | read: Follow-up, Shutdown order; the shutdown tests check the ledger after each path | read; no new test |
| serve exits without waiting for an upstream that ignores end of input, and leaves it running (stated limit) | `pytest tests/test_shutdown.py::test_upstream_ignoring_end_of_input_is_left_running` | yes (POSIX only) |
| `test_signal_during_startup`'s probe ends on its own and is no longer left running | `pytest tests/test_shutdown.py::test_signal_during_startup` | yes (POSIX only) |
| The follow-up's changed and affected test files pass five times in a row, leaving no test upstream running | the loop in Follow-up, Repeated runs | yes, 5 of 5 |
| The whole suite, ruff and the docs check, after the follow-up | `scripts/test.sh` | yes: 515 passed, 7 skipped, in 112.8 s |
| The pre-push scan finds none of the private strings | Follow-up, Pre-push scan | yes: every count 0 |
| `scripts/m1a-check.sh`: reset, approve, rugpull, approve-changed and finish in order on a pseudo-terminal with scripted answers, a `serve` run with stdin closed standing in for each Claude Code start; status names the right next step before each; every printed command is complete; the toml and rug-pull file are back as before | `pytest tests/test_m1a_check.py::test_whole_check` | yes (POSIX only) |
| Ctrl+C or a blank answer stops a step with a line saying what was done, other text is asked again, and a step run again carries on | `pytest tests/test_m1a_check.py::test_ctrl_c_and_blank_answers_stop_cleanly` | yes (POSIX only) |
| The script refuses the test overrides without `POLARIZER_CHECK_TESTING=1` before writing anything, and reset and finish refuse while a probe runs, naming its pid in the `kill` command | `pytest tests/test_m1a_check.py::test_reset_refusals` | yes (POSIX only) |
| `scripts/m1a_check.py` reads `pending`'s real output, the golden files, the toml and the ledger | the other six tests in `tests/test_m1a_check.py` | yes |
| The new tests pass five times in a row and leave no process running | `pytest tests/test_m1a_check.py`, five times | yes, 5 of 5 |
| shellcheck on `scripts/m1a-check.sh` | `shellcheck scripts/m1a-check.sh` | no: not installed; `bash -n` passed |
| The script in a real check, with Claude Code | `scripts/m1a-check.sh reset`, then each step | yes, by the owner, on 2026-10-04, except the rugpull step; the 53-entry ledger verified intact |
| The probe's `PROBE_PHASE` serves the original or the changed `wait` on every start, takes precedence over `PROBE_RUGPULL` (whose file it neither reads nor creates), and refuses other values; `PROBE_RUGPULL` alone is unchanged | `pytest tests/test_rugpull_check.py -k probe` | yes |
| rugpull-check's two mcp configs differ only in `PROBE_PHASE`, and its toml passes it to the probe | `pytest tests/test_rugpull_check.py::test_setup_configs_differ_only_in_phase` | yes |
| With a stub claude, all nine checks pass, the temp directory is removed, the results file holds the whole output, and claude got the stated arguments, stdin from `/dev/null`, the temp directory as working directory and no `MCP_TOOL_TIMEOUT` | `pytest tests/test_rugpull_check.py::test_all_checks_pass` | yes (POSIX only) |
| A run B that reaches the probe fails exactly B's two no-call checks | `pytest tests/test_rugpull_check.py::test_b_calling_the_tool_fails` | yes (POSIX only) |
| No drift fails the drift and approval checks | `pytest tests/test_rugpull_check.py::test_no_drift_fails` | yes (POSIX only) |
| A tampered ledger fails only the verify check | `pytest tests/test_rugpull_check.py::test_tampered_ledger_fails` | yes (POSIX only) |
| A claude that exits non-zero fails its run's checks, with the first lines of its output, and the temp directory is kept | `pytest tests/test_rugpull_check.py::test_claude_exiting_nonzero_fails` | yes (POSIX only) |
| A run B with no Polarizer start fails its no-call checks rather than passing them | `pytest tests/test_rugpull_check.py::test_b_without_polarizer_proves_nothing` | yes |
| Under `POLARIZER_CHECK_TESTING=1`, the script needs `CLAUDE_BIN` and `POLARIZER_CHECK_RESULTS` and writes nothing without them | `pytest tests/test_rugpull_check.py::test_testing_needs_both_overrides` | yes (POSIX only) |
| The new test file passes five times in a row and leaves no process running | `pytest tests/test_rugpull_check.py`, five times | yes, 5 of 5 |
| The whole suite, ruff and the docs check, with the rug-pull check | `scripts/test.sh` | yes: 536 passed, 7 skipped, in 148.1 s |
| shellcheck on `scripts/rugpull-check.sh` | `shellcheck scripts/rugpull-check.sh` | no: not installed; `bash -n` passed |
| The rug pull end to end with real headless Claude Code | `scripts/rugpull-check.sh` | yes, once, by the owner, inside a Claude Code session, on 2026-10-04 with Claude Code 2.1.289: all nine checks passed, 0.045214 USD (verified-facts.md, Rug-pull check) |
| guard.sh: snapshot then check exits 0; a change exits 1; a root `.mcp.json` warns, exits 1 and still records the snapshot | `pytest tests/test_guard.py` (three tests) | yes (POSIX only) |
| guard.sh: an unreadable entry stops snapshot with exit 2, a `guard: stopped` line and no `snapshot-*.txt`, and stops check with exit 2; both fail on the old script | `pytest tests/test_guard.py -k unreadable` | yes (POSIX only) |
| rugpull-check.sh: with `CLAUDE_CODE_SESSION_ID` set, the first line is the notice naming the `CLAUDE_CODE_*` variables without their values, and the nine checks are unchanged; without it, no notice | `pytest tests/test_rugpull_check.py -k "notice or all_checks_pass"` | yes (POSIX only) |
| The whole suite, ruff and the docs check, after this follow-up | `scripts/test.sh` | yes: 542 passed, 7 skipped, in 153.8 s |
