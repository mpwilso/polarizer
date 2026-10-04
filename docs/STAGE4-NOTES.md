# Stage 4 notes

Read after docs/STAGE3-NOTES.md. This file records what stage 4 (M1a, pins at rest) decided on its own: what step 0 found, the deviations, the guesses, what could not be run, the platform hazards, and a claims table. Where these notes and the specs differ, the specs win until a spec is changed.

Stage 4 began with the commit "spec: m1a decisions", which applied the owner's ten decisions to PIN-SPEC.md, PROXY-SPEC.md, LEDGER-SPEC.md, m0-plan.md, milestones.md, MANUAL-CHECK.md and QUICKSTART-DRAFT.md. Then it added `src/polarizer/defhash.py` (the definition hash and the stored copy), `src/polarizer/pins.py` (the fold, the states and what `pending` lists) and `src/polarizer/decisions.py` (approve and reject), changed the gateway, the upstreams, the writer and the command line, and added the pin tests. The two fixed hashes in PIN-SPEC.md section 10 matched the implementation on the first run; no expected value was changed.

## What step 0 found

Nothing needed the owner: no gap touched the ledger format, a hash form beyond decision 1, a status or an exit code.

1. **Stage 4 needed part of stage 5's watch.** `rig.gateway(approve=True)` must "approve all and wait until the exposed list is complete", and `test_writer_stopped_exposes_nothing` must see a line another process appended. Both need a running gateway to read the ledger, which section 8 assigns to the watch, and the stage 4 prompt excluded the watch. Decided: the catch-up at the start of every client `tools/list` and `tools/call`, with serve's own fsync before acting on an adopted approval, is built in stage 4. The once-a-second timer, change notices on decisions and `test_pin_durability.py` stay in stage 5. Recorded in PIN-SPEC.md (section 8, section 12 and deviation 30).
2. **Sticky drift and the cap** were listed under stage 5 but are part of the fold (the changed state) and of section 3's recording rules, which stage 4 builds. Built in stage 4 (PIN-SPEC.md deviation 31).
3. **The stage 4 tests whose claims include a change notice** (`test_approve_exposes`, `test_reject_hides_and_needs_reason`, `test_flip_flop_is_bounded`) were written without the notice assertion. Stage 5 adds it.
4. **A revert.** PIN-SPEC.md section 4 said `pending` shows a revert as `changed <prefix>__<tool> H, approved H`. Section 3 records no drift when the live hash returns to the approved one, and section 10's `test_drift_once_per_pair_and_sticky` requires that nothing is recorded, so no record can name H twice. Sections 3 and 10 were followed, and section 4's sentence now says a revert records nothing: `pending` keeps showing `changed ... B, approved A`, and the person approves A again by name, which `approve` accepts.

## Deviations

1. **The catch-up before each list and call is in stage 4** (step 0, item 1).
2. **Sticky drift and the cap are in stage 4** (step 0, item 2).
3. **The writer fsyncs the ledger once when it opens with a pin-state listener** (`serve`, `approve`, `reject`), before the verified entries reach pin state. An approval written by a process whose own fsync failed is then durable before anything acts on it at startup, as section 8 requires for adopted approvals while running. `verify` and `pending` never fsync.
4. **A lost connection is detected at the next request, not the moment the upstream exits.** The SDK raises nothing into the task holding the client when an idle stdio upstream exits (verified-facts.md, Stage 4). A call or listing that fails with -32000 is confirmed with the session's ping, which fails at once on a closed connection; only then is the upstream marked lost, its tools hidden and `connection-lost` recorded. An upstream that sends -32000 itself still answers the ping, so it isn't hidden.
5. **M0 tests whose assertions change by design** (PIN-SPEC.md section 12, stage 4 step 6):
   - `test_proxy::test_upstream_isolation`: after `crashy__crash`, `crashy__wait` is refused (`its server's tool list could not be checked`) and recorded as `call.refused`, not as a second `transport-error`;
   - `test_listing::test_refresh_failure_keeps_last_list` is replaced by `test_refresh_failure_hides_the_upstream`;
   - `test_listing::test_every_client_list_refreshes`, `test_eras::test_modern_upstream` and `test_eras::test_handshake_upstream`: a changed definition is hidden until approved again, and the tests then approve it and see the new one;
   - `test_proxy::test_ledger_verifies_after_a_session` counts entries by kind;
   - `test_reference::test_raw_wire`: every tool with `execution` now differs by `execution` on the 2025-11-25 side too;
   - the stdio tests (`test_stdout`, `test_fidelity`'s stdio and raw tests, `test_proxy::test_meta_filter_over_stdio`, `test_eras::test_modern_upstream[stdio]`, `test_config::test_upstream_gets_minimal_environment`, `test_reference`) prime their ledger with `rig.prime`.
6. **Upstream change notices are still passed on as M0 does.** The client's `tools/list` that follows refreshes the upstream and checks the pins. Coalesced refreshes and notices only when the exposed list changes are stage 5.
7. **stderr lines changed:** `polarizer: upstream <p>: listing timed out; its tools are hidden`, `polarizer: upstream <p>: listing failed (<why>); its tools are hidden` and `polarizer: upstream <p> stopped: <why>; its tools are hidden` replace M0's `kept its last list` lines. New: `polarizer: <n> tools wait for approval; run polarizer pending`.
8. **A decision whose write fails with an operating-system error** prints `polarizer: could not record the decision: <why>` and exits 1, the code a stopped writer already uses.
9. **Library changes:** `verify_bytes` takes `on_entry`; `ChainState` keeps `last_entry`; `LedgerWriter` takes `on_entry` and gains `catch_up()` and `stopped`; `Upstream` gains `list_error`, `list_known`, `lose()` and `check_closed()`, its `refresh()` returns whether it succeeded, and `exposed()` is gone (the gateway serves stored copies). `Gateway` takes `pins=`.
10. **Tests beyond PIN-SPEC.md section 10:** `test_pins::test_fold_gives_every_state` (stage 4 step 2's done-when), `test_defhash::test_written_copy_never_replaces` and `test_copy_modes`, `test_pin_cli::test_reject_updates_head_and_can_revoke`, `test_decisions_need_a_ledger`, `test_group_writer_fails_partway`, `test_approve_runs_on_a_terminal` (the pseudo-terminal half of `test_approve_needs_terminal`) and `test_pending_on_a_broken_ledger_prints_verify`, and the golden row `pending_capped.txt`.
11. **Conformance fixtures added** for stage 4 step 3: `valid/pins` (every M1a kind), `broken/pins_edit_approval` (tampered) and `broken/pins_rehashed_rejection` (a rejection edited and re-hashed, caught by `ledger.head`). Both verifiers agree on all three; the reference verifier is unchanged.
12. **Test helpers:** `rig` gains `approve_all`, `approve_changed`, `approve_pending`, `prime` and `run_serve`; `FakeUpstream` gains `definitions=`, `list_delay` and `bump_on_list`, and `FAKE_DEFINITIONS`; the probe gains `PROBE_SNAKE`; `tests/helpers/pinledger.py` builds the deterministic ledgers for the pin golden files.
13. **`test_group_is_bound_to_what_was_printed`** reads "a changed definition" as a grouped tool seen with another definition. Since the owner's decision 3, a decided tool's change can't move the group either way, and the test checks that such tools are left out.
14. **`pyproject.toml`'s comment** on the SDK pin no longer cites M0's `mcp>=2.2,<3` range; the pins themselves are unchanged and `uv.lock` did not change.

## Guesses

1. **State precedence.** A live definition that can't be hashed, or a new hash beyond the cap, is unservable whatever the decisions say. A hash already observed under the cap is pending or changed as usual.
2. **`pending` lists every hash observed since the latest decision,** one block each, and a group approves a tool's several new hashes in listing order, so the most recently seen becomes its latest decision.
3. **Unservable blocks** are the `tool.unservable` records since each tool's latest decision, once per hash and problem. A stored-copy problem is checked again; a copy that passes now is left out, and one that fails is printed with its file and its current problem.
4. **The count in `<n> tools wait for approval`** is the listed tools in the pending and changed states, after startup, and the line is printed only when it is at least 1.
5. **Over the cap,** the 17th hash's copy is not stored; its `tool.unservable` names that hash.
6. **A call re-reads and rehashes an approved tool's stored copy** before it is forwarded, as `tools/list` does, so a copy altered since the last list is refused at once.
7. **Routing puts "list unknown" before "not listed"** for a lost upstream as for a failed refresh, as section 6 orders them.
8. **The terminal check** is `os.isatty(sys.stdin.fileno())`; no stdin, or one without a file descriptor (pytest's), counts as not a terminal.
9. **The group check runs when `approve --group` opens the ledger.** Between that and its appends, only serve's observations can land, within milliseconds; a decision by another person needs the lock the approving writer takes for each append, and is adopted before it.
10. **`pending` on an intact v0 ledger** prints `pending: nothing waits for a decision`: v0 holds no pin entries.
11. **The error of a timed-out refresh** is `timeout after <n> s`, as for a connect timeout.
12. **A copy is written for every live hash not beyond the cap,** including one the latest decision covers, which is how a deleted copy comes back at the next refresh.
13. **`pending` doesn't filter by the configured upstreams:** it lists whatever the ledger names.
14. **The fold ignores `tool.*` entries whose fields aren't strings,** which only a hand-written ledger could hold.

## Not run

- **CI,** and so **Windows and macOS runs** of everything stage 4 added or changed. Only Linux (WSL2, Python 3.12.3) ran it.
- **Claude Code, any model, and an interactive session.** Nothing in stage 4 ran `claude`. **`scripts/live-check.sh` does not work after stage 4:** its generated ledger is not primed, so the probe's tool is not exposed and the model can't call it. Priming it is stage 5.
- **`test_reference::test_sdk_clients` and `test_reference::test_raw_wire`** were changed (priming, and `execution` on the 2025-11-25 side) but not run: the stage 4 prompt said to set `POLARIZER_REFERENCE=1` only for `test_reference_tools_hash_and_serve`.
- **Repeated runs under load.** The new and changed tests passed five times in a row on an idle machine, nothing more.

## Windows and macOS hazards in the new code

- **Stored copies on Windows** are moved into place with `os.rename`, which fails if the target exists, instead of POSIX's `os.link`; that path has not run. The temp file is removed after a failed rename.
- **`test_lost_upstream_hides_tools`** ends the idle probe with `os.kill(pid, SIGTERM)`, which is `TerminateProcess` on Windows, and then waits 0.5 s.
- **`test_unwritable_stored_copy_hides`** and `test_copy_modes` are skipped on Windows, and `test_approve_runs_on_a_terminal` too (no `pty` module).
- **fsync cost:** `test_listing::test_paging_limits` now stores 1,100 copies and records 1,100 approvals, each fsynced; it took 8.3 s on WSL2 and may take much longer where fsync is slow.
- **The terminal check on Windows** uses `os.isatty`, which reports a console as a terminal; a pipe is not one.

## Claims

"Yes" means run locally on Linux (WSL2, Python 3.12.3, mcp 2.2.0), in the final run of `scripts/test.sh` unless the row says otherwise.

| Claim | Command | Run? |
|---|---|---|
| The probe's `wait` hashes to `ac0778cf...` with the stored bytes written in the test, and the nested description to `3462be54...` | `pytest tests/test_defhash.py -k "fixed or nested"` | yes |
| One tool with `execution` gives one hash through `legacy` and `auto` clients, in memory and over stdio | `pytest tests/test_defhash.py::test_same_hash_in_both_eras` | yes |
| `execution` is the only field one era serves and the other doesn't, and `Tool` has exactly the nine known fields | `pytest tests/test_defhash.py::test_execution_is_the_only_era_field` | yes |
| Nulls, key order, unknown fields and schema keywords hash as PIN-SPEC.md section 2 says | `pytest tests/test_defhash.py -k "null or key_order or unknown"` | yes |
| A change to `_meta` alone changes neither the hash nor the copy and doesn't hide the tool; no `_meta` is served | `pytest tests/test_defhash.py::test_meta_not_hashed_not_served` | yes |
| `input_schema` on the wire fails the connect, and later a refresh | `pytest tests/test_defhash.py::test_snake_case_input_schema_fails_listing` | yes |
| An integer past 2^53-1 in a schema makes the tool unservable and unapprovable | `pytest tests/test_defhash.py::test_unhashable_definition_hidden` | yes |
| The fold gives every state the ledger decides | `pytest tests/test_pins.py::test_fold_gives_every_state` | yes |
| Pending tools are hidden, each seen once with one stored copy; approve exposes from the copy; reject needs a reason and hides | `pytest tests/test_pins.py -k "pending or approve_exposes or reject"` | yes |
| State folded from the file equals the gateway's; a restart exposes the same list and records nothing new | `pytest tests/test_pins.py -k "state_from_ledger or restart"` | yes |
| Drift is recorded once per pair and is sticky; the cap bounds a flip-flopping upstream to 16 observations and one cap entry | `pytest tests/test_pins.py -k "sticky or flip_flop"` | yes |
| A raw client in either era receives the stored copy, renamed, with no `execution` or `_meta` | `pytest tests/test_pins.py::test_served_copy_is_stored_copy` | yes |
| An altered or unwritable copy hides the tool; deleting the altered one restores it | `pytest tests/test_pins.py -k "tampered or unwritable"` | yes (the unwritable case on POSIX only) |
| Each hidden state is refused with its exact reason and line, with no upstream call and no side file | `pytest tests/test_pins.py::test_hidden_tool_called_by_name` | yes |
| A failed refresh hides the upstream and is recorded once; drift during it is caught; an unchanged tool comes back unapproved again | `pytest tests/test_pins.py::test_failed_refresh_recorded_not_unchanged` | yes |
| An upstream lost mid-call or while idle hides its tools and records `connection-lost` | `pytest tests/test_pins.py::test_lost_upstream_hides_tools` | yes |
| Every pin command row prints its golden bytes and exit code | `pytest tests/test_golden.py -k "pending or approve or reject or usage_pins"` | yes |
| A group binds to what was printed and never includes a decided tool; approve takes only what serve saw; head moves; pending writes nothing; forbidden paths refused | `pytest tests/test_pin_cli.py` | yes |
| approve and reject need a terminal unless given `--allow-no-terminal`, and run on a pseudo-terminal | `pytest tests/test_pin_cli.py -k terminal` | yes (the pseudo-terminal case on POSIX only) |
| The real CLI prints `pending`'s golden bytes with non-ASCII escaped | `pytest tests/test_pin_cli.py::test_pin_commands_subprocess` | yes |
| Startup fails closed on each broken status, including a head past a lost rejection; a stopped writer exposes nothing and tells the client | `pytest tests/test_pin_startup.py` | yes |
| `serve` with stdin closed finishes startup, records every tool and exits 0 | `pytest tests/test_pin_startup.py::test_prime_with_closed_stdin` | yes |
| Every M1a kind verifies in both verifiers, and the fixtures regenerate byte for byte | `pytest tests/test_conformance.py tests/test_fixtures.py` | yes |
| The pinned reference servers: 27 tools hashed, none with `_meta`, none unservable, largest schema integer 10, same hashes twice, served as stored | `POLARIZER_REFERENCE=1 uv run --locked pytest -s "tests/test_reference.py::test_reference_tools_hash_and_serve"` | yes, once, locally |
| The other reference tests, primed | `POLARIZER_REFERENCE=1 uv run --locked pytest tests/test_reference.py` | no (see Not run) |
| The whole M0 suite still passes, with the changes listed under Deviations | `scripts/test.sh` | yes |
| The new and changed tests pass repeatedly | five runs of the pin, proxy, era, listing, stdout, fidelity, outcome and startup tests | yes, 5 of 5 |
| All of the above on Windows and macOS | CI | no |
| Approving while an interactive session is open | docs/MANUAL-CHECK.md (stage 5) | no |
