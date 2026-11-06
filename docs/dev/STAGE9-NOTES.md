# Stage 9 notes

Read after docs/dev/STAGE8-NOTES.md. This file records stage 9: `polarizer stats`, statistics over real holds, the second slice of M5 and M6 (docs/MEASURE-SPEC.md, section 10). Stage 10, live planted calls inside `serve`, stays deferred until after M3 by the owner's decision (section 16, question 6), and nothing of it is built here.

## Step 0

Read on Nov 5, 2026 (UTC) at fad32da on a fresh clone, `git status` clean, on Windows 11 (native), Python 3.12.10, uv 0.12.2, after `uv sync --locked`. `tests/test_measure.py` and `tests/test_holds.py` passed first (53 passed), to see that the suite runs here. The development guard was not run: its `.guard-paths` is gitignored and belongs to the author's WSL2 machine (docs/verified-facts.md, Stage 9).

What section 10 asked for, checked against the code:
- **`holds.HoldState`** already folds each hold's ending as section 10 defines a state (the first `hold.decided`, `hold.expired` or `hold.abandoned`, a decision with the wrong `args_commit` ignored), so `stats` reuses it rather than folding holds again. What it drops and `stats` needs, each `hold.decided`'s `ts` and optional `elapsed_ms` and the order sessions started, a second small fold keeps beside it.
- **`measure.py`** had every formula: `rate_parts` for the approval rate, `newcombe` for the trend, `median_low`, and `stats_seconds`, written in stage 8 for this stage and unused until now.
- **`cli.py`** gives every ledger command the same `--config` or `--ledger-dir` check and the same usage line, so `stats` joined that list and got section 9's usage line for free.

## The build

- **`src/polarizer/stats.py`** (new): `StatsFold`, `count` (a hold to its state and time), the line builders for each section of the output, `suggestions`, `trend`, `lines` and `run`. `run` reads the ledger as `holds` does: `read_files`, `LOCKED_LINE` and exit 7, `no ledger at <dir>` and exit 2, a status's lines and code when not intact.
- **`src/polarizer/cli.py`:** `stats` in the list of ledger commands and one dispatch line.
- **`tests/helpers/statsledger.py`** (new): the fixture of section 10 from named pieces, one function per hold with a docstring giving its row of the table; `build` for any list of `(ts, kind, data)`; `allowed_run` and `two_windows` for the threshold and trend ledgers.
- **`tests/test_stats.py`** (new): the ten tests of section 12's stage 9 table, plus the extras listed in section 17, Stage 9 build, 8. 30 tests.
- **Golden files:** `stats_mixed.txt`, `stats_nothing.txt`, `stats_suggestion.txt`, `stats_trend_warning.txt`, `stats_trend_no_warning.txt`, `stats_tampered.txt`, `stats_invalid.txt`, `stats_torn_tail.txt`, `stats_truncated.txt`, `stats_no_ledger.txt`, `stats_locked.txt`. The six broken-ledger and missing-ledger files equal their `holds_` twins, and a test says so.
- **Docs:** README.md's Limits (the not-built list loses stats; the People limit says what `stats` measures and what it can't tell apart), docs/LIMITS.md's People section likewise, docs/milestones.md's status, CHANGELOG.md, docs/PROXY-SPEC.md's command pointer, CONTRIBUTING.md's layout line, docs/dev/README.md, docs/MEASURE-SPEC.md section 17 (Stage 9 build) and docs/verified-facts.md (Stage 9).

**The fixture matched on the first run.** `test_stats_fixture` compares the output with section 10's text copied into the test, as `tests/test_drill_report.py` does for section 9's report, and it passed the first time the test file ran, with 25 of the file's other tests. The two that failed then were the test's own: a config written in the wrong shape (`[upstreams.fs]` for `[upstream.fs]`), and a check that the golden-update variable was unset, which the golden-writing run itself had set; the first was corrected and the second dropped.

## Guesses

Each is recorded in docs/MEASURE-SPEC.md, section 17, Stage 9 build:
1. a pattern rule's suggestion is counted per pattern, not per tool and rule, since the line names a pattern;
2. a decision whose `ts` doesn't parse is counted and left out of the medians;
3. a session with holds and no `session.started` is listed last, as `started unknown`;
4. no planted-calls handling until stage 10;
5. the wording of the note for decisions timed with `elapsed_ms`;
6. singular forms of the counts;
7. per-entry timestamps in the fixture helper;
8. the extra tests.

## Not run

- **A real ledger.** No serve ledger with holds exists on this machine; `stats` ran on built fixtures only. The owner's M2a check ledger (docs/verified-facts.md, M2a check, interactive) would be the first real one.
- **Linux and macOS.** Nothing in this stage ran there before the push; CI runs the five jobs.
- **The guard** (Step 0).

## Windows hazards in the new code

- `stats` prints the ledger directory as the operating system gives it, with backslashes on Windows; the golden file holds `<dir>`, masked by `mask_paths` as the other golden tests do.
- The fixture's `ts` strings are fixed text, so no clock, time zone or locale is involved; `datetime.strptime` on them is the same everywhere.

## Claims

| Claim | Checked by | Run |
|---|---|---|
| Section 10's fixture prints its expected output exactly | `tests/test_stats.py::test_stats_fixture` (the spec's text in the test) and `stats_mixed.txt` | yes |
| Time to decision rounds as section 10 says, and a stepped clock gives 0 | `test_time_to_decision_rounding` | yes |
| The approval rate counts decided holds only | `test_approval_rate_counts_decisions_only` | yes |
| A suggestion needs 20 of 20; built-in patterns and fail-closed rules never get one | `test_suggestion_threshold`, `test_each_rule_has_its_line`, `stats_suggestion.txt` | yes |
| The trend needs 80, fires on section 10's numbers, and not when the rate fell or the floor isn't met | `test_trend_needs_80`, `test_trend_warning`, `test_trend_no_warning`, `test_trend_windows_are_the_last_by_decision_seq` | yes |
| Other kinds change nothing | `test_stats_ignores_other_kinds` | yes |
| `stats` is read-only and creates no lock file | `test_stats_is_read_only` | yes |
| A monotonic `elapsed_ms` is preferred when present | `test_stats_prefers_a_monotonic_elapsed_ms` | yes |
| Every `stats` row of section 9's golden table but the stage 10 row | `test_stats_golden`, `test_stats_locked` | yes |
| `--config` is read with `require_env=False` and nothing is started | `test_stats_with_config` | yes |
| The usage line is section 9's | `test_stats_usage` | yes |
| The suite passes on Linux, Windows and macOS | CI | see Runs |

## Runs

- **`tests/test_stats.py` with the golden files written** (`POLARIZER_UPDATE_GOLDEN=1`): 26 passed, 2 failed in 3.99 s, the two being the test's own mistakes (The build, above). After the fix: `tests/test_stats.py`, `test_golden.py`, `test_readme_rules.py` and `test_source_ascii.py` together, 153 passed in 14.25 s; `ruff check` and `ruff format --check` clean on all 154 files; `scripts/check_docs.py`: 33 files, 0 findings.
- **`scripts/test.sh`** on Windows 11, Python 3.12.10, at the stage 9 tree before the commit: 1 failed, 1292 passed, 79 skipped in 262.45 s. The failure is `tests/test_drill_guide.py::test_guide_install_lines_run_offline`, which runs `uv pip install --dry-run` offline and skips when uv says packages were unavailable because the network was disabled; uv 0.12.2 on this machine, whose cache has no pydantic 2.14.1, says `No solution found when resolving dependencies` instead, so the test fails rather than skips. **Reproduced on master** at fad32da with the stage 9 files stashed: the same test, run alone, 1 failed in 2.27 s. It is not touched by this stage, and CI's runners (which have the cache from their own `uv sync`) are the check that matters; see the CI run below.
