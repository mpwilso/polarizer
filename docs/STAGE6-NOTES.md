# Stage 6 notes

Read after docs/STAGE5-NOTES.md. This file records what stage 6 (M2a, policy and holds) decided on its own: what step 0 found, the deviations, the guesses, the tests left for stage 7, the platform-dependent behaviors, the time bounds, the repeated runs, what could not be run, the Windows and macOS hazards, and a claims table. Where these notes and the specs differ, the specs win until a spec is changed.

The session began with the commit "spec: m2a decisions", which applied the owner's answers to HOLD-SPEC.md's questions and step 0's findings to HOLD-SPEC.md, LEDGER-SPEC.md, PROXY-SPEC.md and milestones.md. Then it built stage 6 in one commit, "stage6: policy and holds": `src/polarizer/paths.py` (resolution and patterns), `src/polarizer/policy.py` (the policy, its hash and the rule function), `src/polarizer/holds.py` (the hold fold and what `holds` prints), the conditional append in `writer.py`, the hold handling in `proxy.py`, `holds`, `allow`, `deny` and `--no-holds` in `cli.py`, and their tests. No change to the ledger format, the hash chain, a status or an exit code. The one fixed value stage 6 had to meet, the policy hash in `test_policy_loaded_fields`, matched the implementation on its first run.

## What step 0 found

Nothing needed the owner. Each is decided and recorded:

1. **HOLD-SPEC.md section 15 contradicted itself.** Stage 6 step 5 asked for every golden row except `pending --config`, but `holds_mixed.txt`, `holds_state_unknown.txt` and `allow_ended_session.txt` need the session-state probe and the warnings on `allow` and `deny`, which stage 7 step 1 listed. Decided: the read side of session state (the probe of a lock file, `<state>` in `holds`, the warnings) is built in stage 6; serve's own lock file, `hold.abandoned` at start and the restart tests stay in stage 7. Section 15 now says so (spec commit).
2. **Shutdown during a hold is stage 7,** and the owner's prompt leaves it out. Until then a held handler cancelled by shutdown records nothing: its hold stays open in the ledger, as if the process had been killed, and a stage 7 start will abandon it. Section 15's stop line says so.
3. **`--bell` and `--wait`.** The owner asked for a BEL "the first time each new open hold of a running session appears, then keep waiting as before", but `--wait` ends as soon as such a hold exists. Decided in the spec commit: one BEL per such hold found at that read, written before the listing, then exit 0 as `--wait` does (HOLD-SPEC.md section 8, deviation 27). Building it is stage 7.
4. **LEDGER-SPEC.md's fsync sentence and files table** were scheduled for stage 6 by HOLD-SPEC.md deviation 5. Both are updated in the stage 6 commit (`sessions/` is listed as read from stage 6 and created from stage 7).
5. **docs/PLAN.md** still does not exist; nothing relies on it.

## Deviations

1. **Session state is read in stage 6, never written.** `lock.probe` and `holds.session_state` read `sessions/<session>.lock`, but serve creates no lock file before stage 7. So every live session reads `state unknown` in `holds`, and every `allow` and `deny` prints `polarizer: warning: cannot tell whether the session that held this call is running`, with exit 0. The golden tests create and lock the files themselves.
2. **A held call cancelled by shutdown records nothing** (step 0, item 2). The handler re-raises when `gateway.shutting_down` is set.
3. **The refusal for a side file that fails its check after an allow** (the owner's decision, HOLD-SPEC.md section 6, Allow, step 3) has the reason `hold <id> was allowed, but its side file <problem>`, `<problem>` being one of `is missing`, `does not match its name`, `is unreadable` or `does not hold JSON arguments`, and serve writes one stderr line: `polarizer: refused <name>: hold <id>'s side file args/<commit>.bin <problem>`, with the operating system's message for an unreadable file, through `safe()`.
4. **serve's fsync before acting on an allow is the writer's catch-up,** as for an adopted `tool.approved`: when a catch-up adopts a `hold.decided` whose decision is `allow`, the writer fsyncs the ledger before handing it to the folds. If that fsync fails, the writer stops with `polarizer: stopped writing the ledger: could not fsync an allow another process wrote: <why>; run polarizer verify`, and the waiting handler answers "could not record it".
5. **The rule function runs on an anyio worker thread, shielded,** so a call the client cancels while its paths are resolved is still decided and recorded; the cancel then lands on the hold or on the upstream call. Each path is resolved on a daemon thread of its own, bounded by `Policy.resolve_bound` (2 s), and a path that takes longer is left to finish on that thread.
6. **`rig.prime()` gains no `policy=`.** The stdio tests' classification lives in the toml: `rig.serve_config()` adds a `[upstream.<p>.tools]` table to every upstream it writes (`rig.classify`), and `prime` runs serve on that file. `rig.gateway()` and `rig.proxied()` take `policy=` (default `rig.classified(specs)`) and `holds=`, as HOLD-SPEC.md section 11 says.
7. **Test hooks on `Gateway`:** `hold_timeout` (seconds, replacing the policy's wait; the ledger still records the policy's integer, since `timeout_seconds` must be an integer), `hold_watch_interval` (default 0.25 s) and `before_forward` (awaited with the hold id after an allow, so a test can hold the forward back).
8. **A hold ended by `hold.abandoned`** while its handler still waits (only a hand-written ledger can do that in stage 6) is refused with `hold <id> was abandoned`.
9. **More than 256 paths** in one argument is held with the reason `argument "<a>": more than 256 paths`; section 3 gave the rule but no text.
10. **`<resolved>` and `<a>` in a reason** go through `safe()` with a 1 KiB limit, not `safe()`'s default 200 characters, so a long path isn't cut before the reason's own 1 KiB cut.
11. **`holds` with a side file it can't read** (missing or unreadable) prints `args_commit <64 hex>` with no byte count, since there is no file to count. An altered or non-JSON file still gets its count.
12. **`allow` and `deny` probe the session once,** after the decision is written, and use that state both in the printed block and for the warning.
13. **`--no-holds` is checked before `--config` and `--ledger-dir`,** so `polarizer holds --no-holds` says `polarizer: --no-holds goes with serve`. Every subcommand accepts the flag only to refuse it.
14. **Config validation order:** `[policy]` is checked after the upstreams, so a file with errors in both reports the upstream's first.
15. **Tests beyond HOLD-SPEC.md section 13:** `test_error_text_from_the_file_is_safe`, `test_unclassified_line_once_after_listing` and `test_no_holds_from_the_command_line` (`test_policy_config.py`); `test_verdict_records_the_class` (`test_rules.py`); `test_ignored_decision_line`, `test_fold_gives_every_ending` (stage 6 step 3's done-when) and `test_allow_racing_timeout` as two tests, one per ordering (`test_holds.py`); `test_allow_runs_on_a_terminal` (the pseudo-terminal half of `test_allow_needs_terminal`) and `test_hold_decisions_need_a_ledger` (`test_hold_cli.py`); the three conditional append tests in `test_writer.py`; `test_example_config_policy` (`test_config.py`).
16. **Existing tests changed:** `test_startup.py::test_parallel_connect_with_timeout` expects `policy.loaded` right after `session.started`; `test_live_check.py::test_setup_writes_both_configs` and `test_rugpull_check.py::test_setup_configs_differ_only_in_phase` check the generated `[upstream.probe.tools]` table. Nothing else in the earlier suite changed in what it asserts.
17. **Test helpers:** `rig` gains `classify`, `classified`, `held`, `decide_hold`, `hold_ids` and `holds_created`, and `serve_config` classifies; `FakeUpstream` sets `server.fake` so `rig.classified` can find the names a fake declares; `tests/helpers/holdledger.py` builds the hold golden ledgers (`holds_mixed.txt` from one function per hold, each with a docstring); `tests/helpers/claim_worker.py` races the conditional append across processes.
18. **Conformance fixtures added:** `valid/holds` (every M2a kind and optional field), `broken/holds_edit_decision` (a deny edited into an allow, tampered at the line) and `broken/holds_rehashed_decision` (the same edit re-hashed, caught by `ledger.head`). Both verifiers agree on all three; the reference verifier is unchanged.
19. **`test_hold_commands_subprocess` compares the age lines by form.** The subprocess runs on the real clock, so its `waiting about` lines are matched with a pattern and every other byte compared with `holds_mixed.txt`.
20. **The scripts' tomls** (`live_check.py`, used by `live-check.sh`, and `rugpull_check.py`) classify the probe's seven tools `local-read` through `live_check.probe_classes()`. `scripts/m1a-check.sh` writes its toml from `polarizer.example.toml`, which now classifies them too.

## Guesses

1. **`~` in a pattern.** `"~" only as the first part, followed by "/"` is read as: a pattern starting with `~` must start `~/`, and no part may be exactly `~`. A `~` inside a part (`file.txt~`, `~$report.docx`) is a literal character.
2. **`~` in an anchored pattern expands to the resolved home directory** (`realpath`), because the paths it is compared with are resolved.
3. **The policy hash** takes patterns as configured, `~` unexpanded, and roots resolved, as the form's `workspace_roots` says.
4. **No unclassified line under `--no-holds`:** nothing is held, so "every call to them is held" would be false.
5. **A workspace root need only exist;** a file is accepted, as section 2 asks for nothing more.
6. **Resolution drops `.` parts** and repeated slashes anywhere, and treats `ENOTDIR` (a part below a regular file) as a part that does not exist.
7. **On Windows, an anchored pattern starting with `/`** matches the path's parts after its drive or UNC share, any drive.
8. **`<age>` is `?`** when a hold's `ts` doesn't parse, and `0m00s` when it is in the future (a clock step).
9. **The reason names the first matching pattern** in the built-in order, then the configured ones; `.claude/**` floats, so it is named rather than `~/.claude/**` for a path in the home directory.
10. **A fold of `hold.created`** ignores an entry whose fields have the wrong types, or a second `hold.created` with an id already seen; `hold.decided` with a decision other than `allow` or `deny` is no ending.

## Tests in HOLD-SPEC.md section 13 left for stage 7

- `test_hold_restart.py`, all of it: `test_restart_records_abandoned_holds`, `test_running_session_is_not_abandoned`, `test_unknown_session_is_not_abandoned`, `test_decision_on_ended_session_warns`, `test_esc_during_a_hold`, `test_kill_during_a_hold`, `test_holds_wait` and `test_holds_wait_bell`.
- `test_holds.py::test_progress_during_a_hold`.
- `test_holds.py::test_one_terminal_entry_per_call`: its shutdown row. The other six live endings run in stage 6.
- `test_hold_cli.py::test_pending_shows_classes`, and the golden rows `pending_classes.txt`, `pending_classes_only.txt`, `approve_one_with_class.txt` and `holds_wait_bell.txt`.
- `test_policy_config.py::test_example_config_matches_reference_servers` (Reference).

## Platform-dependent behavior

| Behavior | Where | Test | Windows | macOS |
|---|---|---|---|---|
| Resolution: own walker with `lstat` and `readlink` on POSIX; `os.path.realpath`, strict then not, on Windows | `paths.resolve` | `tests/test_paths.py` | the walker's tests run; the Windows branch has never run | runs (same walker); `/tmp` and `/var` resolve into `/private` |
| Symbolic links: escape, into a pattern, dangling, loop, through to Polarizer's files | `paths._resolve_posix` | `test_symlink_escape_is_held`, `test_symlink_into_a_pattern_is_held`, `test_dangling_link_resolves_to_target`, `test_link_loop_is_held`, `test_polarizer_files_are_held` | skipped where `os.symlink` raises; where it doesn't (an admin runner), the loop test depends on winerror 1921 (hazards) | runs |
| A directory with no permissions | `paths._resolve_posix` | `test_unreadable_part_is_held` | skipped (POSIX only) | runs; skipped as root |
| Windows names, drive-relative and root-relative paths | `paths._windows_name_problem` | `test_windows_names_are_held` | runs only there | skipped |
| Case rule for patterns: casefold and NFC on Windows and macOS, exact on Linux | `paths.FOLD`, `paths.matches` | `test_pattern_matching` (both rules everywhere), `test_case_rule_on_this_platform` | casefold | casefold |
| Roots compared like forbidden paths (`normcase`, then `samefile`) | `ledgerdir.is_inside` | `test_roots_compare_like_forbidden_paths` | a differently cased path is inside | inside on a case-insensitive volume |
| Built-in PowerShell profile patterns | `paths.BUILTIN_WRITE` | `test_builtin_patterns_cannot_be_removed` (the platform's own list) | included | not included |
| Session probe: `flock(LOCK_SH)` on POSIX, `msvcrt.locking(LK_NBRLCK)` on Windows | `lock.probe` | the hold golden tests, `test_hold_commands_subprocess` | runs in CI; never run here | runs |
| Allow on a pseudo-terminal | `cli._stdin_is_terminal` | `test_allow_runs_on_a_terminal` | skipped (no `pty`) | runs |
| Paths in reasons use the platform's separator; `args/<commit>.bin` always "/" | `policy`, `holds.block` | `test_polarizer_files_are_held` (`os.sep`), the golden files | runs | runs |

## Time bounds in the new tests

Lower bounds can't fail under load. Each upper bound is at least 5 times what the operation needs.

| Test | Bound | The operation needs |
|---|---|---|
| `rig.holds_created`, `Calls.result` and `rig.until` (most waits in the hold tests) | 10 s, 10 s and 5 s | milliseconds to about 0.1 s (watch every 0.05 s) |
| `test_hold_durability.py::test_allow_from_second_process` | forwarded within 2 s of the command returning | about 0.25 s (the default watch while a hold is open) |
| `test_holds.py::test_held_call_does_not_reach_upstream_until_allowed` | nothing forwarded in 0.5 s (a window, not a bound: a late forward would fail it, not pass it) | none expected |
| `test_holds.py::test_allow_racing_timeout_*` | the held fsync or write released after 1.0 s or 0.5 s; each wait 30 s | the 0.5 s timeout; milliseconds |
| `test_paths.py::test_slow_resolution_is_held` | ends in under 1.0 s, with a 0.05 s bound and a resolver that sleeps 1.0 s | 0.05 s |
| `test_writer.py::test_conditional_append_two_processes_race` | the workers open within 60 s, each ends within 120 s | about 1 s |
| `test_hold_cli.py` subprocesses | 60 s each | under 2 s |

## Repeated runs

The new test files with subprocess, race or timing tests (`test_holds.py`, `test_hold_durability.py`, `test_hold_cli.py`, `test_policy_config.py`, `test_paths.py` and `test_writer.py`, 106 tests), run from a script in the session's scratch directory:
- **Five times in a row:** 105 passed and 1 skipped (the Windows names test) each time, exit 0, in 23.7 to 35.0 s.
- **Once under load,** while two busy-loop Python processes ran, started by the script and stopped by their own pids: 105 passed, 1 skipped, exit 0, in 24.1 s. Both pids were gone afterwards. The machine has 24 logical CPUs, so two busy loops are a light load.
- **Leftovers:** no probe, claim worker, `serve` or `allow` process was running after the series.
- An earlier attempt at the series was stopped after one run, because the script's output dropped pytest's summary line (it passed `-q` on top of the project's own `-q`); it was rerun with each run's exit code and summary recorded, as above.

The code changed twice after the series, without changing behavior on Linux: `paths._resolve_windows` gained its strict first call (Windows only), and `_await_ending` was tidied. The final `scripts/test.sh` ran on the final code.

## Not run

- **CI,** so nothing from stage 6 has run on Windows or macOS, or on Python 3.11 or 3.13. Only Linux (WSL2, Python 3.12.3) ran it.
- **Claude Code, any model, and an interactive session.** Nothing in this session ran `claude`. How Claude Code shows a call that waits for a person, whether the model retries a call that was not allowed, and the manual check's M2a section are stage 7 and the owner's.
- **`scripts/live-check.sh` and `scripts/rugpull-check.sh` with real Claude Code.** Their tomls changed (the probe's tools classified), and their tests ran with the stub; a real run is the owner's.
- **The reference tests** (`POLARIZER_REFERENCE=1`). `rig.serve_config` now classifies their tools as the example config does; not run.
- **Windows resolution** (`paths._resolve_windows`) and the Windows lock probe: never run anywhere.

## Windows and macOS hazards in the new code

- **Symbolic link loops on Windows.** Reasoned, not run: `ntpath.realpath` without `strict` walks past a part it can't follow (`ERROR_CANT_RESOLVE_FILENAME`, 1921), which would hide a loop. Polarizer calls it with `strict=True` first and maps winerror 1921 to `too many symbolic links`. If Windows reports a loop below a missing part as "file not found", the non-strict call runs and the loop is not reported; `test_link_loop_is_held` would then fail on a runner that allows `os.symlink` (GitHub's Windows runners run as administrators, so it may).
- **The Windows lock probe** uses `msvcrt.locking(fd, LK_NBRLCK, 1)` on a read-only handle and treats `EACCES` or `EDEADLOCK` as "running". If Windows refuses a lock on a read-only handle with another error, every session reads `state unknown`, and the golden files `holds_mixed.txt` and `allow_one.txt` would differ there.
- **Resolution on Windows is not this file's walker.** `os.path.realpath` resolves `..` lexically, as Win32 does; Polarizer checks for a `..` after a part that does not exist itself, with `os.lstat`, before calling it.
- **macOS case.** On the default case-insensitive volume, a path whose case differs from the root's is inside it through `samefile`, and pattern matching casefolds; on a case-sensitive volume, patterns still casefold (holding more, the safe direction), and roots compare exactly.
- **Temporary paths** on macOS start `/private/var/...` once resolved; the tests compare resolved paths only.
- **The pseudo-terminal test** is POSIX only; Windows has no `pty`.

## Found, not changed

- **`verify --args` reports the side files of holds that were never forwarded as orphaned.** LEDGER-SPEC.md Part 3 counts only `call.sent` as a reference, and a denied, expired or abandoned hold's side file has only its `hold.created`. Orphans don't fail the check (exit 0), and the file is the arguments a person saw, so nothing is lost; but "orphaned" then reads as "a crash left this" when it means "held and never forwarded". Counting `hold.created` as a reference too would change `verify --args`' lines for ledgers with holds, though no status or exit code. Left for the owner.

## Claims

"Yes" means run locally on Linux (WSL2, Python 3.12.3, mcp 2.2.0), in the final run of `scripts/test.sh` unless the row says otherwise.

| Claim | Command | Run? |
|---|---|---|
| Each of section 2's error lines comes out exactly, exit 2, and serve starts no upstream; `holds = false` is an unknown key at either level | `pytest tests/test_policy_config.py::test_policy_errors` | yes |
| Text from the file in those lines (a key, a tool name, a pattern) is escaped | `pytest tests/test_policy_config.py::test_error_text_from_the_file_is_safe` | yes |
| With no `[policy]` and no `tools`, every tool is unclassified and its calls held; the `<n> of <m>` line is written once, after startup's listing, and not under `--no-holds` | `pytest tests/test_policy_config.py -k "no_policy_table or unclassified_line"` | yes |
| `--no-holds` records `policy.loaded` with `holds` `"off"`, fsynced before any upstream connects, every call runs with `allowed_by` `holds-off`, and serve warns on stderr | `pytest tests/test_policy_config.py -k no_holds` | yes |
| The fixed config's policy hash equals `bcd888f4...7006`, computed beforehand from a hand-written form with the standard library; `policy.loaded` follows `session.started` with the resolved roots, the count and the timeout | `pytest tests/test_policy_config.py::test_policy_loaded_fields` | yes |
| A missing workspace root stops serve with its line; `holds`, `pending` and `verify` with `--config` don't check it | `pytest tests/test_policy_config.py::test_roots_must_exist_for_serve_only` | yes |
| Symlink escape, `..` escape, `..` after a missing part, a link into a pattern, a dangling link, a loop, an unreadable part, relative, `~` and NUL paths, a slow resolver, non-string and missing arguments, and more than 256 paths are each held or allowed as section 3 says | `pytest tests/test_paths.py` | yes, Linux (the Windows names test skipped) |
| Patterns match as specified with both case rules; on this platform `.GIT/hooks` runs (Linux) | `pytest tests/test_paths.py -k "pattern_matching or case_rule"` | yes, Linux only |
| Roots compare like forbidden paths; Polarizer's ledger and config are held, also through a link | `pytest tests/test_paths.py -k "roots_compare or polarizer_files"` | yes, Linux |
| Windows names are held; `C:a` and `\a` are not absolute | `pytest tests/test_paths.py::test_windows_names_are_held` | no: Windows only |
| Every row of section 4's table gives its action, rule and reason exactly; first match wins by config order; every built-in pattern holds and can't be removed; annotations suggest classes and contradictions as section 4 says; a trusted upstream's annotations classify, a configured class wins | `pytest tests/test_rules.py` | yes |
| Every M2a kind and optional field verifies in both verifiers; an edited decision is caught by the chain and, re-hashed, by `ledger.head`; the fixtures regenerate byte for byte | `pytest tests/test_fixtures.py tests/test_conformance.py` | yes |
| The conditional append writes nothing when its check refuses, sees what another process wrote, and lets exactly one of two or four racing processes write | `pytest tests/test_writer.py -k conditional` | yes |
| The hold fold: the first ending wins; a decision with another `args_commit`, for an unknown hold, or after the ending changes nothing | `pytest tests/test_holds.py::test_fold_gives_every_ending` | yes |
| A held call reaches the upstream only after an allow, with exactly the side file's arguments; deny, timeout, the client's cancel, a cancel after the allow, a re-route refusal and a side file that fails its check each end it with the recorded entries and the one model line | `pytest tests/test_holds.py` | yes |
| A decision is bound to its hold's arguments and used once; a decision for another session's hold changes nothing in this one; holds decided out of order each end once | `pytest tests/test_holds.py -k "bound or single_use or another_session or out_of_order or ignored"` | yes |
| Allow and timeout racing, both ways round: exactly one ending, and serve acts on the first in the ledger | `pytest tests/test_holds.py -k racing` | yes |
| A writer that stops ends every waiting hold with the "could not record it" line and writes nothing more; a 17th hold is refused at once with no side file | `pytest tests/test_holds.py -k "writer_stop or too_many or model_line"` | yes |
| Every live ending but shutdown leaves one ending per hold and one terminal entry per held call, and the fold of the file equals the gateway's | `pytest tests/test_holds.py -k "terminal or matches_live"` | yes (shutdown is stage 7) |
| `call.sent` records `allowed_by` for each allow rule and `"hold"` after an allow | `pytest tests/test_holds.py -k "allowed_by or forwarded"` | yes |
| An allow is durable before serve forwards on it: the deciding writer's held fsync keeps the call back; serve fsyncs the ledger itself first, and stops if that fails; `python -m polarizer allow` from a second process forwards within 2 s; `ledger.head` names the decision; `holds` changes nothing | `pytest tests/test_hold_durability.py` | yes |
| Every `holds`, `allow`, `deny` and usage row of section 8's golden table, except `holds_wait_bell.txt` and the `pending --config` rows (stage 7), prints its exact output and exit code | `pytest tests/test_golden.py -k "holds or allow or deny or usage_holds"` | yes |
| `holds` escapes ledger text; `allow` and `deny` need a terminal or the flag and run on a pseudo-terminal; they refuse a forbidden `ledger_dir` and a missing ledger; two at once write one decision; the real CLI prints `holds_mixed.txt`'s bytes | `pytest tests/test_hold_cli.py` | yes (the pseudo-terminal case POSIX only) |
| The example config classifies the probe and the Filesystem server so the manual check holds `fs__write_file` outside its directory and `fs__move_file` always | `pytest tests/test_config.py::test_example_config_policy` | yes |
| live-check's and rugpull-check's tomls classify the probe's seven tools | `pytest tests/test_live_check.py tests/test_rugpull_check.py -k setup` | yes |
| The new subprocess, race and timing tests pass five times in a row and once under load, leaving no process running | the loop in Repeated runs | yes |
| The whole suite, ruff and the docs check | `scripts/test.sh` | yes: see the summary below the table |
| All of the above on Windows and macOS, and on Python 3.11 and 3.13 | CI | no |
| The reference servers through the classified toml | `POLARIZER_REFERENCE=1 uv run --locked pytest tests/test_reference.py` | no |
| live-check and rugpull-check with Claude Code | `scripts/live-check.sh`, `scripts/rugpull-check.sh` | no: for the owner |
| The pre-push scan finds none of the owner's private strings | `git grep --cached -i -c` per string, and the commit's message, author and committer | yes: every count 0 |

The final `scripts/test.sh` on the committed code: ruff (lint and format) clean, the docs check 17 files with 0 findings, and pytest 739 passed, 8 skipped (stage 1's 4 platform skips, the 3 reference tests, and the Windows names test) in 187.03 s; 3 min 11 s in all.
