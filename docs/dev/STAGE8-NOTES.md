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
- **The Windows path:** this distro's registered name is in `$WSL_DISTRO_NAME`, so the sheet is at `\\wsl$\<distro name>\` followed by the repo's path with backslashes. `wsl.exe` is not on the PATH here, and `/etc/os-release` says Ubuntu 24.04, the release, not the registered name. In PowerShell, `wsl -l -q` lists the registered names.

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

## Drill usability round (Oct 5, 2026, UTC)

The owner's brief after a first drill run as a person who is not an engineer: they stopped after 3 calls, with no basis to judge calls they did not write, found the prediction step pointless, and could not see the value of doing it. Five items. Guard snapshot `snapshot-20261005T195857Z.txt` taken first. Written as the work goes.

### 0. The ledger format

Checked before building, as the brief asked: the guided condition needs no change to the ledger format, the hash chain, a status or an exit code. `guided` is a new value of `drill.started`'s `condition`, and `first-drill` a new value of its `condition_from` (a first guided drill is neither drawn nor flagged); no kind or field is added. LEDGER-SPEC.md's `drill.started` row now lists both fields' values. `--condition`'s usage line names guided, with the same exit 2.

### 1. The guided condition

**The lines.** `src/polarizer/scenarios/drill-set-1-plain.json` holds 150 lines, one per scenario, keyed by id, with `set` 1 and the set file's sha256; the set file and its pin are unchanged. They were written by hand in this session from a listing of each scenario's id, tool and compact arguments only (no answer, shape, task or reveal), so the wording could not follow the answer. Each names the action in everyday words and every file, place, recipient and value as the call has it.

**What changed after the first look.** The first draft explained some files ("the private login key", "the saved website logins", "a script git runs automatically before each push") and wrote a URL inside a message as "with a link to". Counting each word over the answers showed those words only on planted lines: such glosses go to the files planted calls touch, so they hinted. All file glosses were removed and message bodies quoted as they are (35 lines rewritten, the same rule applied to every line of that kind whatever its answer). Then the 8 push lines were changed from "Upload the branch" to "Push (upload) the branch", so a push names its tool as other lines do. The small test set got its own 26 lines (`tests/helpers/drill_scenarios-plain.json`).

**Rules, in the loader** (`scenarios.check_plain`, run by `scenarios.load` with the set, so a bad file is the set's one refusal line, exit 2): the format, the set version, `set_sha256` equal to the set file's, exactly one line per scenario and no other id; each line printable ASCII, 4 to 24 words, at most 200 characters, ending a sentence; none of the brief's seven words, as substrings in any case; none of twelve comparing words as whole words (`task`, `asked`, `instead`, `also`, `extra`, `another`, `different`, `however`, `actually`, `but`, `rather`, `only`), this round's addition. Set 1's lines are 6 to 24 words.

**The leak audit** (`polarizer.scenarios.audit`, `guided=True`): the call screen's features plus two of the plain line, its length in characters (under 70, 70 to 99, 100 to 124, 125 and over: edges set at the first draft's quartiles, 69.75, 102.5 and 123.25; the final lines' are 67, 96 and 118) and whether it shares a word with the argument that matters (a planted call's `plant.planted`, or its tool for a different tool; for a clean call the argument planted calls on its tool change most often, or its first argument). Set 1: no tell. Plain length: under 70, 17 planted and 26 clean; 70 to 99, 10 and 28; 100 to 124, 20 and 24; 125 and over, 13 and 12. Names the argument: yes, 60 and 89; no, 0 and 1 (s080, an empty list). Leave-one-out over 1,000 drills: plain length 52.0% (scenarios) and 51.5% (drills), naming 60.0% and 59.9%, always allow 60.0% and 59.9%; the best single-feature rule is still argument bytes, 64.0% and 64.2%, as on the call screen; naive Bayes 43.3% and 43.8%. Planted lines run a little longer (median 107.5 characters against 94), since they describe more arguments; no length bucket reaches a tell.

**The screen.** A guided call is the plain call with `In plain words: <line>` inserted after the block, before the blank line and `allow or deny? `. `test_guided_call_is_the_plain_call_and_one_line` checks for all 150 scenarios that taking that line out gives the plain screen byte for byte, and `test_drill_block_equals_holds_block`, unchanged, that the block is what `polarizer holds` prints.

**Kept apart.** The report gets a `guided:` block, `guided minus plain` and `guided minus prediction gate` (labelled differences with their Newcombe intervals and the noise phrase), and `by kind of planted call, guided` beside `by kind of planted call, plain and prediction gate`; with no guided drill the report is as before, so `drill_report.txt` is unchanged. The export is schema version 2: `conditions.guided`, `guided_difference` (`minus_plain`, `minus_prediction_gate`) and `guided_by_shape`; `difference` and `by_shape` keep their meaning over the other two conditions. Only the report's first two lines and the export's `drills`, `answered` and `repeats` count every drill. A new fixture, `fx.WITH_GUIDED` (a guided first drill before the seven, a flagged guided drill after them), gives `drill_report_guided.txt` and `drill_export_guided.json`.

### 2. A short first drill

**Ten calls.** `planted_range(10)` is 5 to 5: every drill of 10 has 5 planted and 5 clean calls and every shape. `test_ten_call_drills` checks 2,000 drills of 10 in a row with the shipped set, each leaving out the two before it: all had 5 and 5, all five shapes, nothing repeated. Such a drill's end screen and report give both rates with intervals, wide ones: 5 of 5 caught is 100%, 95% interval 56% to 100%; 4 of 5 is 80%, 37% to 97%; 0 of 5 false flags is 0%, 0% to 44%. Stopped before 5 of a kind, it says `too few to say a rate (5 or more needed)`.

**A first drill is guided.** Without `--condition`, a drill started when no drill in the ledger has an answer is guided, with `condition_from` `first-drill`, and its first screen says `This drill: guided, because it is your first.` A drill stopped before its first answer leaves the next one first (the report leaves such drills out too); counting any `drill.started` as a record would have taken the guided drill from a person who typed q at the first screen. Later drills draw plain or prediction gate as before. The plan's draw is made either way, so the order of calls doesn't change, `--seed` included.

**The suggestion.** A first drill of more than 10 calls shows, before its `This drill:` lines: `A first try can be shorter: type q now and run polarizer drill --calls 10, / which takes about five minutes.` `polarizer drill --help` now reads:

```text
  --calls N           calls in the drill, 10 to 40 (default 20; 10 for a first
                      try)
  --condition NAME    plain, prediction-gate or guided (default: guided for a
                      first drill, then plain or prediction-gate at random)
```

### 3. The first screen

The intro gains, after its title: `Why do this? People who approve what an AI assistant wants to do tend to / approve more as time goes on. A drill lets you see how well you are catching / its mistakes.` The privacy paragraph and the closing lines are unchanged. The claim rests on MEASURE-SPEC.md section 2's first source (a fatiguing, overloaded reviewer rubber-stamps); the secondary 93% figure stays out. The prediction prompt is the brief's sentence, wrapped after `do,` to fit 80 columns: `Before you see the call, write a few words on what you think it will do, / from the task alone. This is not graded. `.

**Golden files changed** (regenerated with `POLARIZER_UPDATE_GOLDEN=1`, each diff read): `drill_intro_plain.txt` and `drill_intro_prediction_gate.txt` (the `Why do this?` paragraph only), `drill_call_prediction_gate.txt` (the prompt only), `drill_refused_condition.txt` (the usage line), `drill_export.json` (format version 2, `guided` null, the two new keys). New: `drill_intro_first.txt`, `drill_intro_guided.txt`, `drill_call_guided.txt` (the plain call's golden plus one line, `diff` checked), `drill_report_guided.txt`, `drill_export_guided.json`. Unchanged: every other golden file, `drill_report.txt` and every `holds_*`, `allow_*` and `deny_*` file included.

### 4. The guide

docs/DRILL-GUIDE.md now says first who drills are for (people who approve what AI assistants do, such as developers, and anyone curious, with the guided version), recommends `polarizer drill --calls 10` as the first drill (about five minutes), explains the three conditions in two sentences, shows a guided call of 10 with its plain line, and reads the numbers of a drill of 10. About 1,000 words of prose (965 before). `tests/test_drill_guide.py` passes, with three changes that follow the guide: its one drill command is now `polarizer drill --calls 10`, the excerpt is compared with `drill_call_guided.txt`, and the pseudo-terminal run expects a guided drill of 10 (and answers the new prediction prompt, which a first drill doesn't show).

### 5. Tests changed, and why

- **`golden_drill`** passes `--condition plain`, since a first drill in a new directory is now guided; a later `--condition` still wins. Tests that ran a fresh drill and expected plain (`test_over_300_is_marked`, the stopped drill in `test_drill_golden`) pass it too.
- **`test_condition_random_and_flag`** became `test_condition_first_random_and_flag` (first drill, flag on a first drill, later drill, guided by flag, stopped-at-intro first drill, a first drill of 10).
- **`test_plan_is_reproducible_from_the_ledger`** expects its first drill guided (`first-drill`) and its second the plan's draw.
- **`test_drill_golden`** runs the plain and prediction-gate drills after an earlier answered drill, so their intros are the ordinary ones, and adds the first-drill and guided screens.
- **`test_review_sheet`** expects the plain line inside each scenario's screen and the guided screen's audit after the call screen's.
- **`check_schema`** in `test_drill_report.py` is schema version 2.

### 6. Runs

- **`scripts/test.sh`** on the finished tree: ruff check and format clean, `check_docs: 28 files, 0 findings`, `1270 passed, 13 skipped in 281.37s (0:04:41)`, exit 0, 282 s wall (1256 before this round). An earlier run, before the last comment and docs edits, gave the same counts in 274.40 s.
- **The new and changed test files** (`tests/test_drill.py`, `test_scenarios.py`, `test_drill_guide.py`, `test_drill_report.py`; 439 tests), five times in a row: 439 passed each time, in 22.16 s to 23.29 s.
- **Once under load,** with two busy-loop Python processes (`python3 -c 'while True: pass'`, PIDs 88774 and 88775, each at 99% CPU before and 99.9% after), started before and stopped by their PIDs after: 439 passed in 23.70 s.
- **Time bounds:** the new tests don't wait. `test_guided_call_is_the_plain_call_and_one_line` runs 300 two-call drills in process; `test_plain_words_leak_nothing` draws 1,000 plans; `test_ten_call_drills` 2,000.
- **The package:** an offline wheel build (`UV_OFFLINE=1 uv build --wheel`, into the scratch directory) holds `polarizer/scenarios/drill-set-1-plain.json` beside the set file.
- **The pre-push scan** (the owner's home path, the Windows users path, the Windows user name and five other words, case-insensitive), over all 378 files in the commit: 0 of each.
- **`scripts/dev/guard.sh check`** against `snapshot-20261005T195857Z.txt`, before the commit: no changes in any guarded repo or directory, the MCP config hashes in `~/.claude.json` unchanged (6 locations); its size and mtime changed (92461 to 92297 bytes, 20:33:21Z), which is Claude Code's own bookkeeping and informational; exit 0.
- **`~/.local/share/polarizer-drills` exists,** created at 19:17:27 UTC and last written at 19:52:36 UTC, before this session's snapshot (19:58:57 UTC): the owner's own first drill. This session did not open it or write to it; every drill here ran in a temporary directory. That ledger has answered calls, so the owner's next `polarizer drill` there is not a first drill and draws plain or prediction gate; `--condition guided` gives a guided one.
- **Not run:** anything on Windows or macOS; CI; a person's guided drill. The plain lines have been read by their author only; section 6's second-reader rule applies to them as to scenarios, and the review sheet now shows each line under its block for that.

## Release round (Oct 5, 2026, UTC)

Everything needed to make the repository public: a shape audit, a test for `mask_paths`, a CI install check, the README rewritten, the changelog, a public-readiness review, an install check and the runs. Guard snapshot `snapshot-20261005T222311Z.txt` taken first. Written as the work goes. CLAUDE.md rule 11 allows one commit per stage, so the round is one commit, which the brief allowed as an option.

### 1. Shape audit

**The rules.** MEASURE-SPEC.md, section 6, Shapes, now defines each shape by a mechanical test of a planted scenario's `intent` against its `call`, and an order for when two apply (different tool, extra effect, look-alike, misleading summary, changed argument). `src/polarizer/scenarios/shapes.py` holds them, read-only; `uv run python -m polarizer.scenarios shapes` prints the table. Set 1 and its pin are unchanged, and no set 2 was made.

**Set 1:** every planted scenario passes at least one rule; 9 of 60 labels differ from the shape the rules give: s028, s095, s115 and s134 (labelled misleading-summary, extra-effect by rule), s063 and s141 (misleading-summary, look-alike), s071 (changed-argument, look-alike), s107 and s135 (misleading-summary, changed-argument). Five of them differ under any order of the rules (s063, s071, s107, s135, s141); the other four come from the overlap of extra effect and misleading summary, and with misleading summary first they would match while s132 and s147 would not (7 differences). Only 4 of the 12 misleading-summary labels match. `test_shape_rules_on_set_1` holds the list.

**The three named in the brief** (s063, s141, s071: right comment, wrong ticket) all come out look-alike, because each ticket number is two edits from the named one (GP-44 and GP-19, HM-80 and HM-18, TT-150 and TT-105) and the rules use the look-alike validation's distance of 3. Had look-alike been kept to package and host names, all three would be changed-argument. Either way they share one shape, which their labels don't.

**The caveat,** in four places: each by-kind table of `drill report` ends with `  <n> drills in all. Shape labels are under review and each drill has only one or two calls per shape; read these counts across many drills, not from one.` (`drill.SHAPE_CAVEAT`; the export is unchanged); docs/DRILL-GUIDE.md, How to read the numbers; docs/LIMITS.md, Drills; MEASURE-SPEC.md, section 6 (with "Planned: set 2 with corrected labels" and the list) and section 9's report example. Golden files changed by that one line each, regenerated with `POLARIZER_UPDATE_GOLDEN=1` and each diff read: `drill_report.txt`, `drill_report_one_condition.txt` (7 and 3 drills) and `drill_report_guided.txt` (7 and 2).

**The repeat in the owner's report.** A copy of `~/.local/share/polarizer-drills/ledger.jsonl` and `ledger.head` (last written before this session's snapshot) was read in the scratch directory; the original was only read by `cp`, never written. `polarizer verify` on the copy: `intact: 55 entries`. The ledger holds four drills, not two:

| seq | condition | calls | shown | ended | excluded at start |
|---|---|---|---|---|---|
| 1 | prediction gate | 20 | s118 | interrupted, no answer | none |
| 5 | prediction gate | 20 | s089 | interrupted, no answer | s118 |
| 8 | prediction gate | 20 | s035, s130, s104 answered; s012 shown | stopped | s089, s118 |
| 23 | guided (flag) | 10 | 10 answered | finished | s012, s035, s089, s104, s130 |

The report leaves out drills with no answer (section 9), so it shows a stopped drill and a finished one. The exclusion counts the last two drills by `drill.started` "whatever their ending" (section 6, Sampling), which for the fourth drill were the second (s089) and the third. s118, shown at call 1 of the first drill, three drills back, was no longer excluded and was drawn again at call 6 (`seen_before` 1). The code does what the spec says; nothing was changed. The two rules disagree about which drills count: a drill stopped at its first screen uses up one of the two excluded drills without being reported. Excluding the calls of the last two drills that have an answer, or of every drill since the last two answered ones, would make the report and the exclusion agree; that is a decision for the owner, not a bug fix.

### 2. A test for `mask_paths`

`tests/conftest.py`'s `mask_paths` takes `sep`, defaulting to `os.sep`; every existing caller leaves it out, so they are unchanged. `tests/test_mask_paths.py` was written first and failed three of its four tests before the change (with no `sep` argument, `sep="/"` was taken as a placeholder named `sep`): Windows-style input with `sep="\\"`, POSIX-style input with `sep="/"`, a backslash escape elsewhere in the line and one after the path left alone, and the longest path replaced first with the default separator. All four pass on Linux; they need no Windows to run the Windows case.

### 3. CI: the install check

Two steps at the end of each of the five jobs in `.github/workflows/ci.yml`, after the existing ones, whose steps and pins are unchanged: `uv build --out-dir dist`, `uv tool install dist/polarizer-*-py3-none-any.whl` (the job's own tool directory; dependencies come from the package index, so this step uses the network), `uv tool dir --bin >> "$GITHUB_PATH"`; then, in the next step so the new path applies on every OS, `command -v polarizer`, `polarizer --help` and `polarizer drill --help`. GitHub's bash runs with `-e`, so any nonzero exit fails the job.

**What it proves that `tests/test_drill_guide.py` does not:** `test_guide_install_lines_run_offline` installs the wheel only when uv's cache already holds every package, and skips on a fresh runner, which is every CI job (CI run 37361705150). So until now no CI job had built the wheel and installed it as a reader would. The new step shows, on Linux, Windows and macOS, that the wheel builds, that its declared dependencies resolve from the index without `uv.lock`, that `uv tool install` puts a working `polarizer` entry point on the path (`polarizer.exe` on Windows), and that the installed package holds what `--help` and `drill --help` load. It does not run a drill or a server from the installed copy.

**Not run:** CI. Nothing in this session can run it; the step has been checked only by reading it and by item 7's local install, which used a virtual environment and uv's cache, not `uv tool install` from the index.

### 4. README

Rewritten in place, in the same family layout: the logo lockup, a centered hook (`Human in the loop only works if the human is still looking.`), the badge, three sentences on what Polarizer is, a status paragraph (v0.1 preview; built and not built; drills are practice with invented scenarios, and no results from people exist yet beyond the author's own tool checks), then the sections. New: "Try a drill" after "Why it exists", and "Related projects" after "How it works". The "How it works" image is replaced by a text diagram 79 columns wide; `docs/img/how-it-works-light.svg` and `-dark.svg` stay, unreferenced. Proof gains a drills bullet; its CI bullet now says to see the badge and cites 9270c97, the last commit recorded as passing on all five jobs (later runs on record failed: CI run 37259695458 at 03cedbd and 37361705150 at b495bf3, in this file and docs/verified-facts.md, and 37370711387 on Windows, named in commit 0f32e11's message). Known limits gains two one-liners; What's next is stats, the card, live planted calls, taint. 257 lines (212 before).

**Related projects** were recorded first, in docs/verified-facts.md, "Related projects (from the owner's research, October 2026, not re-checked in this repo)", from the brief's facts, with each link; nothing was fetched. The README section is written from that record. `test_related_projects_are_recorded` checks every link in the section is in it.

**`tests/test_readme.py`:** the layout test has the new hook, status line, section list, a 260-line limit, the logo, and no `how-it-works` reference with the SVGs still present; every `polarizer` command in any sh block or code span parses (the README's first draft named `polarizer stats`, which is not built, and this test caught it; the line now names the statistics without a command); the "Try a drill" commands run, the drill on a pseudo-terminal (POSIX) and the report without a terminal; every absolute link is a well-formed https URL, checked with `urllib.parse`, never fetched.

### 5. Changelog and the rest

CHANGELOG.md's 0.1.0 entry now names drills as the first slice of M5 and M6 and gains two sections: drills (the command, the three conditions with guided, the drill ledger, the report and export, set 1, the shortcut audit and the shape check) and tests on Windows and macOS (the golden-file paths, pseudo-terminal reads and bash 3.2, the guide's install test, the CI install check). SECURITY.md: read, nothing now untrue, unchanged. docs/dev/README.md already named STAGE8-NOTES.md; its line now also names the shortcut audit, the guided condition and this round. docs/EVIDENCE.md gains a drills entry, since the README's proof points to it for every claim.

### 6. Public-readiness review

**Scans,** case-insensitive for the words, as regular expressions for the token shapes (`ghp_` and 20 more, `github_pat_` and 20 more, `sk-` and 20 more, `AKIA` and 16, `BEGIN ... PRIVATE KEY`):
- the working tree: 0 for each of the brief's eight words (the owner's home path, the Windows users path, the Windows user name, a mail domain and four other names), and 0 for every token shape;
- every one of the 56 commits (`git grep` over `git rev-list --all`): 0 for all of those words and token shapes, with two known exceptions. The home-path form of the owner's user name, inside a Claude Code cache path, is on one line of docs/verified-facts.md in 32 commits, added at e642602 and removed at b9a4373; it is not in the working tree. `BEGIN OPENSSH PRIVATE KEY` is one line of tests/test_scenarios.py in 6 commits: a test that the credential check catches that header, not a key. The literal `BEGIN PRIVATE KEY`: 0 in every commit.

**Reading every tracked file** (greps over the tree, and a read-only review agent over docs/, manual/, scripts/ comments and the scenario files). Fixed, wording and placeholders only:
- this file: the scan's words were written out by name in a first draft of this section and are now described, as earlier rounds did; the WSL distro's registered name is now a placeholder; the owner's drill ledger is described without its size and hash;
- docs/MEASURE-SPEC.md, section 3's first line and section 13's: they named executives, hiring managers and a friend as readers, now "people who read this repository" and "anyone, engineer or not";
- docs/PROXY-SPEC.md, Where the ledger lives: the dev guard's list of the author's other projects' directories, a notes folder among them, is now "listed in the script", and the guard is described as a script for the author's own machine;
- manual/polarizer.manual.toml: a comment says its forbidden paths are the author's other repositories on the machine the checks ran on;
- docs/verified-facts.md, Unverified: "the repository is private" is now "was private when this was written".

**Kept, and reported:**
- **By design, as before:** CLAUDE.md and scripts/dev/guard.sh name the Parallax, Loupe and ISR repositories, Parallax's directories and a notes folder; they are the build's own guard rails. The manual check's config, docs/dev/MANUAL-CHECK.md and docs/dev/m0-plan.md list the same four repository paths (`tests/test_config.py` checks the manual config refuses them).
- **Explained where they appear:** Parallax in LEDGER-SPEC.md (the v0 format), PROXY-SPEC.md, HOLD-SPEC.md, MEASURE-SPEC.md and the code (its two always-forbidden directories); the README's family line links the three projects.
- **For the owner to decide:** docs/milestones.md's stretch row "Parallax and ISR adoption" is not explained to a stranger; "the owner" and "the brief" run through the specs (about 30 uses in MEASURE-SPEC.md, 17 in HOLD-SPEC.md, 12 in PIN-SPEC.md), defined once in verified-facts.md; docs/DRILL-GUIDE.md and MEASURE-SPEC.md, question 1, speak of sending a package file before the repository is public, which goes stale once it is; docs/dev/m0-plan.md and CLAUDE.md name docs/PLAN.md, which is not in the repository; docs/dev/PUSHING.md is the first-push checklist, with "Visibility: Private"; the build log records Claude Code API costs (cents per headless run), `~/.claude.json` sizes and npx cache ids; this file's step 1 mentions the computer shutting down; the README's and QUICKSTART-DRAFT.md's "independently of any employer system" is kept on purpose.
- **Nothing found:** other e-mail addresses (pyproject.toml has the noreply address), phone numbers, IP addresses, private hosts, real secrets. `manual/mcp.json` holds a real home path but is gitignored and untracked.

### 7. Install check

In the scratch directory, with `UV_OFFLINE=1`, so everything came from uv's cache and nothing was fetched: `uv build` wrote `polarizer-0.1.0.tar.gz` and `polarizer-0.1.0-py3-none-any.whl`; `uv venv --python 3.12` made a fresh virtual environment there, and `uv pip install` put the wheel in it (no global or tool install). With that environment's `polarizer`:
- `polarizer --help` and `polarizer drill --help`: exit 0, with the ten commands and the drill's flags as in item 4 of the usability round;
- `polarizer verify --ledger-dir <dir>` on a copy of `conformance/valid/session.jsonl` and `.head`: `intact: 13 entries, 1 sessions, 3 calls`, exit 0;
- one guided drill of 10 calls, `--condition guided` into a temporary ledger with a temporary home, driven on a pseudo-terminal by a script with a fixed rule (deny when the call's screen shows `.invalid`, `/.git`, `.ssh` or `.env`, else allow): exit 0, `Drill finished: 10 of 10 calls answered (guided).`, caught 2 of 5 (40%, 95% interval 11% to 77%), false flags 0 of 5 (0% to 44%); the ledger verified `intact: 33 entries`;
- `polarizer drill report`: exit 0, the guided block, the three "no drills yet" comparison lines, the by-kind table with `1 drill in all.` and the caveat, and one drill line;
- `polarizer drill report --export <path>`: exit 0, schema version 2, `guided_by_shape` with one planted call of each shape.

**Not removed:** the scratch directories (`install-check`, `drillcopy`) are outside this repository, and CLAUDE.md rule 10 allows deletes only inside it, so they were left for the owner, although the brief asked for their removal.

### 8. Runs

- **`scripts/test.sh`** on the finished tree: ruff check and format clean, `check_docs: 28 files, 0 findings`, `1281 passed, 13 skipped in 281.32s (0:04:41)`, exit 0, 285 s wall (1270 before this round, plus the 4 `mask_paths` tests, the 3 shape tests and 4 README tests). A run before the review's wording fixes gave the same counts in 281.83 s.
- **The new and changed test files** (`tests/test_mask_paths.py`, `test_readme.py`, `test_scenarios.py`, `test_drill_report.py`, `test_drill.py`, `test_drill_guide.py`, `test_config.py`; 497 tests), five times in a row: 497 passed each time, in 30.64 s to 31.57 s.
- **Once under load,** with two busy-loop Python processes (`python3 -c 'while True: pass'`, PIDs 209415 and 209416, each at 101% CPU before and after), started before and stopped by their PIDs after: 497 passed in 32.21 s.
- **Time bounds:** the new tests don't wait, apart from `test_try_a_drill_runs`, which answers each prompt as it appears (well under a second each) within 300 s for the whole drill.
- **`scripts/dev/guard.sh check`** against `snapshot-20261005T222311Z.txt`, before the commit: exit 1. No changes in any guarded repository (the ISR repository has no commit after 7b1df40), `~/.config/parallax` and `~/.claude/settings.json` unchanged, the MCP config hashes in `~/.claude.json` unchanged (6 locations); its size and mtime changed (94,699 to 92,274 bytes), Claude Code's own bookkeeping. Two tool directories changed: `~/.local/share/parallax` (87,318 files to 86,884, newest mtime 22:28:50 UTC) and `~/isr-notes` (in `real-run-3`, files written 22:31 to 22:43 UTC, among them an `isr-run` output folder). This session ran no Parallax, ISR or Loupe command and wrote nothing there; the changes match the owner's own concurrent work, which the brief said to expect.
- **`~/.local/share/polarizer-drills`** was only read, by `cp`, for item 1; every drill in this round ran in a temporary directory.
- **Not run:** CI; anything on Windows or macOS; a drill by a person.

## Release fixes (Oct 5, 2026, UTC)

Three fixes before the repository goes public. Guard snapshot `snapshot-20261005T231339Z.txt` taken first. Nothing was fetched and no model was run.

### Fix 1. README code fences

**What was found:** the Setup section's MCP config already had its opening `json` fence, in the working tree and at fd8b948 (README.md line 202), and the closing fence did not swallow the `claude` command. The brief described it as missing; nothing in README.md needed changing.

**The test, written first:** `fence_problems` in `tests/test_readme.py` pairs fence lines in order (three backticks after any list indentation): an opening fence may carry a language, a closing fence carries none, a fence with a language inside an open block is reported, an unclosed block is reported, and a block whose content starts with `{` must be labelled `json`. `test_readme_fences_pair_up` runs it on README.md and also on README.md with that opening fence removed, which it must flag; `test_docs_fences_pair_up` runs it on every Markdown file at the root and under docs/ (28 files). With the fence removed from README.md for the run, three tests failed: `test_readme_fences_pair_up` and `test_docs_fences_pair_up` with `line 212: ```sh inside the block opened at line 210`, and the existing `test_quickstart_mcp_config`, which found no json block. With the file restored, all passed. No Markdown file at the root or under docs/ fails the check, so no fence was changed. Two files have indented fences inside list items (docs/HOLD-SPEC.md and docs/dev/m0-plan.md); they pair up.

### Fix 2. The CI record

The latest commit recorded as passing on all five CI jobs is now 0f32e11, CI run 37380445365, as observed by the owner, with 1270 passed and 13 skipped in the local Linux run at that commit (docs/verified-facts.md, CI run 37380445365 at 0f32e11). README.md's Proof, docs/EVIDENCE.md (CI and Drills) and docs/milestones.md (a new CI line in Status, and the M1a and M2a lines) now give it. The earlier record, kept here as history: all five jobs passed at 9270c97 (as reported by the owner), with 784 passed and 13 skipped locally (docs/dev/STAGE7-NOTES.md, Follow-up: macOS output). docs/MEASURE-SPEC.md, section 17, item 8 still names 784 at 9270c97: it records what the README and EVIDENCE.md cited at 03cedbd during that spec round, not the latest run, so it was left.

### Fix 3. CI: the install step

`.github/workflows/ci.yml`'s install step no longer depends on uv's tool bin directory being on a runner's PATH. Both install-check steps name `shell: bash`. The first builds the wheel, requires exactly one `dist/polarizer-*-py3-none-any.whl`, gives it to `uv tool install` as a native path on Windows (`cygpath -w`), asks `uv tool dir --bin` for the bin directory, appends it to `$GITHUB_PATH`, and runs `polarizer --help` and `polarizer drill --help` by the executable's full path (`polarizer.exe` on Windows, with the directory converted by `cygpath -u` for bash). The second step runs `command -v polarizer`, `polarizer --help` and `polarizer drill --help` from PATH. `$GITHUB_PATH` applies only to later steps, so the PATH check stays a step of its own.

**What it proves:** the wheel built from the checkout installs as a uv tool, with its dependencies resolved from the package index, and the executable uv puts in its own bin directory runs `--help` and `drill --help` on Linux, Windows and macOS. The next step shows that adding that directory to PATH, as the README tells a reader, makes `polarizer` the command found there.

**Run locally, not in CI:** nothing in this session can run CI. The two steps' `run` blocks, taken from the edited ci.yml by a script, ran with `bash --noprofile --norc -eo pipefail` (GitHub's bash flags) in the scratch directory, on a `git archive` copy of HEAD, with `UV_OFFLINE=1`, `UV_TOOL_DIR` and `UV_TOOL_BIN_DIR` set to scratch directories, `GITHUB_PATH` a scratch file and `RUNNER_OS=Linux`. The bin directory was not on PATH before. Step 1: exit 0; it built the sdist and wheel, installed 30 packages and 1 executable from uv's cache, wrote the bin directory to the PATH file, and both full-path `--help` runs printed their usage lines. Step 2, with that line prepended to PATH as GitHub does: exit 0, `command -v` named the scratch bin directory's `polarizer`. Step 2 without it: exit 1. The wheel check, run alone on an empty `dist/`: exit 1 with `expected one wheel in dist/`. The Windows branch (`cygpath`, `polarizer.exe`) did not run anywhere, and nothing was installed from the package index. The user's own uv tool directory was not written.

### Fix 4. Runs

- **`scripts/test.sh`:** ruff check and format clean, `check_docs: 28 files, 0 findings`, `1283 passed, 13 skipped in 270.03s (0:04:30)`, exit 0, 276 s wall (1281 before, plus the 2 fence tests). Run before this section was written; after it, `scripts/check_docs.py` and the fence tests ran again (Fix 5).
- **Not run:** CI; anything on Windows or macOS.
- **A slip:** while making the scratch directory, `rm -rf repo` ran there, outside this repository, on a path that did not exist, and removed nothing. CLAUDE.md rule 10 allows deletes only inside this repository.

### Fix 5. After the notes

- **`scripts/check_docs.py`:** the first run after this section was written found one repeated heading (this round's runs heading had the release round's name), now renamed; then `check_docs: 28 files, 0 findings`. `tests/test_readme.py`: 14 passed.
- **The pre-push scan** of the working tree's tracked files, case-insensitive: 0 for each of the brief's eight words (the owner's home path, the Windows users path, the Windows user name, a mail domain and four other names).
- **`scripts/dev/guard.sh check`** against `snapshot-20261005T231339Z.txt`, before the commit: exit 1. No changes in any guarded repository, `~/.local/share/parallax`, `~/.config/parallax` or `~/.claude/settings.json`; the MCP config hashes in `~/.claude.json` unchanged (6 locations), and its size and mtime unchanged too. One tool directory changed: `~/isr-notes` (in `real-run-3`, a changed notes file, a screenshots folder and a new folder holding a git repository, written 23:15 to 23:21 UTC). This session ran no Parallax, ISR or Loupe command and wrote nothing there; the changes match the owner's own concurrent work, which the brief said to expect.

## Animated logo (Oct 6, 2026, UTC)

The lockups animate in place, with the same filenames, so README.md is unchanged; a still small mark is added. Guard snapshot `snapshot-20261006T000143Z.txt` taken first. Nothing was fetched and no model was run, apart from the README review's subagents (Cold-read review, below). The sibling projects' brand folders and READMEs were read as data for style, and not changed.

### The animation rule, as found

- **Polarizer, before this round** (`scripts/brand.py`, docs/brand/README.md, `tests/test_brand.py`): the band turned into place once, 1.6 s, `ease-out`, by CSS keyframes on an outer group; the inner group's SVG `transform` attribute is the finished turn, so a renderer without CSS animation shows the finished mark; `@media (prefers-reduced-motion: reduce){*{animation:none!important}}` stops it. No script. Files: the two lockups.
- **Loupe** (`brand/README.md`): "fade in once, one after another, and hold", and reduced motion shows "the finished mark straight away". Its files use CSS keyframes in the SVG's own `<style>`, the same reduced-motion rule, and no script, but as committed they repeat (`10s`, `infinite`: a short dip in opacity, then a hold), so its README and its files disagree; noted, not touched. File set: `mark.svg` (dark pages, animated), `mark-light.svg`, `mark-small.svg` ("32px and under. Still, cropped close": a `viewBox` of 8 8 104 104 at 32 by 32, and a solid stroke of 5 where the large mark has a dashed 2.5), the two lockups, `palette.json` and PNG exports.
- **ISR** (no brand README; `docs/brand/`): the two lockups only, CSS keyframes repeating every 6 s, the same reduced-motion rule plus a rule that hides its pings.
- **Parallax** (no brand README; `docs/brand/`): `lockup-animated-light.svg` and `-dark.svg`, `mark.svg`, `mark-animated.svg` and others, CSS keyframes repeating every 7 s, and a reduced-motion rule naming its own classes.
- None of the four uses SMIL or a script. Each README puts the light and dark lockups in a `<picture>` with a `prefers-color-scheme` source.

**The rule used here:** plays once and holds (Loupe's stated rule, and Polarizer's own); CSS keyframes in the file's own `<style>`, no script, no SMIL; every element's own attributes are the finished frame, so reduced motion (`*{animation:none!important}`) and a renderer without CSS animation show it at once; the file set is the two animated lockups and a still `mark-small.svg` for 32 px and under, as Loupe's. The mark's drawing, palette and meaning are unchanged.

### The build

`scripts/brand.py`: each parallel line and the band's two strokes get a class; `.draw` scales each from its own top (`transform-box: fill-box`, origin `50% 0`) from `scaleY(0)` in 0.4 s, `ease-out`, `backwards`, with delays 0.07 s apart in left-to-right order (the band's slot, x = 58, is sixth of nine); the band's outer group holds it back in line (`rotate(-28deg)`, `backwards`) until 0.96 s, when the last line is drawn, then swings it into place in 0.64 s, `ease-in-out`. Total 1.6 s (`TOTAL_SECONDS`). No element, attribute or color of the mark changed; the animation is only the `<style>` and the class attributes. "Swings from level" in the brief was read as from in line with the other lines, where the band was before this round, since that is what the mark means (a call that lined up, turning out of line); turning it from horizontal would have been a different drawing.

`small_mark()` draws `docs/brand/mark-small.svg`: 32 by 32, `viewBox` 2 2 96 96 (the rim's outer edge), the light palette, three lines at x = 26, 42 and 74 with a stroke of 6 in place of eight of 3, the band's place and 28 degree turn kept with strokes of 12 and a 20 halo, and a rim of 8. No `<style>`. The thinnest stroke is 2 px at 32 px.

Sizes: `lockup-light.svg` and `lockup-dark.svg` 1,934 bytes each (1,413 before), `mark-small.svg` 673 bytes.

**Tests, written first** (`tests/test_brand.py`): the files match the script and include the small mark; every file is ASCII, under 8,000 bytes and labelled; no file has a script element, a `javascript:` URL or an event attribute; each lockup has `@keyframes` and the reduced-motion rule; no animation repeats, and the latest delay plus the longest duration is under 2.5 s and equals `TOTAL_SECONDS`; the lockups' geometry (every element but `<style>` and `<title>`, attributes less class, fill, stroke, role and aria-label) equals `OLD_GEOMETRY`, taken from the files committed at 7afd4e1 before the change (identical in both themes); the small mark has no style or animation, is 32 by 32 with a cropped `viewBox`, keeps the band's turn and has no stroke under 1.5 px. Against the files at 7afd4e1, 3 failed (no small mark, no `TOTAL_SECONDS`, the script drew no small mark) and 5 passed, the geometry test among them; after the change, 8 passed.

### Looked at

Rendered with the headless Chromium shell already in the Playwright cache (build 1243), at device scale 2, into the gitignored `brand-preview/` with a throwaway script there.

- **Frames:** each lockup inline in a page, its animations paused with the Web Animations API at 0, 0.4, 0.8, 1.2 and 2.0 s, the light lockup on white and the dark on GitHub's dark background (`#0d1117`). At 0 s the face, rim and word only; at 0.4 s the first five lines part drawn and the band's first sliver; at 0.8 s every line but the last drawn and the band upright; at 1.2 s the band partway through its turn; at 2.0 s the finished mark. Nothing clipped or misaligned.
- **The finished frame is the old one, pixel for pixel:** the new lockups in an `<img>` with `--force-prefers-reduced-motion` gave PNGs byte-identical to the same render of the files at 7afd4e1, in both themes; so did the inline frame at 2.0 s against the 7afd4e1 files at 2.0 s.
- **The small mark** at 32 px on white and on dark, and at 16 px on white: the lines, the band and the rim read at both sizes.
- **Not checked this way:** the animation in an `<img>` over time. Chromium's `--virtual-time-budget` did not advance it (an `<img>` render of the 7afd4e1 file showed its first frame), so the frames come from the inline copies, which run the same CSS. How GitHub shows it can only be seen by pushing.

### Cold-read review of README.md

Six subagents, each started fresh with Claude Code's built-in subagent tool and run in parallel, with no tools allowed: five read README.md as pasted in their prompts (in two parts, the first 30 lines and the rest, with a note that it begins with a logo and has a text diagram under How it works), as a skeptical senior engineer, a non-technical professional, a VP, a recruiter and an applied-AI hiring manager; the sixth read docs/DRILL-GUIDE.md as the non-technical professional. None was told the project's history, its author's goals or the other readers. Each used one tool call, the hand-back of its report; none read a file or the network. Their answers are in the gitignored `readme-review/`, not committed. They are simulated readers from the same model family as the author's tools, share its blind spots, and are a proofreading aid, not evidence about real readers. The proposed README edits went to the owner in chat and were not applied; README.md is unchanged.

### Runs

- **`scripts/test.sh`** with `set -o pipefail`, before this section and the review were written: ruff check and format clean, `check_docs: 28 files, 0 findings`, `1288 passed, 13 skipped in 276.71s (0:04:36)`, exit 0, 279 s wall (1283 before, plus the 5 new brand tests). After this section, `scripts/check_docs.py` and `tests/test_brand.py` ran again.
- **Not run:** CI; GitHub's rendering of the animated lockups, which only a push can show; anything on Windows or macOS.
