# Stage 12 notes

Read after docs/dev/STAGE10-NOTES.md. This file records stage 12: M2b, argument rules and taint (docs/TAINT-SPEC.md), built on Nov 7, 2026 (UTC) on branch `m2b-taint` from ef6b516, master after stage 10's merge.

## Step 0

Read at ef6b516, `git status` clean, on Windows 11 (native), Python 3.12.10, uv 0.12.2. The development guard was not run, for Stage 9's reason: its `.guard-paths` belongs to the author's WSL2 machine.

What the brief asked for, checked against the code:
- **`policy.evaluate`** already had the deny, hold and allow tiers in its docstring and none in its body, so the deny tier is one more step after the class, and `Verdict.action` gains `"deny"`.
- **`proxy.Gateway._forward`** is the one place a forwarded call's answer returns to the client, for calls allowed at once and from a hold, so tainting goes there, before the answer and before the first relayed progress, and `_held` passes the verdict's class to it through `_Waiter`.
- **`holds.HoldState`** is fed every entry during the startup verification pass and every catch-up afterwards, so the taint fold goes in it: serve knows its taint at start with no second read, `holds`, `allow`, `deny`, the card and `stats` get it from the fold they already build, and two serves of one Claude Code session share it at the next catch-up.
- **HOLD-SPEC.md, section 7's session locks** prove that a serve process ended but not which Claude Code session started the next one. So before the spec was written, a headless probe on real Claude Code 2.1.310 looked at a stdio MCP server's environment: `CLAUDE_CODE_SESSION_ID` is the session's id, and a server started for `claude -p --resume` gets the same value (docs/verified-facts.md, Taint session id). That is the client session.
- **MEASURE-SPEC.md, section 11:** a planted call's trigger is never egress, and its tool is the trigger's, so taint never changes a planted verdict; a trigger the deny tier refuses had to be passed to the planter as refused.

## The spec

docs/TAINT-SPEC.md was written first, from the owner's design (its section 14), with every guess as a numbered deviation (section 15). It is a file of its own rather than a section of HOLD-SPEC.md, as CARD-SPEC.md is for M3 (deviation 1). The ones that change what the brief said:
1. **The taint line shows on every hold of a tainted session,** in `holds` and on the card, not only on egress holds (deviation 8): the card's exact-call box is `holds`' block, and it keeps planted holds, never egress, from standing out.
2. **Taint is recorded at the first upstream content the client could see,** the first relayed progress or the answer, whatever its outcome (deviation 9).
3. **An answer is withheld when its taint can't be recorded** (deviation 10).
4. **Rule `tainted` only when taint alone held the call;** other egress holds of a tainted session keep their rule with the taint as the first part of the reason (deviation 12).
5. **IDNA is handled by holding non-ASCII hosts,** entries in `xn--` form only (deviation 5), and Polarizer reads URL hosts with its own parser (deviation 4).
6. **Nested argument names stay out** (deviation 2), though HOLD-SPEC.md's decision 3 had put them in M2b.

## The build

- **`src/polarizer/argrules.py`** (new): `host_of` (section 3's table), `listed`, `domain_problem`, `url_hold`, `value_match` and the strings a value is searched as.
- **`src/polarizer/taint.py`** (new): `Taint` and its text, the client session from the environment and the two start-up warnings, and the fold's key.
- **`src/polarizer/config.py`:** `url_args`, `domains` and `deny_values` in each tool entry, with their error lines; `ToolRule` gains the three fields.
- **`src/polarizer/policy.py`:** the deny tier, the egress rows with domains and taint, rules `deny-value`, `domains` and `tainted`, the taint prefix on egress reasons, the hash form with the keys only when set, `client_session` in `policy.loaded`, and `has_domains`.
- **`src/polarizer/holds.py`:** the fold of `policy.loaded`'s client session and the first `session.tainted` per key (`taint_of`, `tainted_sessions`), and the taint line in a hold's block.
- **`src/polarizer/proxy.py`:** the client session, the start-up lines, `taint`, `_taint` (one at a time, shielded, fsynced, withheld on failure), the deny tier's `call.refused`, the taint before the answer and the first progress in `_forward`, and the planter told of deny-tier refusals.
- **`src/polarizer/writer.py`:** `session.tainted` in SECURITY_KINDS. **`src/polarizer/cli.py`:** `serve` reads `CLAUDE_CODE_SESSION_ID`.
- **`src/polarizer/card.py`:** the `Session taint` row and the recommendation for `tainted`. **`src/polarizer/stats.py`:** the taint section.
- **Tests:** `tests/test_argrules.py` (61, with its parametrized cases), `tests/test_taint.py` (19), `tests/test_taint_check.py` (4), `tests/helpers/taintledger.py`, `tests/helpers/web_stub.py`, the golden files `holds_tainted.txt` and `stats_tainted.txt`, and `valid/taint` and `broken/taint_edit_source` from `tools/make_fixtures.py` (both verifiers agree on them in `tests/test_conformance.py`).
- **Changed in the existing suite,** as section 12 of the spec says: `test_policy_loaded_fields` pins `client_session: null`, and `tests/test_readme.py`'s pinned limit names only scanning.
- **`scripts/taint_check.py`** (new): the headless check, and `--setup` for the owner's interactive one.
- **`polarizer.example.toml`:** a commented web server with `fetch`, `post_comment`, `url_args`, `domains` and `deny_values`, which `test_example_config_loads` uncomments and loads.
- **Docs:** TAINT-SPEC.md; pointers in HOLD-SPEC.md, PROXY-SPEC.md, PIN-SPEC.md, CARD-SPEC.md (the recommendation row) and MEASURE-SPEC.md; LEDGER-SPEC.md's kinds and fsync policy; LIMITS.md's Argument rules and taint section; SETUP.md's line on egress; README.md (the "Not built" sentence replaced, one bullet in What's different, the spec link); milestones.md; CHANGELOG.md; CLAUDE.md's read-first list; CONTRIBUTING.md's layout line; EVIDENCE.md; verified-facts.md (Taint session id, Stage 12, Taint check); docs/dev/README.md; docs/dev/MANUAL-CHECK.md's M2b section, for the owner.

**What failed while building, before any code changed for it:** the test files' own mistakes. A TOML case repeated `url_args` in one inline table, which TOML refuses before Polarizer reads it; `test_taint_reason_prefix` called a tool `post` that the fake upstream doesn't have; and two cases in the first full run failed because the example config and the README's pinned limit had not been written yet. The Bash tool turned an inline patch's `\\` into something else, as stage 10's notes warned, so every later patch was written to a file first and run from there. The `parallax` distro mounts no Windows drive (`automount` is off), so the tree went to WSL as a tar stream.

## The headless check

docs/verified-facts.md, Taint check: one run, every check passed, 0.127 USD over three `claude -p` runs. Run 1's post to the listed host ran as a grant, the fetch tainted the session at seq 15 before its answer, and the same post was held by `tainted` naming seq 14 and allowed through the card. Run 2, `--resume` of the same session, started a new serve that held the post by `tainted` at once, with the same source, and it was denied through the card. Run 3, a new session, posted as a grant. Before the spec, a probe of three runs (0.083 USD) found the session id in a stdio server's environment (docs/verified-facts.md, Taint session id).

**After the check,** one change: `argrules._strings`, which lists the strings `deny_values` searches, walks a value with a stack of its own instead of recursion, so an argument nested deeper than Python's recursion limit is still searched (`test_deny_tier_in_the_rule_function` nests one 5,000 deep). The check never exercises `deny_values`, so its result stands.

## Not run

- **The owner's interactive check** (docs/dev/MANUAL-CHECK.md, M2b), and with it the restart that follows Esc: whether interactive Claude Code passes the same `CLAUDE_CODE_SESSION_ID` to the serve it starts after Esc. The headless check used `--resume`, which starts a new serve in the same session.
- **`deny_values` and the tricky hosts on real Claude Code:** tests only.
- **The guard,** for Stage 9's reason.

## Windows hazards in the new code

- Path arguments in the taint tests are built from the resolved temporary directory with the platform's separator, and the policy's home is `paths.split` of it, so the `~/.ssh/**` pattern matches on every platform.
- `test_client_session_from_environment` starts `serve` as a subprocess with an environment built without `CLAUDE_CODE_SESSION_ID`, so a run inside Claude Code, which sets it, doesn't change what the test sees.
- `tests/test_taint_check.py` reads the script's paths with `str()` of the repository's paths, never as POSIX text, so it compares on Windows.

## Claims

| Claim | Checked by | Run |
|---|---|---|
| Each bad key has its error line, exit 2, no upstream started | `test_argrule_errors` | yes |
| The policy hash is unchanged without the keys | `test_policy_hash_unchanged_without_the_keys` | yes |
| URL hosts are read and matched as section 3 says | `test_host_reading_holds`, `test_host_reading_accepts`, `test_domain_matching`, `test_domain_entry_problem` | yes |
| An allowlisted egress call runs as a grant; tricky hosts are held and never reach the server | `test_allowlisted_egress_runs`, `test_tricky_hosts_are_held`, `test_egress_rows` | yes |
| A deny_values match is refused at once and recorded with its rule and reason | `test_deny_tier_in_the_rule_function`, `test_deny_values_refuse_and_record` | yes |
| An open-world call taints; the next egress call to a listed host is held with the source in its reason | `test_open_world_taints_and_egress_is_held`, `test_every_answer_taints`, `test_taint_reason_prefix` | yes |
| The taint entry is written before the answer and before the first progress; an unrecordable taint withholds the answer | `test_taint_written_before_the_answer`, `test_taint_before_first_progress`, `test_taint_withheld_when_unrecordable` | yes |
| Taint survives a serve restart in the same session, not a new one; two serves share it | `test_taint_survives_restart_same_session`, `test_taint_without_client_session`, `test_client_session_from_environment`, `test_two_serves_share_taint` | yes |
| What does not taint | `test_what_does_not_taint` | yes |
| holds, the card and stats show taint | `test_holds_shows_taint`, `test_card_shows_taint`, `test_stats_counts_taint` | yes |
| Planted calls keep MEASURE-SPEC.md, section 11 | `test_planted_calls_with_taint`, `tests/test_planted.py` unchanged | yes |
| Both verifiers agree on the new fixtures | `tests/test_conformance.py`, `test_taint_fixture_verifies` | yes |
| On real Claude Code: a grant, then a hold by taint naming the source, decided on the card; a resumed session's new serve holds at once; a new session runs | `scripts/taint_check.py` | yes, once, Claude Code 2.1.310 |
| Taint survives the restart after Esc in an interactive session | docs/dev/MANUAL-CHECK.md, M2b | no: the owner's to run |
| The suite passes on Linux, Windows and macOS | CI | see Runs |

## Runs

- **In WSL2,** on the copy of the tree: the stage 12, planted, card, hold, stats and conformance tests, 314 passed in 49.92 s.
- **`scripts/taint_check.py`** in WSL2 with Claude Code 2.1.310: every check passed, 0.127 USD.
- **`scripts/test.sh` on Windows,** on the final tree: ruff, the format check and `scripts/check_docs.py` clean; 1442 passed, 81 skipped in 271.90 s. An earlier run, started while the README was still being edited, had 1 failed (`test_readme_passes_every_rule`, for the word "only" in a new sentence, since reworded), 1441 passed.
