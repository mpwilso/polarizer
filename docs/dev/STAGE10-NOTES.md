# Stage 10 notes

Read after docs/dev/STAGE11-NOTES.md: stage 10 was named in docs/MEASURE-SPEC.md before M3 and built after it, by the owner's decision (section 16, question 6). This file records stage 10, live planted calls inside `serve` (docs/MEASURE-SPEC.md, section 11, with its tests in section 12), built on Nov 7, 2026 (UTC) on branch `stage10-planted` from e619536, master after M3's merge.

## Step 0

M3 merged first: CI run 42338768267 at e619536 passed on all five jobs on `m3-card`, the owner's `~/merge-branch.sh` (run from WSL as the brief says) fast-forwarded master to it, master's run 42399956899 passed, and the script deleted the branch. Section 11 was read against the code at e619536:
- **`proxy.Gateway._held`** writes `hold.created` from one dict, so `plant_commit` is one more key, and its handler learns the ending in two places (the wait, and the cancel path, `_cancelled`), which is where a real hold's reveal goes, before anything else is done for the hold.
- **The writer's conditional append** and **`_wake_holds`** work for any object in the gateway's table of open holds that has an `event`, so a planted hold can sit in the same table, count toward the cap of 16 and be woken by the same watch, without being a `_Waiter` with a request.
- **`HoldDecider.decide`** is shared by `allow`, `deny` and the card since M3, so the reveal wait is written once.
- **`stats.count`** folds every hold, so the exclusions are one split before it.
- **`tests/test_policy_config.py::test_policy_loaded_fields`** pins `policy.loaded`'s data exactly; it gains `planted_calls_per_session: 0`, as section 8 says every `policy.loaded` from stage 10 carries it.

## The build

- **`src/polarizer/planted.py`** (new): `Planter` (eligibility, the delay, the target, the verdict, the planted hold, its wait and expiry, the reveal), `Trigger`, the target lists and the reveal's texts. A Planter is built from the writer, the hold fold, the policy, the session, the gateway's table of open holds and two callbacks, and calls nothing of the MCP SDK; its task is started in the gateway's background task group, so shutdown cancels it, and it records `hold.expired` (shutdown) and its reveal in a shielded scope, best effort.
- **`src/polarizer/proxy.py`:** the planter, the start-up line, `_plan` after the rule function (a call refused for 16 open holds is counted but is no trigger), the trigger's seq from `call.sent` or `hold.created`, `plant_commit` and the salt on every real hold of a planting session, and `_reveal_real` after the ending in both places.
- **`src/polarizer/config.py`, `policy.py`:** the key, its error line, `Policy.planted_calls_per_session` (0 with holds off), the policy hash's form only above 0, and `policy.loaded`'s field.
- **`src/polarizer/holds.py`:** `Hold.plant_commit`, the `Reveal` fold kept beside the holds, `plant_commit()` and `reveal_checks()`.
- **`src/polarizer/decisions.py`:** the 2 s reveal wait, the planted lines and the no-reveal line; `reveal_out` lets the card take them apart.
- **`src/polarizer/card.py`:** the answer's `reveal`, the ended list's reveal, and history without revealed planted holds (docs/CARD-SPEC.md, section 8).
- **`src/polarizer/stats.py`:** `split` and the planted calls section.
- **Tests:** `tests/test_planted.py` (23), `tests/helpers/plantledger.py`, one more in `tests/test_card_check.py`, golden files `allow_planted.txt`, `deny_planted.txt`, `allow_real_planting_on.txt` and `stats_planted.txt`, and `valid/planted` from `tools/make_fixtures.py` (both verifiers find it intact).
- **`scripts/card_check.py --planted`** and **`polarizer.example.toml`**'s commented key with the three facts.
- **Docs:** docs/MEASURE-SPEC.md, section 17, Stage 10 build (the guesses); CARD-SPEC.md, section 8; LIMITS.md's Live planted calls section; README.md's Not built; milestones.md; CHANGELOG.md; docs/dev/README.md; docs/dev/MANUAL-CHECK.md's Stage 10 section; EVIDENCE.md; verified-facts.md.

**What failed while building, before any code changed for it:** the test file's own mistakes. A path `/elsewhere/x` is not absolute on Windows, so those calls were held by `path-unresolvable`; a `mkdir` lacked `parents=True`; a reading of every file under the ledger directory met Windows' lock on a session lock file's byte 0, and now allows only empty `.lock` files there; and the comparison of two folds needed their `ts` left out as well as their `seq`. The Bash tool also turned `\n` in two inline patches into real newlines (in `holds.py` and `scripts/card_check.py`); both were rewritten with the Edit tool, and no backslash went through an inline patch after that.

## The headless check

docs/verified-facts.md, Planted check, has all three attempts. The first found a real limit, now in LIMITS.md: a planted call comes 1 to 8 s after its trigger, and a headless run ends as soon as its last call returns, so its planted hold ended with the session before the card's next poll. The script's prompt now ends with a 20 s call to the probe's `wait`, which keeps the session open; nothing in Polarizer changed for it. The second attempt's one failure was the script expecting a timed decision for a planted hold; the third passed every check, at 0.25 USD for all three.

## Not run

- **The owner's interactive check** (docs/dev/MANUAL-CHECK.md, Stage 10): a person deciding a live planted call among real ones without knowing which it is.
- **A planted write on real Claude Code:** both planted calls seen were reads, from read triggers; writes are covered by the tests.
- **The guard,** for Stage 9's reason.

## Claims

| Claim | Checked by | Run |
|---|---|---|
| A planted hold is never forwarded, whatever its ending | `test_planted_call_is_never_forwarded` (allow, deny, expire, shutdown), `test_plan_reads_the_trigger_and_never_the_client`, the planted check | yes |
| Opt-in only; off without the key, at 0 and under `--no-holds`; the hash unchanged without it | `test_planting_is_opt_in`, `test_no_holds_records_zero`, `test_the_key_is_checked`, `test_policy_hash_unchanged_without_the_key` | yes |
| At most the limit per session, never with 12 open, only while the agent is active | `test_planted_cap_per_session`, `test_none_near_the_cap`, `test_planted_only_while_active` | yes |
| The planted hold is what the rule function gives its arguments | `test_planted_hold_uses_the_rule_function` | yes |
| Every hold of a planting session commits, is revealed once after its ending, and checks; the salt is never on disk before | `test_every_hold_commits_when_on`, `test_salt_never_written_before_reveal` | yes |
| No reader changes; allow, deny and the card print the reveal; stats counts it | `test_plant_commit_changes_no_reader`, `test_allow_and_deny_print_the_reveal`, `test_card_shows_a_planted_hold_like_a_real_one`, `test_stats_counts_planted` | yes |
| The stderr line is the same for planted and real holds | `test_planted_stderr_line_matches_real` | yes |
| One terminal entry per real held call with a planted hold in the run | `test_one_terminal_entry_per_call_with_planted` | yes |
| On real Claude Code, a planted call appears on the card, is denied and revealed | `scripts/card_check.py --planted` | yes, Claude Code 2.1.310 |
| A person decides one without knowing it is planted | docs/dev/MANUAL-CHECK.md, Stage 10 | no: the owner's to run |
| The suite passes on Linux, Windows and macOS | CI | see Runs |

## Runs

- **`scripts/test.sh` on Windows:** 1354 passed, 81 skipped in 344.05 s, with ruff, the format check and the docs check clean.
- **In WSL2:** the stage 10, card, hold and stats tests, 109 passed, twice.
- **The planted check:** passed on its third attempt (docs/verified-facts.md, Planted check).
