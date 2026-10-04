# Stage 5 notes

Read after docs/STAGE4-NOTES.md. This file records what stage 5 (M1a, pins in motion and process lifetime) decided on its own: what step 0 found, the deviations, the guesses, the time bounds in the new tests, the repeated runs, what could not be run, the platform hazards, and a claims table. Where these notes and the specs differ, the specs win until a spec is changed. PIN-SPEC.md deviations 38 to 45 summarize the decisions below that change the spec.

The session first fixed the stage 4 review items (two commits; docs/STAGE4-NOTES.md, Review fixes), then built stage 5 in one commit, "stage5: pins in motion and shutdown". No change to the ledger format, the hash chain, a status or an exit code.

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

## Claims

"Yes" means run locally on Linux (WSL2, Python 3.12.3, mcp 2.2.0), in the final run of `scripts/test.sh` (509 passed, 7 skipped: stage 1's 4 platform skips and the 3 reference tests), unless the row says otherwise.

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
| Interactive Claude Code lists the tools again after an approval, without a reconnect | docs/MANUAL-CHECK.md, M1a, steps M3 and M5 | no: for the owner |
| The rug-pull check in an interactive session | docs/MANUAL-CHECK.md, M1a, step M4 | no: for the owner |
| All of the above on Windows and macOS | CI | no |
