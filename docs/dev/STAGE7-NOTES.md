# Stage 7 notes

Read after docs/dev/STAGE6-NOTES.md. This file records what stage 7 (M2a, lifetime, progress and visibility) decided on its own: what step 0 found, the Windows skip audit, the deviations, the guesses, the time bounds, the repeated runs, what could not be run, the Windows and macOS hazards, and a claims table. Where these notes and the specs differ, the specs win until a spec is changed; the decisions that change behavior are also folded into docs/HOLD-SPEC.md (section 17, Stage 7).

Stage 7 is one commit, "stage7: lifetime, progress and visibility". It builds docs/HOLD-SPEC.md section 15's stage 7 list: serve's own session lock and `hold.abandoned` at start (`proxy.py`, `lock.py`); shutdown during holds and progress while held (`proxy.py`); `holds --wait` and `--bell` (`cli.py`, `holds.py`); classes in `pending` and `approve` with `--config` (`policy.py`, `pins.py`, `decisions.py`, `cli.py`); the reference check of the example config, which found `read_file` missing; and `scripts/hold-check.sh`, the M2a manual check. No change to the ledger format, the hash chain, a status or an exit code.

## What step 0 found

The tree was clean at e97bfba, `scripts/test.sh` passed (743 passed, 8 skipped, 3 min 13 s) and the guard snapshot was taken. Nothing needed the owner: none of these changes the ledger format, a status or an exit code. Each is decided and recorded:

1. **Progress values and the test's injected interval.** Section 6 says the progress value is the whole seconds waited, and `test_progress_during_a_hold` injects an interval of 0.05 s and asks for increasing progress. At that interval whole seconds would be 0, 0, 0. MCP asks that each progress value be higher than the last. Decided: a value is the whole seconds waited, or the one before plus one if that is higher. At the 10 s default that is always the whole seconds waited (deviation 1).
2. **Shutdown after an allow, before the forward,** had no row in section 5's table and no reason. The client's cancel in the same place has `the client cancelled the call before it was forwarded`. Decided: `polarizer shut down before the call was forwarded` (deviation 2).
3. **`hold.abandoned` was not a conditional append,** while section 5 says each held call gets at most one terminal entry and section 7 says a decided hold gets no `hold.abandoned`. Two starts at once, or an allow racing a start, could otherwise give a hold two endings. Decided: conditional, like `hold.expired` (deviation 3).
4. **`pending --config` and a block whose stored copy fails.** The class line's suggestion comes from the stored copy's annotations; such a block has none to read. Decided: no class line for it (guess 6).
5. **The classes section's "live hash".** `pending` reads only the ledger and `defs/`, so it can't know a live hash. Decided: a tool whose latest decision is an approval with no drift since (guess 7).
6. **`holds --wait` with no ledger,** and the order of the `--bell` usage check, are not specified. Decided: `no ledger at <dir>`, exit 2, as `holds` (no waiting for a ledger to appear), and `--bell` without `--wait` is checked right after `--no-holds`, before `--config` and `--ledger-dir` (deviations 6 and 7).
7. **Where the stdio half of `test_progress_during_a_hold` goes.** Section 13 lists one in-memory test "in memory and over stdio". Decided: the stdio half is `test_hold_restart.py::test_progress_during_a_hold_over_stdio`, beside the other stdio tests, and serve's interval is replaced there by `tests/helpers/fast_serve.py`, which sets the module constant before the gateway is made: no environment variable or flag in the shipped code.
8. **docs/PLAN.md** still does not exist; nothing relies on it.

Found while building, and fixed: **`scripts/m1a_check.py` broke on `pending --config`.** Its parser read the new class line as a block's note, and the classes section as a block, so `m1a-check.sh approve` refused the group (two failures in `tests/test_m1a_check.py`). `--ledger-dir` output is unchanged, as the spec asks, but the superseded script calls `pending --config`. The parser now reads the class line separately and stops at the classes section.

## The Windows skip audit

CI on Windows skipped 49 tests at e97bfba, against 8 on Linux. Nothing here ran on Windows. The list below comes from collecting every test's skip markers on Linux (a throwaway pytest plugin in the session scratch directory that printed each `skipif` marker and its reason) and reading every `pytest.skip` call in the tests.

The count adds up only one way. In the table below, 36 tests skip by a marker that applies on Windows, and 13 by a `pytest.skip` call that always fires there (the 12 read-only `verify` cases and the symlink half of one `ledgerdir` test): 36 + 13 = 49. So the five symbolic link tests of `test_paths.py`, which skip only where `os.symlink` raises, did not skip: GitHub's Windows runner allowed `os.symlink`, and `test_link_loop_is_held` passed there. That is inferred from the counts, not seen in a log.

| Group and reason | Tests (count) | Matters on Windows? | Held-path or hold-ending rule left untested on Windows? |
|---|---|---|---|
| POSIX file modes: "POSIX modes; Windows uses profile ACLs", "POSIX modes", "Windows has no read-only directories in this sense", "POSIX permissions" | `test_defhash.py::test_copy_modes`, `test_writer.py::test_modes`, `test_pins.py::test_unwritable_stored_copy_hides`, `test_verify_readonly.py::test_verify_changes_nothing` with `read_only=True` (12 cases) (15) | Modes don't apply; the profile's ACLs protect `ledger_dir` (docs/LEDGER-SPEC.md). Not about holds. | No |
| The scripts need bash (and pgrep, a pty): "needs bash; as root ...", "the script needs bash, pgrep and a pty ...", "the script needs bash and pgrep ...", "live-check.sh is POSIX only ..." | `test_guard.py` (7), `test_m1a_check.py` (3), `test_rugpull_check.py` (7), `test_live_check.py::test_prime_exposes_the_probe_tool` (1) (18) | Development scripts, not the tool. Stage 7 adds `test_hold_check.py`'s 2 script tests to this group. | No: the scripts only drive the tool |
| No pty module: "Windows has no pty module" | `test_pin_cli.py::test_approve_runs_on_a_terminal`, `test_hold_cli.py::test_allow_runs_on_a_terminal` (2) | Yes, a little: that `allow` runs from a real console without `--allow-no-terminal` is untested there. The refusal without a terminal, and `allow` with the flag, run on Windows. | No: `allow` and `deny` decide holds on Windows in `test_hold_durability.py` and the golden tests |
| Signals: "Windows has no way to send SIGINT and SIGTERM to a child the way Claude Code does ..." | `test_shutdown.py`: SIGINT then SIGTERM, SIGTERM alone, kill then restart, a signal during startup, an upstream ignoring end of input (5) | Yes: on Windows only Ctrl+C and end of input reach serve. Stage 7 adds `test_esc_during_a_hold`, `test_kill_during_a_hold` and `test_holds_wait_ends_on_ctrl_c` to this group. | Shutdown during a hold had no test anywhere before stage 7. Now `test_stdin_closed_during_a_hold` (end of input, the same shutdown path) runs on Windows, and so do the in-memory shutdown row of `test_one_terminal_entry_per_call` and `test_killed_process_releases_its_session_lock` |
| Symbolic links and permissions in paths: "symlinks need privileges on Windows", "POSIX permissions and paths" | `test_ledgerdir.py::test_dot_dot_and_symlinks_are_resolved` (its `..` half runs first, then it skips), `test_repair.py::test_repair_does_not_follow_a_side_file_link`, `test_paths.py::test_unreadable_part_is_held` (3) | Yes: path resolution decides holds. | The symlink path rules ran on Windows (the inference above), but only because the runner allows `os.symlink`; on a runner that refuses it, nothing would. Replaced, below. A part that can't be examined (permission denied) still has no Windows test: Windows permissions work differently, and nothing simple makes a directory unsearchable there |
| Another platform's case rule: "Linux paths are case-sensitive", "macOS: depends on the volume" | `test_ledgerdir.py` (3) | Windows has its own case test, which runs there. | No |
| Reference servers: "reference servers run only with POLARIZER_REFERENCE=1" | `test_reference.py` (3) | Local only, on every platform. Stage 7 adds `test_example_config_matches_reference_servers`. | No |

**What stage 7 added so the path rules that hold calls are tested on the Windows runner** whether or not it allows `os.symlink`:
- `test_paths.py::test_link_targets_decide_with_an_injected_resolver` (every platform): a resolver that stands in for the file system's links, so a write that escapes the root through a link, lands in `.git/hooks` or in the ledger directory, stays inside, or meets a loop, is judged by its resolved path.
- `test_paths.py::test_junction_escape_is_held`, `test_junction_into_a_pattern_is_held` and `test_junction_to_polarizer_files_is_held` (Windows only): directory junctions, which need no privilege, made with `_winapi.CreateJunction` as CPython's own tests do.
- `test_ledgerdir.py::test_junction_into_a_forbidden_path_is_refused` (Windows only): the stand-in for the skipped symlink half of the forbidden-path test.

No skip was removed. `test_repair.py`'s side-file link test has no junction stand-in: a junction links directories, and the side file is a file.

## Deviations

1. **Progress values always increase** (step 0, item 1): the whole seconds waited, or the one before plus one.
2. **Shutdown after an allow, before forwarding** records `call.refused` with `polarizer shut down before the call was forwarded` (step 0, item 2). The model gets nothing, as for any cancelled request.
3. **`hold.abandoned` goes through the conditional append** (step 0, item 3). `test_abandonment_is_conditional` has another process allow the hold after the start folded the ledger and before it appends: nothing is written.
4. **Every gateway takes a session lock,** in memory too, in `Gateway.start` before `session.started`, and an in-memory gateway releases it when its `running()` block ends (`Gateway.release_session_lock`). serve never releases it: the process's end does. The directory is created with 0700 and chmod'ed, as `ledger_dir` is.
5. **The lock-failure warning** prints the path through `safe()` with a 1 KiB limit, not `safe()`'s default 200 characters, so a long temporary path isn't cut; the operating system's message goes through `safe()` too.
6. **`holds --wait` reads again when the ledger's size differs** from what it read, grown or shrunk (a repair), not only when it grew.
7. **`--bell` without `--wait`** is refused right after `--no-holds`, before the `--config` and `--ledger-dir` checks.
8. **`holds --wait` sets SIGINT to its default action** only when it runs on the main thread (always, for the command), and puts the previous handler back when it returns, which only an in-process caller (a test) can see.
9. **Without `--wait`, `holds` probes each session once,** not once per hold; the output is unchanged.
10. **The reference check also checks `path_args`**: each name the example gives is a property of that tool's input schema.
11. **The example config gains `read_file`** (`local-read`, `path_args = ["path"]`), the server's deprecated name for `read_text_file`, with the same arguments and annotations `readOnlyHint` true, `openWorldHint` false. `tests/helpers/rig.py`'s `FILESYSTEM_TOOLS` follows the example, and `test_config.py::test_example_config_policy` now counts 14 Filesystem entries, not 13: the example's count, changed on purpose, not a fixed value fitted to the code.
12. **The M2a manual check is a script with subcommands:** `reset`, `deny`, `allow`, `long-wait`, `expire`, `finish` and `status`. The step that lets a hold time out is called `expire`, because `tests/test_scripts_portable.py` reads the word `timeout` as the GNU command macOS lacks. `reset` moves an earlier check's ledger aside (`<ledger>.before-<UTC time>`) instead of deleting it, and the files Claude is asked to write carry the first 8 hex characters of the fresh ledger's chain id, so each run's files are new and nothing needs deleting.
13. **`scripts/hold_check.py` uses Polarizer's own hold fold and session probe** (it runs on the repo's `.venv`), unlike `m1a_check.py`, which is stdlib only; its `group` reuses `m1a_check.parse`.
14. **Tests beyond docs/HOLD-SPEC.md section 13:** in `test_hold_restart.py`, `test_serve_holds_its_session_lock`, `test_session_lock_failure_warns`, `test_killed_process_releases_its_session_lock`, `test_abandonment_is_conditional`, `test_stdin_closed_during_a_hold`, `test_progress_during_a_hold_over_stdio` and `test_holds_wait_ends_on_ctrl_c`; the path tests of the skip audit; `tests/test_hold_check.py` (two script tests and one parsing test). docs/HOLD-SPEC.md section 13 now lists the two that matter most for Windows.
15. **Test helpers:** `tests/helpers/fast_serve.py` (serve with a shorter progress interval), `tests/helpers/fs_stub.py` (a stdlib stand-in for the Filesystem server's `write_file` and `move_file`, for the hold check's test), and `pinledger.classes`, `config_for`, `CLASSES_CONFIG` and `PLAIN_CONFIG` for the class golden files, with three annotated tools added to `pinledger.TOOLS` (the existing ones and their hashes are unchanged).
16. **`holds_wait_bell.txt` is `holds_mixed.txt` after two BEL bytes,** built from the existing, reviewed golden file, as section 8's table describes it; `test_holds_wait_bell` checks that relation as well as the command's bytes.

## Guesses

1. **`_winapi.CreateJunction(target, link)`**: the argument order is from memory of CPython's tests. Not run here.
2. **TerminateProcess releases a `msvcrt.locking` lock within 10 s**: docs/HOLD-SPEC.md section 7 says "not necessarily at once"; the test allows 10 s. Not run on Windows.
3. **`os.fstat` on Windows reports a file's current size** while another handle appends to it, which `holds --wait` relies on. Not run there.
4. **A start probes each other session once** and uses that state for all of its open holds.
5. **The class line of a trusted upstream's tool** is the spec's form applied literally: `class <x> (from annotations); annotations suggest <x>`, with the same class twice.
6. **No class line for a block whose stored copy fails its check** (step 0, item 4).
7. **The classes section** lists only tools of upstreams in the given config (a prefix no longer configured can't be called, so "every call is held" would not be true of it), whose latest decision is an approval with no drift since, and whose approved copy passes its check; `--upstream` limits it as it limits the blocks.
8. **What the hold check asks Claude to do** ("Call the polarizer tool fs__write_file ...") is a guess at a request Claude will route to the polarizer tool rather than its own file tools; the manual check says what to do if it doesn't.

## Time bounds in the new tests

Lower bounds can't fail under load. Each upper bound is at least 5 times what the operation needs.

| Test | Bound | The operation needs |
|---|---|---|
| `test_hold_restart.py::test_holds_wait` | the listing within 2 s of the hold appearing | the 0.25 s poll and one read, about 0.3 s |
| `test_hold_restart.py::test_holds_wait` and `test_holds_wait_bell` | each wait for a read or a poll 30 s | milliseconds |
| `test_hold_restart.py::test_killed_process_releases_its_session_lock` | ended within 10 s of the kill | at once on Linux |
| `test_hold_restart.py::test_progress_during_a_hold_over_stdio` | three notifications within 10 s; nothing in 0.5 s (a window, not a bound) | about 0.15 s |
| `test_holds.py::test_progress_during_a_hold` | three notifications within 10 s; nothing in 0.5 s (a window) | about 0.15 s |
| `test_hold_restart.py`'s stdio tests | each hold within 30 s, serve's exit 30 s | under 1 s |
| `test_hold_restart.py::test_holds_wait_ends_on_ctrl_c` | each disposition change 30 s, the exit 30 s | under 1 s |
| `test_hold_check.py` | each script step 60 s; each answer from serve 30 s | under 5 s for `reset`, which primes; about 2 s for the others |

## Repeated runs

The test files with new subprocess, signal, lock or timing tests (`test_hold_restart.py`, `test_hold_check.py`, `test_holds.py`, `test_hold_cli.py`, `test_m1a_check.py`, `test_paths.py` and `test_ledgerdir.py`), run from a script in the session scratch directory, on the final code:

- **Five times in a row:** 111 passed and 8 skipped each time, exit 0, in 54.8 to 57.4 s. The 8 skips are the tests that run only on Windows or macOS: `test_windows_names_are_held`, the three junction tests of `test_paths.py`, and in `test_ledgerdir.py` the junction test, the two Windows case tests and the macOS one.
- **Once under load,** while two busy-loop Python processes ran, started by the script and stopped by their own pids: 111 passed, 8 skipped, exit 0, in 55.1 s. Both pids were gone afterwards. The machine has 24 logical CPUs, so two busy loops are a light load.
- **Leftovers:** no `polarizer serve`, probe, Filesystem stand-in or claim worker was running after the series.
- **An earlier series,** while the code was still changing (`holds --wait`'s SIGINT handler was made to be put back, and `test_holds_wait_ends_on_ctrl_c` was added, during it), also passed every run: 110 or 111 passed, 8 skipped. The series above was run again on the final code.
- **The series script first wrote its scratch output to `run.txt` in the repo's root,** because it ran from there; the file was removed by its path before the commit, and the script now writes in the scratch directory.

## Not run

- **CI,** during the stage: only Linux (WSL2, Python 3.12.3) ran it. CI ran once the stage was committed, and passed at c986088 on all five jobs (Follow-up: CI, below).
- **Claude Code, any model, and an interactive session.** Nothing in this session ran `claude`. The M2a manual check (`scripts/hold-check.sh`) ran only against its stand-ins in `tests/test_hold_check.py`; its real run, with interactive Claude Code, the pinned Filesystem server and a person, is the owner's. The owner ran it on Oct 5, 2026 (Follow-up: the owner's M2a check, below).
- **`scripts/live-check.sh` and `scripts/rugpull-check.sh` with real Claude Code.** Unchanged in stage 7; their tests ran with the stub.
- **Esc during a hold with real Claude Code.** Only the two signals Claude Code was seen to send, from a test.

## Windows and macOS hazards in the new code

- **The session lock on Windows** is `msvcrt.locking` on byte 0 of an empty file, taken with `LK_NBLCK`, as the ledger lock is; the probe from stage 6 uses `LK_NBRLCK` on a read-only handle. The golden tests showed the probe works on the Windows runner. Taking the lock in `take_session`, and its release when a process is killed, have not run there.
- **Junctions** are made with `_winapi`, a private CPython module that exists only on Windows.
- **`holds --wait` on Windows:** SIGINT's default action ends the process; Ctrl+C in a console reaches it as SIGINT. Untested there (`test_holds_wait_ends_on_ctrl_c` is POSIX only).
- **The in-memory tests** of restarts close one gateway's writer and release its lock in the same process, then start another gateway on the same ledger; two writers in one process were already run on Windows by stage 6's `test_decision_for_another_session_is_ignored`.
- **macOS:** `scripts/hold-check.sh` keeps to bash 3.2 and the BSD tools (`tests/test_scripts_portable.py` checks it as text); its test runs on macOS's CI job like `test_m1a_check.py`'s. `test_holds_wait_ends_on_ctrl_c` has no `/proc` there and waits 5 s instead.

## Claims

"Yes" means run locally on Linux (WSL2, Python 3.12.3, mcp 2.2.0), in the final run of `scripts/test.sh` unless the row says otherwise.

| Claim | Command | Run? |
|---|---|---|
| serve creates `sessions/<session>.lock` exclusively (0600, in a 0700 directory) before `session.started` and holds it while it runs; a failure to create it is one warning line, and that session's holds read unknown and are never abandoned | `pytest tests/test_hold_restart.py -k "session_lock"` | yes |
| A process killed outright leaves its session lock free | `pytest tests/test_hold_restart.py::test_killed_process_releases_its_session_lock` | yes, Linux only |
| A later start records one `hold.abandoned` per open hold of an ended session, naming `held_by`, and none on a third start; a running session's hold is never abandoned and can still be allowed; an unknown session's isn't, and `holds` shows `state unknown`; a decision on an ended session warns and leaves nothing to abandon; abandonment is conditional | `pytest tests/test_hold_restart.py -k "restart or running or unknown or ended or conditional"` | yes |
| Esc (SIGINT, then SIGTERM 100 ms later) and end of input while a call is held: serve exits 0, the hold ends with `hold.expired` (shutdown) and one `call.refused`, the upstream sees nothing, and a new serve abandons nothing | `pytest tests/test_hold_restart.py -k "esc or stdin_closed"` | yes (Esc POSIX only) |
| SIGKILL while a call is held: intact ledger, open hold, no terminal entry; `holds` shows the session ended; a new serve records `hold.abandoned`, the one terminal entry | `pytest tests/test_hold_restart.py::test_kill_during_a_hold` | yes, POSIX only |
| Every ending a live process can produce, shutdown included, leaves one ending per hold and one terminal entry per held call, and the fold of the file equals the gateway's | `pytest tests/test_holds.py -k "terminal or matches_live"` | yes |
| While held, a client that asked for progress gets increasing whole-number progress with no total and no message, at the injected interval; one that asked for none gets none; in memory and over stdio | `pytest tests/test_holds.py::test_progress_during_a_hold tests/test_hold_restart.py::test_progress_during_a_hold_over_stdio` | yes |
| `holds --wait` keeps waiting on a dead session's hold and lists within 2 s of a running session's; `--bell` writes one BEL per open hold of a running session, byte for byte as `holds_wait_bell.txt`; without it none; `--bell` without `--wait` is the usage line; Ctrl+C ends it with no traceback | `pytest tests/test_hold_restart.py -k wait` | yes (Ctrl+C POSIX only) |
| `pending --config` and `approve --config` print the class lines and the classes section, byte for byte as the three golden files; `--ledger-dir` output is unchanged, and is the `--config` output without those lines | `pytest tests/test_golden.py -k "classes or with_class" tests/test_hold_cli.py::test_pending_shows_classes` | yes |
| Every earlier golden file is unchanged | `git diff --stat e97bfba -- tests/golden` (only added files) | yes |
| The example config names exactly the 14 tools the pinned Filesystem server lists, with path arguments its schemas have | `POLARIZER_REFERENCE=1 uv run --locked pytest -s tests/test_policy_config.py::test_example_config_matches_reference_servers`, offline (docs/verified-facts.md, Stage 7) | yes, once, locally |
| The three reference tests pass with the classified toml | `POLARIZER_REFERENCE=1 uv run --locked pytest tests/test_reference.py`, offline | yes, once, locally |
| Path rules follow resolved paths through stand-in links on every platform, and through junctions on Windows | `pytest tests/test_paths.py -k "injected or junction" tests/test_ledgerdir.py -k junction` | yes for the injected resolver; no for the junctions (Windows only) |
| `scripts/hold-check.sh` runs every step end to end against a stand-in for Claude Code and the Filesystem server, stops on a wrong hold, refuses its overrides without testing mode, and moves an earlier ledger aside | `pytest tests/test_hold_check.py` | yes, POSIX only |
| The M1a check's parser reads `pending --config`'s new lines | `pytest tests/test_m1a_check.py tests/test_hold_check.py::test_group_reads_a_fresh_first_run` | yes |
| The new subprocess, signal and lock tests pass five times in a row and once under load, leaving no process running | the script in Repeated runs | yes |
| The whole suite, ruff and the docs check | `scripts/test.sh` | yes: see the summary below |
| The suite on Windows and macOS, and on Python 3.11 and 3.13 (not the reference tests, which need `POLARIZER_REFERENCE=1`) | CI | yes, after the stage: passed at c986088 on all five jobs |
| The M2a manual check with interactive Claude Code: a held write denied and not created; the same call asked again a new hold with a new id and `args_commit`, allowed, `call.sent` with `allowed_by` `hold`, `call.returned` `ok`; a call held 128 s, then allowed, `ok`; a destructive call expired after 30 s and not moved; ledger intact, 71 entries | `scripts/hold-check.sh` (docs/dev/MANUAL-CHECK.md, M2a; docs/verified-facts.md, M2a check, interactive) | yes, once, by the owner, interactive (Oct 5, 2026, Claude Code 2.1.289) |
| While a call is held, Claude Code 2.1.289 shows the call line and the person can keep typing; after about 123 s it moves the call to the background as a task, the agent keeps working, and the result arrives as a task-completed notification when the hold is allowed | the long-wait step of `scripts/hold-check.sh` | yes, once, by the owner, interactive |
| A refused agent did not try another route, and named Bash `mv` as one it could have taken | the expire step of `scripts/hold-check.sh` | yes, once, by the owner, interactive (what the model said; nothing stops such a route) |
| `TaskStop` on a backgrounded held call sends `notifications/cancelled`; what a backgrounded held call does when the session exits; whether the 123 s is fixed or configurable | none | no: unverified |
| The pre-push scan finds none of the owner's private strings | `git grep --cached -i -c` per string, and the commit's message, author and committer | yes: every count 0 |

The final `scripts/test.sh` on the committed code, apart from this paragraph: ruff (lint and format) clean, the docs check 18 files with 0 findings, and pytest 768 passed, 13 skipped in 214.57 s; 3 min 39 s in all. The 13 skips on Linux are stage 6's 8 (stage 1's 4 platform skips, the 3 reference tests and the Windows names test), the four new Windows-only junction tests, and the new reference test. After this paragraph was written, the docs check ran again with 0 findings.

## Follow-up: the owner's M2a check (Oct 5, 2026)

The owner ran `scripts/hold-check.sh` interactively with Claude Code 2.1.289 at commit c986088; the results and the two new facts are in docs/verified-facts.md (M2a check, interactive) and in the claims table above. This follow-up is one commit, "record the M2a check; background tasks after 123 s; shell bypass". It records them in docs/HOLD-SPEC.md (sections 9, 14, 15 and 17), QUICKSTART-DRAFT.md (the same README text, and the Holds section), MANUAL-CHECK.md and docs/milestones.md (M2a done as of the check), and fixes one thing the check found in the script. No change to Polarizer itself.

- **Pasted answers reached the shell.** The script's answers are one line each, read with `read -r`; when the owner pasted several lines at a free-text question, the first was recorded and the rest stayed in the terminal's input, to be read by the shell as commands once the script ended (harmless `command not found` lines). After each answer, `ask`, `confirm` and `describe` now run `hold_check.py discard-input`, which reads how many bytes wait on the terminal (`FIONREAD`), throws them away with `tcflush(TCIFLUSH)`, and says how many; it never waits for input, and does nothing when stdin is not a terminal. Bash 3.2 has no non-blocking `read -t 0` to do this in the script. The free-text questions now say "Type a short answer; do not paste multi-line text"; the y/n and yes questions don't, since their answers are single words.
- **Test:** `tests/test_hold_check.py::test_whole_check` now pastes two lines at the deny step's last question (only the first is recorded, the second is reported as ignored and never appears in the results) and two at the long wait's first free-text question (without the discard, the second line, `not tried`, would answer the next question). With the discard replaced by a no-op, the test fails at the first of those assertions.
- **The tail of a paste** that has not reached the terminal when the answer is read is not thrown away. A terminal normally delivers a paste in one piece; not tested with a slow paste.

| Claim | Command | Run? |
|---|---|---|
| After each answer, input already waiting on the terminal is thrown away and reported, so it neither answers the next question nor is left for the shell; the free-text questions say not to paste multi-line text | `pytest tests/test_hold_check.py` | yes, POSIX only |
| The test fails without the discard | the same, with `discard_input` made a no-op, then restored | yes, once |
| The whole suite, ruff and the docs check | `scripts/test.sh` | yes: ruff (lint and format) clean, the docs check 18 files with 0 findings, pytest 768 passed, 13 skipped in 212.08 s; 3 min 38 s in all |
| The pre-push scan finds none of the owner's private strings | `git grep --cached -i -c` per string, and the commit's message, author and committer | yes: every count 0 |

## Follow-up: CI

CI ran stage 7's commit, c986088, on all five jobs (Linux with Python 3.11, 3.12 and 3.13, Windows, macOS), and all five passed (run 37247672401). The next commit, 74cedbe (Follow-up: the owner's M2a check, above), failed on macOS only (run 37249953282, and again when the job was re-run): `tests/test_hold_check.py::test_whole_check` failed at its first line, because `drive(env, "status")` returned exit 0 with no output and the `status` helper's `splitlines()[-1]` raised `IndexError`. The Linux and Windows jobs' results for 74cedbe are not recorded here.

- **What 74cedbe changed on the path `status` takes:** nothing that runs. `status` never calls `ask`, `confirm` or `describe`, so `discard-input` never runs; the commit added the `discard_input` function's definition and changed comments, and nothing in `start_output`, `end_output`, `marker`, `toml_ready` or `cmd_status`. `git diff c986088 74cedbe -- scripts tests` shows no other change to that path or to `drive`. So the discard is not shown to be the cause, and it was left as it is: it already throws away only input waiting on the terminal (`tcflush(TCIFLUSH)`), never touches output, and does nothing when stdin is not a terminal.
- **The cause, and what was changed:** every line `status` prints goes through `tee`, started by `start_output`, and `end_output` waited for it with `wait "$TEE_PID"` before bash exits. `tee` was a process substitution, which bash 3.2 can't wait for (docs/dev/STAGE5-NOTES.md, Follow-up: macOS, where this was found and left): on macOS the wait returned at once, bash exited, and `tee` lost the terminal before writing. On Linux, with that wait left out, the output was empty in 199 of 200 runs on a pseudo-terminal (bash is the session leader, as in `drive`), and with the wait, in 0 of 200. The three scripts now start `tee` as a job of their own shell, which bash 3.2 waits for (Follow-up: macOS output, below).
- **The tests' pty reading:** `drive` in `test_hold_check.py` and `test_m1a_check.py` now read through `tests/helpers/ptyread.py`: they stop reading only at end of file or EIO (any other error is raised, where it used to be taken as the end), then wait for the child and read whatever is still on the terminal, so no output is dropped on any platform. Each `status` helper fails with "status exited 0 and printed nothing" rather than `IndexError`. `test_hold_cli.py` and `test_pin_cli.py` give a pseudo-terminal only as stdin and read output through pipes, and `test_rugpull_check.py` uses no pseudo-terminal, so they are unchanged.
- **Whether this fixes the macOS job** can only be confirmed by CI. The test changes above only make the failure say so; the fix is in the scripts (Follow-up: macOS output, below).

| Claim | Command | Run? |
|---|---|---|
| c986088 passed CI on all five jobs; 74cedbe failed on macOS at `test_whole_check`'s first `status` | CI runs 37247672401 and 37249953282 | yes, by CI (as reported by the owner) |
| Nothing 74cedbe changed runs on `status`'s path | `git diff c986088 74cedbe -- scripts tests` | yes |
| On Linux, bash exiting before `tee` writes leaves a pseudo-terminal with no output | a throwaway script in the session scratch directory, 200 runs each way | yes: 199 of 200 empty without the wait, 0 of 200 with it |
| The hold-check tests pass 20 times in a row with the new reading | `pytest tests/test_hold_check.py`, 20 times | yes: 20 of 20, Linux only |
| The macOS job passes | CI | no: only CI can confirm it |

## Follow-up: macOS output (bash 3.2 wait)

CI's macOS job failed deterministically in `test_hold_check.py::test_whole_check`, because `hold-check.sh status` printed nothing (Follow-up: CI, above). The scripts now start `tee` in a way bash 3.2 waits for.

- **What changed:** in `scripts/hold-check.sh`, `scripts/m1a-check.sh` and `scripts/rugpull-check.sh`, `tee` is `(trap '' INT TERM; exec tee -a "$RESULTS") < <pipe> &` (without `-a` in rugpull-check.sh), a job of the script's own shell, reading a named pipe (`mkfifo`) onto which the shell then opens its standard output and error. hold-check.sh and m1a-check.sh make the pipe in a new `mktemp -d` directory under `$TMPDIR` (or /tmp); rugpull-check.sh makes it in its own temp directory. The pipe, and for the first two its directory, are removed by that path once both ends are open. `end_output` is unchanged: it closes standard output and error and runs `wait "$TEE_PID"`, which bash 3.2 and bash 5 both honor for a job.
- **Why a named pipe, not a pipeline into tee:** with `{ ...; } 2>&1 | tee -a "$RESULTS"`, the script's body would run in a subshell, and the script's own process would only wait on the pipeline. That process is the one the tests and the person see: `test_m1a_check.py` types Ctrl+C only once the script's process is asleep in its read (`/proc/<pid>/syscall`), which would never happen; a SIGTERM sent to it would wait for the pipeline to end before any trap ran; and with `set -e`, a body to the left of `||` runs with errexit off. With the named pipe the script stays one shell, with the same process, traps, reads and exits.
- **Kept as they were:** every exit code (0, 1, 2 and 130), every prompt (standard input is never redirected, so answers come from the terminal), Ctrl+C (tee ignores INT and TERM, the script's `on_signal` prints its line and exits 130), the discard of extra input after each answer, and the results file (appended by hold-check.sh and m1a-check.sh, written afresh by rugpull-check.sh).
- **One window, handled:** a Ctrl+C or SIGTERM that arrives while the pipe is being connected is held (`SIGNALLED=1`) and acted on once both ends are open, so its "stopped by Ctrl+C" line is recorded too; before, a signal there was printed on the terminal only. Bash 5.2 runs a trap and retries a pipe's open that a signal interrupted (tried here with a throwaway script); what bash 3.2 does there is not verified.
- **What the scripts now delete:** only the pipe and its directory, by the path they just made. hold-check.sh's header said "Nothing is deleted"; it now names the pipe.
- **The test that would have caught it on Linux:** `tests/helpers/slowtee.py` runs a script as bash 3.2 would, on any bash: a `BASH_ENV` file replaces `wait` with one that waits only for a job (one listed by `jobs -p`), as bash 3.2 does, and a stand-in `tee` first on `PATH` sleeps 0.2 s before each write. `BASH_COMPAT=32` doesn't do it: with compat 32, 40, 42, 43 and 44, bash 5.2 still waited for a process substitution (a throwaway script). Each of the three test files has `test_output_is_written_before_exit_on_bash_3_2`: hold-check.sh `status`, then `reset` answered `yes`; m1a-check.sh `status`, then `approve` stopped by Ctrl+C at "Type yes" (exit 130); rugpull-check.sh's whole run with the stub claude. Each asserts that the results file, read the moment bash exits, already holds all of the output, and that the terminal (or the pipe, for rugpull-check.sh) shows it; the hold-check and m1a tests also check that the pipe's directory is gone. On the scripts as they were at f8e4574, all three fail: hold-check.sh's and m1a-check.sh's `status` left the results file empty at exit, and rugpull-check.sh's ended at `== checks`.
- **The lint:** `tests/test_scripts_portable.py::test_no_wait_on_a_process_substitution` flags, as text, a `wait` on a PID variable set from `$!` after a process substitution with no `&` job in between (anywhere in the file, since a function that waits may come first), and `wait $!` straight after one. Four cases it must catch and three it must pass are tested. On the scripts at f8e4574 it flags hold-check.sh:105, m1a-check.sh:96 and rugpull-check.sh:67.
- **Runs:** the three scripts' test files (`tests/test_hold_check.py`, `tests/test_m1a_check.py`, `tests/test_rugpull_check.py`) 20 times in a row: 28 passed each time, exit 0, in 73.1 to 77.6 s. `scripts/test.sh` (ruff, the docs check and pytest): 784 passed, 13 skipped, in 222.78 s. All on Linux (WSL2, Python 3.12.3, bash 5.2.21). No `hold-check-output.*` or `m1a-check-output.*` directory was left in /tmp.
- **Not verified:** anything on macOS, which only CI can confirm. No bash 3.2 binary is installed here (bash 5.2.21 only), and none was installed; the emulation copies bash 3.2's rule for `wait`, not bash 3.2. Claude Code, any model, and the scripts' real runs were not run.

| Claim | Command | Run? |
|---|---|---|
| The three new tests and the lint fail on the scripts at f8e4574 | the new tests and `test_no_wait_on_a_process_substitution`, with the three scripts set back to f8e4574 | yes: 6 failed |
| They pass on the changed scripts | the same | yes |
| The three scripts' tests pass 20 times in a row | `pytest tests/test_hold_check.py tests/test_m1a_check.py tests/test_rugpull_check.py`, 20 times | yes: 20 of 20, 28 passed each time, Linux only |
| The whole suite, ruff and the docs check | `scripts/test.sh` | yes: 784 passed, 13 skipped, in 222.78 s, Linux only |
| The macOS job passes | CI | yes, by CI at 9270c97, with the other four jobs (as reported by the owner) |
