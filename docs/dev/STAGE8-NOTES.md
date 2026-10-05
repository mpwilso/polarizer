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

## Stage 8 review (Oct 5, 2026, UTC)

The owner's review of stage 8, after the tool check of Step 4: five items, worked in order, in one commit. Guard snapshot `snapshot-20261005T181739Z.txt` taken first. Written as the work goes.

### 1. The sampler: a fixed 8 planted and 12 clean

**How the old sampler decided the count.** `drill.plan` drew the condition (`below(s, 2)`), then `k = lo + below(s, hi - lo + 1)` with `lo = ceil(calls / 5)` and `hi = floor(2 * calls / 5)`: for 20 calls, uniform on 4 to 8. The planted ids were shuffled and the first `k` taken, with no regard to shape.

**Measured, before any change** (`sim_old.py` in the session's scratch directory, the code at 98ade25 and the shipped set): seeds 0, 1, 2, ... as 16-byte counters, `--calls` 20, nothing excluded, until each drawn condition had 10,000 sessions (20,201 seeds):

| Condition | 4 planted | 5 | 6 | 7 | 8 | catch rate reportable (5+ planted) | false-flag rate reportable (5+ clean) | a shape missing |
|---|---|---|---|---|---|---|---|---|
| plain | 1,961 | 1,965 | 2,083 | 1,982 | 2,009 | 80.39% | 100.00% | 83.47% |
| prediction gate | 1,889 | 2,020 | 1,975 | 2,072 | 2,044 | 81.11% | 100.00% | 83.49% |

Under 95%, so the sampler changed as the brief says.

**The new sampler** (`drill.plan`, `drill.planted_count`, `ScenarioSet.shapes`): `k = min(floor(2 * calls / 5), planted ids)`, 8 of 20. The shapes the set has are shuffled into a dealing order, and calls are dealt one per shape per round, skipping a shape that has run out, so shape counts differ by at most one. Each shape's ids are drawn from those not excluded, or from all of that shape when too few are left. The clean draw is as before. The seed is drawn and recorded as before. MEASURE-SPEC.md section 6 has the pseudo-code.

**The vectors were computed twice.** First, `indep_sampler.py` in the scratch directory: stdlib only, written from the new pseudo-code without importing Polarizer. Then the code. Both gave the same values:

- the stream's first values: 8468598625902157147, 15816047190215027138 (script and code);
- a: `prediction-gate`, 8, `c02, c09, p07, p06, p10, c04, c11, p09, c05, c06, c08, c10, p05, p03, c03, c01, c12, p01, c07, p04` (script and code);
- b: `prediction-gate`, 8, `c07, c11, c09, c05, c10, c01, c04, c02, p04, c08, p07, p05, p03, p10, p02, c03, c12, p09, c06, p06` (script and code);
- c: `prediction-gate`, 4, `c02, c05, c06, p08, c09, p04, p06, c01, c07, p10` (script and code).

The tests changed only after that comparison. The Wilson values for the new example end screen, 7 of 8 (0.529112 to 0.977583, `88% (52% to 98%)`) and 1 of 12 (0.014865 to 0.353880, `8% (1% to 36%)`), were also computed first by a stdlib-only script (`wilson.py`) and matched `measure.rate_parts`. They are now rows of section 7's table and of `tests/test_measure.py`.

**Measured after the change** (`sim_new.py`, the same seeds): 10,000 sessions in each condition, every one with 8 planted and 12 clean calls. Catch rate and false-flag rate reportable in 100.00% of sessions, no shape missing, and the shape counts 2, 2, 2, 1, 1 in every session (asserted). Repeats (`gaps.py`, 2,000 drills in a row on one ledger, last two excluded): a planted or clean scenario comes back after 3 drills at the least, 6 at the median, 7.5 on average.

**What else item 1 changed:**
- **The intro** now reads `8 of the 20 calls are planted: ... Real work has far fewer.`, because its old sentence, that the count changes from drill to drill, is no longer true (MEASURE-SPEC.md section 17, Stage 8 review, 2). The guide and LIMITS.md state the count too, and the new limit that a person can count down.
- **The golden seed** is now `000...05fd`: the first counter seed whose plan on the test set draws plain and puts the README scenario at call 3, a look-alike at call 7 and a clean call at call 12 (`find_seed.py` in the scratch directory). The example times are now 8 planted and 12 clean values with the same medians, so the median line is unchanged.
- **Golden files changed** (regenerated with `POLARIZER_UPDATE_GOLDEN=1`, each diff read): `drill_intro_plain.txt`, `drill_intro_prediction_gate.txt` (the count); `drill_end.txt` (8 and 12, 88% and 8%); `drill_end_over_300.txt`, `drill_end_stopped.txt`, `drill_reveal_caught.txt`, `drill_reveal_false_flag.txt`, `drill_reveal_over_300.txt` and `drill_reveal_right.txt` (another order: call 1 is now a planted call). `drill_call_*.txt` and `drill_reveal_missed.txt` are unchanged.
- **Not changed:** the report fixture (`tests/helpers/drillledger.py`, `drill_report.txt`, `drill_export.json`, section 9's example). It is a hand-built ledger, not drawn by the sampler, so its drills of 20 keep 5 or 6 planted calls. Changing it would change section 7's report vectors too. The spec says so in section 9.
- **Tests:** `test_sampler_vector` (new vectors), `test_planted_count_and_shapes` (new: 2,000 drills in a row on the shipped set, each 8 and 12 with shape counts 2, 2, 2, 1, 1, and nothing from the two before), `test_end_screen_equals_report_line` (8 and 12), `test_drill_golden` (the spec's new text), and two new rows in `tests/test_measure.py`.

### 2. The guide, for a reader who is not an engineer

docs/DRILL-GUIDE.md: the opening says a terminal is needed (what it is called on each system) and that a friend who works with computers can do the install in five minutes. The install is two numbered steps in plain words. "Words you will see" gives planted call, clean call, caught and false flag in three lines. "How to read an action" follows the sample screen, which now shows the arguments in braces. The guide says clean calls are held too, as real holds are, and that the reveal explains each one. It gives the `uvx --from git+https://github.com/mpwilso/polarizer polarizer drill` line, marked as working once the repository is public and uv is installed. It says the planted share is far above any real rate and that one drill's interval is wide (88%, 52% to 98%, the new example). Every other statement is kept: the privacy list, the closing lines and the not-for-grading text. The package file now goes in the home folder, so the guide needs no `cd`. It is about 800 words of prose (935 in all), up from about 560, and section 13 of the spec says so.

**`tests/test_drill_guide.py`** now accounts for every command line in the guide. The three polarizer lines run as before. `test_guide_commands_exist` also parses the polarizer command inside the uvx line, and checks that every other line is one of four uv lines. `test_guide_install_lines_run_offline` (new) builds the wheel offline, then runs the guide's `uv tool install ./polarizer-0.1.0-py3-none-any.whl` and `uv tool uninstall polarizer` exactly as written, with `UV_OFFLINE=1` and its own `UV_TOOL_DIR` and `UV_TOOL_BIN_DIR`. Between the two, the installed `polarizer drill report` prints the "none yet" line. Only the two lines that fetch from GitHub are not run. It skips if `uv` is not on the PATH, which CI has. It takes about a second. `~/.local/share/uv/tools` and `~/.local/bin` held no polarizer afterwards.

### 3. `writer.py` at 98ade25

The only change in 98ade25 to `src/polarizer/writer.py` is two lines in `_check_existing`, plus its docstring. At open, each verified entry is handed to the `on_entry` listener. Before, any exception from the listener became `LedgerError("polarizer: could not fold seq <n> into pin state: <error>", 1)`. Now a `LedgerError` raised by the listener itself is re-raised unchanged, with its own line and exit code. Every other exception is wrapped as before.

**Who calls it:** two listeners raise `LedgerError` on purpose. `serve`'s `no_drills` (`cli.py`) refuses a ledger with `drill.*` entries with exit 2, and `drill`'s `listen` (`drill.py`) refuses a ledger with `session.started` with exit 2. Neither existing listener raises it (`pins.PinState.apply`, `holds.HoldState.apply`, through `decisions.py` and `serve`): neither `pins.py` nor `holds.py` raises anything. The after-open path (`_hand_over`) is unchanged: a listener error there, a `LedgerError` included, still stops the writer.

**Tests that cover it:** `tests/test_drill.py::test_serve_refuses_drill_ledger` and `::test_drill_refuses_serve_ledger` (the two callers, end to end); `tests/test_writer.py::test_listener_failure_at_open_refuses_to_start` (other errors, unchanged); and, new in this review, `tests/test_writer.py::test_listener_ledger_error_at_open_is_passed_on`. That test opens one ledger three ways: with a listener raising `LedgerError` (its line and code 2, nothing written), with a listener raising `ValueError` (the fold line, exit 1, nothing written, as before), and with no listener (it opens and rebuilds the missing head, as before).

**Shown against the old code.** `src/polarizer` was copied to the scratch directory with `writer.py` from 29bca55, the commit before 98ade25, and put first on `PYTHONPATH`. There, `tests/test_writer.py`, `tests/test_pin_startup.py` and `tests/test_pin_durability.py` gave `1 failed, 42 passed`. The one failure is the new test's first assertion: the old code reports `polarizer: could not fold seq 2 into pin state: polarizer: not this ledger` with exit 1. On the current code: `43 passed`. Those three test files are unchanged between 29bca55 and 98ade25. No existing behavior changed: no listener before stage 8 raised `LedgerError`.

### 4. The review sheet

`python -m polarizer.scenarios sheet --out <path>` (`src/polarizer/scenarios/__main__.py`; `show` now shares its block function). It writes one markdown file with every scenario in file order: its id, answer and shape, its task line and the block a drill shows (zero ids, the fixed time `show` uses) in one text fence, and its reveal in another. Each fence is longer than any run of backticks inside it. Then come the summary tables (per shape, tool, class, rule, answer, and planted versus clean per shape), "Look at these", and one line on the nearest misses. Tests: `test_review_sheet` and `test_review_sheet_lists_what_to_look_at` in `tests/test_scenarios.py`. `/drill-review/` is in `.gitignore`. The sheet was written to `drill-review/scenarios.md`: 3,834 lines, 104,579 bytes.

- **Guess:** clean scenarios have no shape, so "planted versus clean per shape" counts, for each shape, the clean scenarios held on the tools that shape's planted scenarios use. The sheet says so above its table.
- **Thresholds,** written in the sheet: task lines the same or at least 0.85 alike by `difflib.SequenceMatcher.ratio()` on lowercased text; reveals under 8 words; shapes under 8; tools held in fewer than 3 scenarios.
- **Set 1 found nothing.** Nearest: s026 and s147 at 0.84; seven reveals of exactly 8 words (s036, s060, s083, s110, s119, s122, s144).
- **The Windows path:** this distro is registered as `parallax` (`$WSL_DISTRO_NAME`), so the sheet is at `\\wsl$\parallax\` followed by the repo's path with backslashes. `wsl.exe` is not on the PATH here, and `/etc/os-release` says Ubuntu 24.04, the release, not the registered name. In PowerShell, `wsl -l -q` lists the registered names.

### 5. Runs

- **`scripts/test.sh`:** `1251 passed, 13 skipped in 269.95s`, exit 0, 271 s wall (1244 at 98ade25, plus 7 new tests).
- **The new and changed test files** (`tests/test_drill.py`, `test_measure.py`, `test_drill_guide.py`, `test_writer.py`, `test_scenarios.py`; 464 tests), five times in a row: 464 passed each time, in 14.79 s to 16.93 s.
- **Once under load,** with two busy-loop Python processes (`python3 -c 'while True: pass'`, PIDs 54469 and 54470, each at about 99% CPU), started before and stopped by their PIDs after: 464 passed in 15.71 s.
- **Time bounds in the new tests:** `test_guide_install_lines_run_offline` gives each of its four subprocesses 120 s against about a second of work. The sampler, sheet and writer tests do not wait.
- **`scripts/dev/guard.sh check`** against `snapshot-20261005T181739Z.txt`, before the commit: no changes in any guarded repo or directory, the MCP config hashes in `~/.claude.json` unchanged (6 locations), and its size and mtime unchanged too; exit 0.
- **The pre-push scan** (the owner's home path, the Windows users path, the Windows user name and five other words, case-insensitive), over all 370 files in the commit: 0 of each.
- **Not run:** `uv tool install` with the network or into the user's own tool directory; the guide's two GitHub lines; anything on Windows or macOS; a person's drill under the new sampler. CI has not run this change.

## Stage 8 follow-up: shortcut audit and a variable planted count (Oct 5, 2026, UTC)

The owner's follow-up brief, four items. Guard snapshot `snapshot-20261005T184537Z.txt` taken first. Written as the work goes.

### 1. The shortcut audit

**Question:** can a person, or a trivial rule, tell planted from clean without reading the task and the arguments? Only what a drill shows before the answer is used: the call screen without the reveal.

**Features** (`src/polarizer/scenarios/audit.py`): tool, class, class_from, the rule of the `held by` line, the number of arguments, argument bytes (the length the block's `args_commit` line gives, in buckets under 60, 60 to 99, 100 to 139, 140 and over, about the set's quartiles), task length in characters (under 60, 60 to 74, 75 to 89, 90 and over), and whether the compact arguments contain each of `..`, `.git`, `.ssh`, `.env`, `http` and `@`. A tell is a value with at least 6 scenarios of which at least 90% share one answer.

**Leave-one-out:** each scenario is answered by a rule learned from the other 149: the majority answer for its value of one feature (deny only on a strict planted majority; a tie or an unseen value is allow), or a naive Bayes over every feature (add-one smoothing). Each is scored over the 150 scenarios and as the mean share right over 1,000 seeded 20-call drills drawn by the drill's own sampler (seeds 0 to 999, nothing excluded), next to always answering allow. Standard library only.

**Set 1 has no tell.** The value nearest one is `fs__edit_file`, 1 planted and 7 clean (87.5%). The best single-feature rule is argument bytes, 64.0% over the scenarios against always allow's 60.0%, because 9 of the 12 calls of 140 bytes or more are planted. The tool rule scores 39.3%: with most tools close to half and half, leaving one scenario out flips its tool's majority against it, a known effect of leave-one-out on balanced values. Naive Bayes scores 42.0% for the same reason. The full tables are in the review sheet and printed by `python -m polarizer.scenarios audit`.

**No set 2.** The brief's set 2 was for removing tells; with none, the owner chose to keep set 1 as the shipped set (asked during this session). Set 1 is unchanged (regenerated from `tools/make_drill_set.py` and compared byte for byte), its pin is unchanged, and `scenarios.load(version)` now loads any shipped set by its version, so a ledger that names an older set can draw its plan again once a set 2 exists.

**Tests** (`tests/test_scenarios.py`): `test_no_shortcut` (no tell over the shipped set, and the best single-feature rule within 10 points of always allow, over the scenarios and over 1,000 drills); `test_shortcut_checks_catch_a_tell` (on set 1 with `..` added to every planted call and six more arguments on six clean calls, both tells are found and the `..` rule scores 100%), which is how the check is shown to fail, since the shipped set never makes it fail; `test_audit_features_and_buckets`; `test_load_by_version`. The audit was first a script in `tools/`; it moved into the package beside `show` and `sheet` so the review sheet can print it.

### 2. The planted count

**The new sampler** (`drill.planted_range`, `drill.plan`): the count is `lo + below(s, hi - lo + 1)`, drawn right after the condition, with `hi = min(floor(calls / 2), planted ids)` and `lo = min(max(ceil(3 * calls / 10), 5, shapes in the set), hi)`: 6 to 10 of 20, 5 of 10, 12 to 20 of 40. The dealing over the shapes is unchanged, so with 5 or more planted calls every shape is present and shape counts differ by at most one.

**The vectors were computed twice:** first by `indep_sampler2.py` in the scratch directory, stdlib only, written from the new pseudo-code without importing Polarizer; then by the code. Both printed:

- the stream's first values: 8468598625902157147, 15816047190215027138;
- a: `prediction-gate`, 9, `c05, c08, c06, p04, c02, c09, p06, c01, c12, p09, p08, p07, p10, c10, p05, p01, p03, c03, c07, c04`;
- b: `prediction-gate`, 9, `c03, c09, c05, c11, c10, c06, c12, c04, p04, p10, p07, p05, p02, p08, p03, c07, c02, p09, c08, p06`;
- c (seed `ff` times 16, 10 calls): `prediction-gate`, 5, `p06, p08, c10, p03, c06, c07, p04, c09, p10, c01`;
- d (seed `ff` times 16, 20 calls): `prediction-gate`, 6, `c04, c12, p05, c09, c06, p06, c05, c10, p04, c07, p08, c03, c01, c02, c08, c11, p03, p10`;
- the range for 10, 11, 12, 13, 14, 15, 16, 17, 20, 25, 30 and 40 calls: 5..5, 5..5, 5..6, 5..6, 5..7, 5..7, 5..8, 6..8, 6..10, 8..12, 9..15, 12..20;
- over the shipped set, seeds 0 to 20,200, until each condition had 10,000 drills: plain 1,961, 1,965, 2,083, 1,982 and 2,009 drills with 6, 7, 8, 9 and 10 planted calls; prediction gate 1,889, 2,020, 1,975, 2,072 and 2,044. In both, 100.00% had at least 5 planted and at least 10 clean calls, and none missed a shape.

The tests changed only after that comparison: `test_sampler_vector` (the four plans), `test_planted_range` (new), `test_planted_count_and_shapes` (2,000 drills in a row, each 6 to 10 planted with every shape and counts at most one apart, every count from 6 to 10 drawn).

**The intro** states the range, never the drawn count: `Between 6 and 10 of the 20 calls are planted ... The number changes from drill to drill, and real work has far fewer.` When the range is one number (10 calls: 5) it says `5 of the 10 calls are planted`.

**The test set** (`tests/helpers/drill_scenarios.json`) had 8 planted scenarios, so its range for 20 calls was 6 to 8, not the shipped set's 6 to 10. Two planted test scenarios were added (a draft sent instead of saved, a commit with an extra hook file), so it has two of each shape and the golden intro shows the shipped range. Set 1 is unchanged by this.

**The golden seed** is now `000...03ea`, the first counter seed whose plan on the test set draws plain, 8 planted calls, the README scenario at call 3, a look-alike at call 7 and a clean call at call 12, so the example end screen of section 5 (7 of 8, 1 of 12) is unchanged. Golden files changed (regenerated with `POLARIZER_UPDATE_GOLDEN=1`, each diff read): `drill_intro_plain.txt` and `drill_intro_prediction_gate.txt` (the range sentence), `drill_reveal_caught.txt` and `drill_reveal_over_300.txt` (the first caught call is now the new commit scenario). `test_review_sheet_lists_what_to_look_at` changed its task lines by position in the test set; it now sorts clean scenarios first, so a changed task never drops a planted call's cue.

**Docs changed for item 2:** MEASURE-SPEC.md sections 1 (Small n, the planted share, Counting down), 4 (Defaults, and why), 5 (the intro, and its one-number form), 6 (Why 150, Sampling's pseudo-code, bullets, vectors and distribution, Validation's new Shortcuts rules, Versioning), 9 (the report fixture's note), 12 (the test tables), 13 (the guide's items), 16 (questions 2 and 15) and 17 (Stage 8 follow-up, deviation 13); LIMITS.md's Drills section; DRILL-GUIDE.md; verified-facts.md (Stage 8 follow-up).

**Counting down, measured** (`drillnumbers.py` in the scratch directory, the shipped set, 10,000 seeded drills of 20): with the range 6 to 10, the count settles at least one answer (after 10 planted or 14 clean calls the rest are known) in 16.1% of drills, 0.26 calls per drill on average, at most 9. With a fixed 8 (the 2,002 of those plans that drew 8), every drill had such calls, 2.0 per drill on average, at most 11.

**Interval widths** for a catch rate from 6 to 10 planted calls, from `measure.rate_parts` over every k: 40 to 64 points (6), 36 to 60 (7), 33 to 58 (8), 30 to 56 (9), 28 to 54 (10); so 28 to 64 in all.

**Repeats** with the drawn count (2,000 drills in a row on one ledger, the last two excluded): a planted or a clean scenario comes back after 3 drills at the least, 6 at the median, 7.49 on average, as with a fixed 8.

### 3. The guide

docs/DRILL-GUIDE.md's "How to read the numbers" gains one sentence: `Always answering "allow" would be right about 60% of the time, so look at the two rates, not at the share of right answers.` The count is now `Between 6 and 10 of the 20 were changed to be wrong on purpose` and `One drill has only 6 to 10 planted calls`. 965 words in all (935 before). `tests/test_drill_guide.py` passes.

### 4. Runs

- **`scripts/test.sh`:** `1256 passed, 13 skipped in 264.55s (0:04:24)`, exit 0, 265 s wall (1251 before, plus `test_planted_range`, `test_load_by_version`, `test_no_shortcut`, `test_shortcut_checks_catch_a_tell` and `test_audit_features_and_buckets`).
- **The changed test files** (`tests/test_drill.py`, `test_scenarios.py`, `test_drill_guide.py`, `test_drill_report.py`; 425 tests), five times in a row: 425 passed each time, in 15.54 s to 16.63 s. A first try printed no summary line, because `-q` on the command line doubles the `-q` in pyproject.toml; it was run again without it.
- **Once under load,** with two busy-loop Python processes (`python3 -c 'while True: pass'`, PIDs 68633 and 68634, each at 99.9% CPU), started before and stopped by their PIDs after: 425 passed in 15.48 s.
- **Time bounds:** the new tests don't wait. `test_no_shortcut` draws 1,000 plans and `test_shortcut_checks_catch_a_tell` 50; the test file runs in about 5 s.
- **The review sheet** for the newest set, set 1: `uv run python -m polarizer.scenarios sheet --out drill-review/scenarios.md`, 3,997 lines, 109,493 bytes, ending with the shortcut audit and the planted count's distribution. Look at these: nothing.
- **The pre-push scan** (the owner's home path, the Windows users path, the Windows user name and five other words, case-insensitive), over all 371 files in the commit: 0 of each.
- **`scripts/dev/guard.sh check`** against `snapshot-20261005T184537Z.txt`, before the commit: no changes in any guarded repo or directory, the MCP config hashes in `~/.claude.json` unchanged (6 locations), and its size and mtime unchanged too; exit 0.
- **`~/.local/share/polarizer-drills`** was not created; every drill in this round ran in a temporary directory or drew plans only.
- **Not run:** anything on Windows or macOS; a person's drill under the drawn count; CI. Set 2 was not made (item 1).
