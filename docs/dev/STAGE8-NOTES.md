# Stage 8 notes

Read after docs/dev/STAGE7-NOTES.md. This file records stage 8's round: the flaky hold-check test fixed first (step 1), the owner's answers applied to docs/MEASURE-SPEC.md (step 2), and the drills build (step 3). It is written as the work goes, so an interruption leaves a record on disk. The session was interrupted once already, when the computer shut down during step 1's repeat runs (below).

## Step 0

Read on Oct 5, 2026 (UTC) at f03a29e: `git status` clean, `scripts/test.sh` passed (798 passed, 13 skipped in 233.5 s), guard snapshot `snapshot-20261005T041445Z.txt` taken. The gaps found and decided are listed in docs/MEASURE-SPEC.md, section 17, What stage 8's step 0 found.

## Step 1: the hold-check expire step no longer races a 1 s timeout

CI run 37259695458 (commit 03cedbd, Linux, Python 3.12) failed in `tests/test_hold_check.py::test_whole_check` at the expire step: `holds --wait` printed the hold, but by the time `scripts/hold-check.sh` looked again for an open hold of a running session, the 1 s timeout had expired it, and the script stopped with `no open hold of a running session was found`.

**What changed:**
- **`wait_for_hold`** in `scripts/hold-check.sh` records the ledger's last seq before it asks for the call, and after `holds --wait` returns takes the first `hold.created` after that seq, whether or not it has ended (`scripts/hold_check.py new-hold`). It waits for that hold if `holds --wait` returned for an older open hold. It prints the hold's tool, rule and reason, and `it has already ended: ...` when it has.
- **Every step that picks a hold after `holds --wait`** goes through `wait_for_hold`: deny, allow, long-wait and expire. All four changed.
- **`other_holds` still uses `open-hold` on purpose.** It runs at the end of a step to list other holds that are still waiting (Claude may have retried a call), each with the command that ends it, so only open holds of a running session belong in it.
- **The test's expire timeout** is `EXPIRE_TIMEOUT = 8`. The comment says what it is: a margin chosen at more than five times the 1 s the step used to have, not a measurement. The lookup no longer needs the hold to be open; only `holds --wait` must see it open once (one 0.25 s poll and one locked read).
- **A testing-only delay,** `POLARIZER_CHECK_LOOKUP_DELAY`, read only with `POLARIZER_CHECK_TESTING=1`, delays each step's lookup after `holds --wait` returns, as a slow machine would. `test_expire_step_finds_a_hold_that_already_ended` uses it (hold timeout 4 s, delay 6 s). It is part of the committed script, documented in its header. The temporary hook used to reproduce the failure on the old script (below) is not.
- **`test_new_hold_is_the_first_created_after_the_mark`** checks `new-hold` and `last-seq` on a built chain, on every platform.

**The old failure, reproduced.** Run first before the shutdown, then again after it, since the logs were in /tmp. The old code is the script, helper and test file at f03a29e; the temporary hook was one line in the old script, `[ "$TESTING" = 1 ] && sleep "${POLARIZER_CHECK_LOOKUP_DELAY:-0}"`, after `holds --wait` returns.

Each run with `POLARIZER_CHECK_LOOKUP_DELAY=3` (a 3 s delay; the hold timeout in the old test is 1 s):
- **(a) old test, old script and helper, temporary hook:** `FAILED tests/test_hold_check_oldcopy.py::test_whole_check`, `1 failed in 23.70s`, the script having printed `stopped: no open hold of a running session was found`, as in CI.
- **(b) old test, new script and helper:** `1 passed in 24.89s`.
- **(c) the new test file:** `6 passed in 50.48s`.

The old script and helper were put back from copies after (a), and checked by sha256 against the new ones saved before it; the temporary hook is not in the committed script. The old test ran as a temporary copy, `tests/test_hold_check_oldcopy.py`, deleted after (b). Before the shutdown the same three runs gave the same results (1 failed, 1 passed in 26.12 s, 6 passed in 50.67 s).

**Repeated runs** (after the shutdown; the 20 runs before it reached run 6, all passing):
- `tests/test_hold_check.py` 20 times in a row: 20 of 20 passed, 6 tests each, 38.07 s to 40.60 s per run.
- Once under load, with two busy-loop Python processes (`python3 -c 'while True: pass'`, PIDs 16317 and 16318, each at 100% CPU) started before and stopped by their PIDs after: `6 passed in 39.17s`.

**The commit.** Before it, `scripts/test.sh` failed once in `tests/test_scripts_portable.py`: the new error line's words "test timeout" looked like the GNU-only `timeout` command to the portability check. The line now names the two variables. Then `scripts/test.sh` passed on the step-1 files alone (the rest set aside with `git stash`): 800 passed, 13 skipped in 268.89 s. Commit 6b74d09, "tests: hold-check expire step no longer races a 1 s timeout": `scripts/hold-check.sh`, `scripts/hold_check.py`, `tests/test_hold_check.py`.

## Step 2: the owner's answers in the spec

docs/MEASURE-SPEC.md section 16 records all 14 answers as decided, one line each; questions 1, 10 and 11 say they take the spec's recommendation. The sections each answer changes, LEDGER-SPEC.md's drill kinds, milestones.md (stage 10 deferred until after M3; M3's monotonic `elapsed_ms`) and CLAUDE.md rule 6 (`~/.local/share/polarizer-drills`) were changed with it. `scripts/test.sh` on those four files alone: 800 passed, 13 skipped in 252.72 s. Commit 29bca55, "spec: measure decisions".

## Step 3: the build, by section 15's steps

1. **The statistics module** (`src/polarizer/measure.py`, `tests/test_measure.py`). The tests were written first from section 7's tables, and every fixed value matched the code on the first run: all 14 Wilson rows, all 5 Newcombe rows, the points, medians and time formats. One test failed then, on my own test's assumption: the k = 0 lower bound came out as 2.8e-17, not 0. The spec says k = 0 and k = n are clamped, so the code now sets those bounds exactly. No expected value was changed.
2. **The renderer refactor.** `holds.render_block(hold, started, state, now, arguments, size)` renders a block from arguments; `holds.block` reads and checks the side file and calls it, keeping its side-file problem lines. Every existing golden file passed unchanged (`tests/test_golden.py`, `tests/test_holds.py`, `tests/test_hold_cli.py`).

3. **The scenario set** (`src/polarizer/scenarios/`, `tools/make_drill_set.py`, `tests/test_scenarios.py`). Set 1 has 150 scenarios: 90 clean and 60 planted, 12 of each shape, on 18 tools of six invented servers (`fs`, `web`, `mail`, `tickets`, `git`, `pkg`). The JSON is written by `tools/make_drill_set.py`, which holds the scenarios as data and works out each held call's class, rule and reason as the rule function would, from an imagined config and the built-in hold patterns. The file is ASCII. `polarizer.scenarios` loads the newest set and checks the schema, including each reason against its rule's template. `python -m polarizer.scenarios show <id> [--answer]` prints one scenario's screen. All 70 validation tests passed on their first run. Its sha256, `e2010c06...92eb80`, is pinned in the test.
   - **Not reviewed by a second person** (step-0 finding 4). The owner's review at stage 8's stop is that review; until then set 1 has not shipped, so corrections edit set 1 and update its pin.
   - **What the real-names grep checked,** on `drill-set-1.json`:
     - every dotted token: all hosts end in `.example`, `.test`, `.invalid`, or are `example.com`, `example.net`, `example.org` or their subdomains; the rest are file names (`env.example`, `prod.env`, `gp41.patch`, `harbor-map.git`, `release-1.3`, `n.venv`);
     - every e-mail address: 17, all role addresses (team@, ops@, editors@, reviewers@, support@, newsletter@, board@, finance@, archive@, forward@, sync@, deploy@) at those hosts;
     - every home directory: `/home/river`, `/home/sol`, `/home/wren`, `/home/marlow`, `/Users/juniper`, `/Users/indigo`, all listed in `names`;
     - IP addresses: none;
     - credential patterns (key prefixes, PEM headers, tokens): none;
     - well-known names (github, gitlab, google, amazon, aws, microsoft, azure, apple, slack, stripe, openai, anthropic, claude, docker, jira, notion, npm, pypi, ubuntu, debian, postgres, mysql, redis, facebook, twitter, linkedin, paypal, and the pre-push scan's words): only `github`, 21 times, every one in `.github/workflows`, a path the spec names as one every reviewer must recognize.
   - **Generic tools named in script content** are real software: `sh`, `python3 -m venv`, `python -m pip`, `curl`, `git`, `make`, `wc`, `rm`. They are left in, as the spec leaves `.git/hooks` and `package.json`, because a reviewer must recognize them; the owner may judge otherwise. `Europe/Lisbon` is a time zone name. Package names (fernleaf, quillmark and the rest) were invented here and could not be checked against any registry, since this session fetches nothing.
4. **The drill** (`src/polarizer/drill.py`; `drill` in `cli.py`; `ledgerdir.git_tree_by_files`; one `except LedgerError: raise` in `writer.py`; `tests/test_drill.py`, `tests/helpers/drillkit.py`, `tests/helpers/drill_scenarios.json`, 25 golden files). The sampler matched section 6's vectors when first drawn, from a throwaway script written from the spec's text before the code existed, and `test_sampler_vector` pins them. The example end screen of section 5 is reproduced exactly: `test_drill_golden` compares it, and the intro, with the spec's text, not only with the golden file. Its plan comes from seed `000...09d9`, found by searching for the first seed whose plan puts 6 planted calls in 20, the README scenario at call 3, a look-alike at call 7 and a clean call at call 12. Found and fixed while building:
   - `serve_refused_drill_ledger.txt` first held a config error (`no upstreams configured`): the test's config had no upstream, so it never reached the refusal. The config now has one, which is never started, and the test checks the line's start.
   - The set check quoted ids as `'s001'`; it prints `s001` now.
   - `test_drill_writes_only_its_directory` first failed on the drill directory's parent, whose mtime changes when the directory is created in it. The test allows that one change and says why.

5. **`drill report` and `--export`** (`tests/test_drill_report.py`, `tests/helpers/drillledger.py`, 7 golden files). The 7-drill fixture is built from one function per drill, each with a docstring, as `holds_mixed.txt`'s is. Every line of section 9's example report appears, in order, in the output; the fixture's one answer over 300 s adds its lines under plain. One assertion failed on its first run, on a value I had typed from memory, not one the spec gives: the interval for 3 false flags in 41 clean calls. Worked out separately from section 7's formula it is 2% to 20%, as the code prints, and the test now says so. When first built, the fixture's shapes totalled 7 different-tool and 6 misleading-summary calls instead of the spec's 6 and 7; drill 7's last shape was corrected before any test ran on it.
6. **Fixtures and docs.** `tools/make_fixtures.py` gains `valid/drills`, a drill ledger with every drill kind and field, its `ledger.head` at genesis; regenerating changed only `conformance/expected.json` and added the fixture's two files, and both verifiers find it intact. docs/DRILL-GUIDE.md (about 560 words of prose besides its commands and screen excerpts) and `tests/test_drill_guide.py`, which parses every command, runs the guide's polarizer commands in order on a pseudo-terminal with a temporary home, and checks the excerpts and closing lines against what Polarizer prints. The first run of that test failed because the drill drew the prediction-gate condition at random, and the test's answerer knew only the plain condition's prompts; it now answers whichever prompt appears. LIMITS.md gains a Drills section, and its People line no longer says nothing measures attention. verified-facts.md gains a Stage 8 section and four unverified items. The build's decisions are in docs/MEASURE-SPEC.md, section 17, Stage 8 build; docs/dev/README.md and CLAUDE.md's read-first list name this file.
   - **The wheel** was built offline (`uv build --wheel --offline`) into the scratch directory and installed into a throwaway venv there: it carries `drill-set-1.json`, and an installed `polarizer` loads it and reads its version from the metadata.

**Repeated runs of the stage 8 tests** (`tests/test_measure.py`, `test_scenarios.py`, `test_drill.py`, `test_drill_report.py`, `test_drill_guide.py`; 442 tests):
- five times in a row: 442 passed each time, in 10.26 s to 10.98 s;
- once under load, with two busy-loop Python processes (PIDs 38093 and 38094, each at about 99% CPU) started before and stopped by their PIDs after: 442 passed in 10.49 s.

`scripts/test.sh` with stage 8 in place, before the tool check: 1244 passed, 13 skipped in 267.38 s. Again before the stage 8 commit: 1244 passed, 13 skipped in 257.00 s. `scripts/dev/guard.sh check` then reported no changes in any guarded repo or directory and the MCP config hashes in `~/.claude.json` unchanged; its size and mtime changed, from Claude Code's own bookkeeping. The pre-push scan (the owner's home path, the Windows users path, the Windows user name and five other words, case-insensitive, over all 370 files to be committed) found 0 of each.

**Time bounds in the new tests.** Every wait in a drill test waits for something to happen, not for a fixed time: the pseudo-terminal tests wait up to 60 s for each prompt (the guide's test up to 300 s for the whole drill), where a prompt appears in well under a second here, far more than five times what is needed. The in-process drills use an injected monotonic clock, so their recorded times are exact and no test sleeps. The subprocess tests (the terminal refusal, the audit hook, the guide's report and export) have a 120 s timeout each against about a second of work.

## Step 4: one drill in each condition, as a tool check

These are checks of the tool, not results about a person. A script (`run.py` in the session's scratch directory, not in the repo) drove `python -m polarizer drill` on a pseudo-terminal twice, `--condition plain` and then `--condition prediction-gate`, into one temporary ledger, with a temporary home. The answerer is a fixed rule, not a model: it denies a call whose arguments show one of a few strings (`.git/hooks`, `.ssh`, `.netrc`, `relay.invalid`, `offsite`, `curl`, `git-credentials`) or whose tool is `fs__delete_file`, and allows the rest; it gives every prediction the same words. It answers within milliseconds, so every time is 0.0 s.

- Both drills finished, exit 0. Plain: 4 planted calls, 2 denied (too few to say a rate); 16 clean, 2 denied (13%, 3% to 37%). Prediction gate: 6 planted, 2 denied (33%, 9% to 71%); 14 clean, 1 denied (7%, 1% to 32%).
- `polarizer verify --ledger-dir` printed `intact: 145 entries, 0 sessions, 0 calls`, exit 0; `verify --args` printed `args: 0 matching, 0 missing, 0 tampered, 0 orphaned`.
- `drill report` showed both drills, `calls seen in an earlier drill: 0 of 40` (the second drill left out the first one's scenarios), and the comparison: catches too few to compare, false flags -5 points (-30 to +21), not distinguishable from noise.
- `--export` wrote one line of JSON with counts, rates, the date 2026-10-05, set "1" and version "0.1.0", and nothing else.
- `~/.local/share/polarizer-drills` was never created in this session.

## Deviations

From docs/MEASURE-SPEC.md as committed in 29bca55; each is recorded in its section 17 (What stage 8's step 0 found, and Stage 8 build):
- The scenario set is loaded before the ledger is opened, so a set that fails its check writes nothing.
- `drill` finds a git working tree by looking for `.git`, starting no process.
- A writer that stops prints its own line before the drill's stop line.
- Ctrl+C and the end of input read `Drill stopped` and `, stopped`, as `q` does; the ledger keeps `interrupted`.
- The report's first line always gives both counts; drills with no answer are left out everywhere.
- One-screen golden files hold one screen, so `drill_end.txt` is the end screen alone.
- `--keep-predictions` with `drill report` is a usage error with its own line.
- `show` takes `--answer`.
- `test_loading_check` is in `tests/test_drill.py`, beside the other refusals, rather than in `tests/test_scenarios.py`; `test_render_block_refactor_keeps_holds_output` is the existing golden tests, as the spec says, and no new test carries that name.

## Guesses

1. **Names the spec left open:** the test helpers (`drillkit.py`, `drillledger.py`), the builder `tools/make_drill_set.py`, the seams in `drill.py` (`monotonic_ns`, `random`, `make_console`, `load_set`, `is_terminal`), the export's `over_300s` field names, and the shapes of every scenario.
2. **Enter lines without a newline.** `Press Enter to start.` and the like are written without a newline, so the person's Enter ends the line and one blank line separates calls on a terminal.
3. **The prediction is stored after `strip()`;** `length` counts the stripped line, and `safe()` then folds its inner whitespace.
4. **Generic tools in scenario text** (`sh`, `python3`, `pip`, `curl`, `git`, `make`) are real software, left in because a reviewer must recognize them (Step 3).
5. **The guide's install lines** use the wheel name `uv build` gives (`polarizer-0.1.0-py3-none-any.whl`) and the repository's public URL from pyproject.toml; neither `uv tool install` line was run.

## Not run

- **`uv tool install`,** either line of the guide: it installs for the user, outside the repo, and the repository is private. The wheel was built and installed into a scratch venv instead (Step 3, 6).
- **Windows and macOS.** Nothing in this stage ran there. CI has not run stage 8.
- **A person reviewing the scenario set** (Step 3).
- **A real person's drill.** Step 4's drills were answered by a script.
- **The README** is unchanged, as the brief says; docs/MEASURE-SPEC.md section 14 holds the plan.

## Windows and macOS hazards in the new code

- **The console on Windows.** `sys.stdin.readline()` on a Windows console returns a line ending in `\r\n`, which `strip()` removes. Ctrl+C there may arrive as `KeyboardInterrupt` during `readline`, or end the input, or neither while a line is half typed; each ends the drill as interrupted if it reaches Python, but it has not been run (verified-facts.md, Unverified). `is_terminal` uses `os.isatty` on both handles, which is true for a Windows console.
- **The pseudo-terminal tests** are skipped on Windows (no `pty` module) and have run only on Linux; macOS's pty may give end of file instead of EIO at the end, which `tests/helpers/ptyread.py` already handles (CI run 37249953282).
- **Paths in the intro and the refusals** print the directory as the operating system gives it, with backslashes on Windows; the golden files replace the directory with `<dir>`.
- **The git-tree check** follows `os.path.realpath` and `Path.exists`; on Windows a `.git` file of a worktree is found the same way. A tree known only through `GIT_DIR` is missed everywhere.
- **The audit-hook test** watches event names that exist on every platform (`os.startfile` only on Windows, `os.fork` and `os.forkpty` only on POSIX); an event that a platform never raises can't be missed.
- **The export file** is opened with mode `x` and `newline="\n"`, so it is the same bytes everywhere.
- **The default directory** on Windows is `.local\share\polarizer-drills` under the profile, as for the serve ledger; the guide says so.

## Claims

| Claim | Checked by | Run |
|---|---|---|
| The hold-check expire step finds its hold whether or not it has ended | `tests/test_hold_check.py::test_expire_step_finds_a_hold_that_already_ended`, and the three demonstrations in Step 1 | yes, 20 times in a row and once under load |
| Every Wilson and Newcombe vector of section 7 matches | `tests/test_measure.py` | yes |
| The sampler matches section 6's vectors | `tests/test_drill.py::test_sampler_vector` | yes |
| The plan can be drawn again from `drill.started` | `test_plan_is_reproducible_from_the_ledger` | yes |
| A drill's block is byte for byte what `polarizer holds` prints, for all 150 scenarios | `test_drill_block_equals_holds_block` (the real `holds` command) | yes |
| Every existing `holds`, `allow` and `deny` golden file is unchanged by the refactor | `tests/test_golden.py` | yes |
| Every planted call differs from its task in its stated way; every clean call matches its task | `tests/test_scenarios.py::test_planted_calls_differ_as_stated`, `test_clean_calls_match_their_task` | yes |
| Every name in the set is invented; nothing looks like a credential | `test_names_are_invented`, `test_name_checks_catch_real_looking_names`, and the grep in Step 3 | yes |
| The set is balanced so no surface feature gives the answer | `test_set_balance` | yes |
| A drill opens no network connection and starts no process | `test_drill_opens_no_connection_and_starts_no_process`, with `test_audit_hook_sees_what_it_watches` | yes |
| A drill writes only its own directory, with no `args/` | `test_drill_writes_only_its_directory` | yes |
| The drill ledger verifies, with 0 sessions and 0 calls, and its head stays at genesis | `test_drill_ledger_verifies`, `test_drill_kinds_are_not_security_kinds`, Step 4 | yes |
| Each drill kind has exactly its fields, inside the subset | `test_drill_kinds_and_fields`, `valid/drills` in `tests/test_conformance.py` | yes |
| Decision times are monotonic, never `ts` differences | `test_elapsed_ms_is_monotonic` | yes |
| By default only a prediction's length is kept | `test_prediction_gate_flow` | yes |
| Answers over 300 s are marked and reported with and without | `test_over_300_is_marked`, `test_report_on_fixture` | yes |
| The screens equal section 5's text | `test_drill_golden` | yes |
| `drill report` prints section 9's example | `tests/test_drill_report.py::test_report_on_fixture` | yes |
| The export holds no free text, path, name, id or time of day | `test_export_has_no_free_text_paths_or_names`, `test_export_schema` | yes |
| `drill` refuses a serve ledger, and `serve` refuses a drill ledger, writing nothing | `test_drill_refuses_serve_ledger`, `test_serve_refuses_drill_ledger` | yes |
| `drill` needs a terminal | `test_drill_needs_terminal` | yes |
| A drill runs to its end on a real pseudo-terminal | `test_drill_on_a_pty`, `tests/test_drill_guide.py::test_guide_commands_run`, Step 4 | yes, Linux only |
| `__version__` comes from pyproject.toml through the package metadata | `test_version_from_metadata`, and the wheel in Step 3, 6 | yes |
| Every command in the guide parses, and its polarizer commands run | `tests/test_drill_guide.py` | yes, Linux only for the run |
| The installed package carries the scenario set | the wheel built and installed offline in Step 3, 6 | yes, by hand |
| Drills work on Windows and macOS | CI | no |
