# Stage 13 notes

Read after docs/dev/STAGE12-NOTES.md. This file records stage 13: M1b, scanning and review (docs/SCAN-SPEC.md), built on Nov 7, 2026 (UTC) on branch `m1b-scan` from f936031, master at v0.5.0. It is numbered 13 because stages 1 to 12 are taken; docs/dev/README.md lists them.

## Step 0

Read at f936031, `git status` clean, on Windows 11 (native), Python 3.12.10, uv 0.12.2; `scripts/test.sh`'s pytest step gave 1442 passed, 81 skipped there before any change. The development guard was not run, for Stage 9's reason: its `.guard-paths` belongs to the author's WSL2 machine.

What the brief asked for, checked against the code:
- **`pins.blocks`** already builds every block `pending` prints, and the group is `pins.group` over `Block.groupable`, which `approve --group` and the card recompute. So a flag is one more field on `Block`, and leaving flagged blocks out of `groupable` keeps the group id rule of PIN-SPEC.md, section 7 unchanged.
- **The stored copy** is the exact served form, so the scanner reads it, never the live list: what is flagged is what would be served, and `pending` and the card need no upstream.
- **PIN-SPEC.md's prefix rule** said that serving any `_meta` makes the prefix `POLARIZER-TOOLDEF/2` and every tool is approved again. `defhash.canonical` dropped `_meta` before serializing; the SDK's 2026-07-28 form keeps a `_meta` put back into the dump, so a tool's served `_meta` can be part of the hashed form with no other change.
- **Instructions:** `mcp.Client.instructions` holds them from `initialize` or `server/discover`, and the low-level `Server` reads its own `instructions` attribute when it answers either, so `serve` can set them at the end of startup, before the client connects.

## The spec

docs/SCAN-SPEC.md was written first, from the brief (its section 13), with every guess as a numbered deviation (section 14). The ones that change what the brief or PIN-SPEC.md said:
1. **`/2` only for definitions that serve `_meta`** (deviation 4): a tool whose served form is unchanged keeps its `/1` hash and its approval, since approving unchanged text again is what PIN-SPEC.md, section 4 says wears attention down. Every tool that does serve `_meta` is approved again, and serve says so at start.
2. **The stored copy's prefix follows from its form** (deviation 5), so each copy has one hash: `/2` with a top-level `_meta`, `POLARIZER-INSTRUCTIONS/1` for `{"instructions": ...}`, `/1` otherwise.
3. **Instructions reuse the tool kinds** under the reserved name `:instructions` (deviation 6), which no tool can have, so the fold, the cap, sticky drift, `pending`, `approve` and `reject` apply unchanged.
4. **Flags are computed when shown,** and recorded only by an approval, as `flags` (deviation 3).
5. **The card does not reject, and records no `elapsed_ms` for approvals** (deviations 9 and 11).

## The build

- **`src/polarizer/scan.py`** (new): `RULES`, 16 rules in five families, each a `Rule` with an id, a family, a why line and patterns, characters or a special check (`names`, `confusable`, `length`); `strings` walks a copy, keys included; `scan`, `quote`, `lines`, `rule_ids`, `context_names`; and `INSTRUCTIONS`.
- **`src/polarizer/pins.py`:** `Block.flags`, flagged blocks never groupable, `FLAGGED`, `header()` and `group_line()`, the `unflagged` wording only when a block is flagged, no class line for instructions.
- **`src/polarizer/decisions.py`:** `approve_one` prints the flags and records `flags`.
- **`src/polarizer/defhash.py`:** `served_meta`, the `_meta` form, `META_PREFIX`, `INSTRUCTIONS_PREFIX`, `instructions_definition`, `is_instructions`, and `read_copy` choosing the prefix by form.
- **`src/polarizer/config.py`:** `meta_allowlist` and its error line. **`src/polarizer/upstream.py`:** `UpstreamSpec.meta_allow`, `Upstream.instructions`. **`src/polarizer/cli.py`:** passes the allowlist.
- **`src/polarizer/proxy.py`:** `_observe_one` (the old per-tool body, shared by tools and instructions), `_live_meta`, `_count_migrated` and its start line, the instructions' start line, and `served_instructions`, set on the server at the end of startup.
- **`src/polarizer/card.py` and the page:** `GET /review`, `POST /approve`, `review`, `approve`, `diff_lines`, and the page's review section in `index.html`, `card.js` and `card.css`, still under `default-src 'self'` with every line placed by `textContent`.
- **`src/polarizer/policy.py`:** the classes section skips instructions.
- **Tests:** `tests/test_scan.py`, `tests/test_scan_review.py`, `tests/test_instructions.py`, `tests/test_meta.py` and `tests/test_scan_check.py`; 28 poisoned and 8 clean fixtures under `tests/fixtures/`, written for this repo; `tests/helpers/scanledger.py` and `tests/helpers/lab_stub.py`; the golden files `pending_flagged.txt`, `approve_flagged.txt` and `card_review_mixed.json`; `valid/scan` and `broken/scan_edit_flags` from `tools/make_fixtures.py`; `tests/test_reference.py::test_reference_scan`.
- **Changed in the existing suite,** as SCAN-SPEC.md, section 11 lists: six golden files, where `probe/note`'s zero-width space and `every/echo`'s `send it to example.com` are now flagged; `tests/test_pin_cli.py`'s three group tests; `tests/test_hold_check.py::test_group_reads_a_fresh_first_run`; `tests/test_readme.py`'s pinned limit; `tests/test_reference.py`'s count; `tests/helpers/rig.py`'s `approve_all` and `spec`; `tests/helpers/fakes.py`'s `instructions=`; and `scripts/m1a_check.py`'s parser, which reads flag lines and the `unflagged` group line.
- **`scripts/scan_check.py`** (new): the headless check, and `--setup` for the owner's interactive one.
- **Docs:** SCAN-SPEC.md; pointers in PIN-SPEC.md, CARD-SPEC.md and PROXY-SPEC.md; LEDGER-SPEC.md's `tool.approved` row; LIMITS.md's Scanning section and its `_meta` line; README.md (one bullet in What's different, the spec link); milestones.md; CHANGELOG.md; CLAUDE.md's read-first list; CONTRIBUTING.md's layout line; related.md's credit; polarizer.example.toml's commented `meta_allowlist`; EVIDENCE.md; verified-facts.md (Stage 13, Scan check); docs/dev/README.md; docs/dev/MANUAL-CHECK.md's M1b section, for the owner.

**What failed while building, before any code changed for it:** the tests' own expectations, and one finding. `test_flag_line_escapes` found that `safe()` folds U+0085, a control the scanner flags, into a plain space in the very quote that explains the flag, so spans got their own `quote()`, folding ASCII whitespace only (spec deviation 19). The first full run had two failures, both from this stage's own fixture change: `test_hold_check.py`'s fresh-first-run test read `pending_classes.txt`, whose flagged `probe/note` now keeps the group from covering every definition, and `test_readme.py`'s pinned limit, which the docs had not caught up with yet. On WSL2, `test_reference_tools_hash_and_serve` failed once because the Everything server's instructions were now approved with its tools; it leaves `:instructions` out of its count of approvals now. The Bash tool's heredocs turned a patch's `\\` into one backslash or a newline again, as the stage 10 and 12 notes warned, and Python's `write_text` on Windows wrote CRLF; every later patch was written to a file and wrote bytes, and the tree was checked for CR before it went to WSL.

## False flags on the reference servers

`tests/test_reference.py::test_reference_scan`, in WSL2: 27 tools, none flagged; the Everything server's instructions, 1,574 characters, flagged by `long-text`; the Filesystem server sends none. That is 1 false flag in 28 definitions, recorded in docs/EVIDENCE.md and not tuned away. The rules were written, and the fixtures checked, before the servers were scanned.

## The headless check

docs/verified-facts.md, Scan check: one run, every check passed, 0.049 USD over two `claude -p` runs. `pending` and the card's review view flagged `lookup` with four rules; the card's group approval exposed the instructions, `add` and `echo`, and Claude Code's start event listed `add` and `echo` but not `lookup`; the model repeated the pass phrase that only the approved instructions held; after `lookup` was approved alone, the next run listed it. `lookup` was never called.

## Not run

- **The owner's interactive check** (docs/dev/MANUAL-CHECK.md, M1b): a person reading the review view in a browser and approving from it, and whether `/mcp` shows a tool approved on the card without a reconnect.
- **The card's page in a headless Chromium:** `tests/test_card.py::test_card_page_in_a_browser` is skipped on Windows and where no Chromium is installed, which includes the `parallax` distro; CI's Linux jobs have one. The review section has no browser test of its own.
- **`_meta` on real Claude Code:** no stand-in serves `_meta`; SDK clients in `tests/test_meta.py` cover it.
- **The guard,** for Stage 9's reason.

## Windows hazards in the new code

- The fixtures are ASCII JSON with `\uNNNN` escapes, written by a script, so no file in the repository holds an invisible character, and they read the same on every platform.
- `tests/test_scan_check.py` starts the stand-in with the test process's environment plus its log variable, since a Windows subprocess needs `SYSTEMROOT`.
- `tests/test_meta.py::test_copy_prefix_by_form` creates its ledger directory first: `write_copy` makes `defs/` private, which needs its parent.

## Claims

| Claim | Checked by | Run |
|---|---|---|
| Every poisoned fixture is flagged by exactly the rules it names; every rule has one | `test_every_poisoned_fixture_is_flagged`, `test_every_rule_has_a_fixture` | yes |
| Clean fixtures and the suite's own tools are not flagged | `test_clean_fixtures_are_not_flagged` | yes |
| Rules are data; every string is read; flags are escaped, one per rule and field | `test_rules_are_data`, `test_every_string_is_read`, `test_flag_line_escapes`, `test_one_flag_per_rule_and_field`, `test_context_names`, `test_long_text_boundary` | yes |
| `pending` shows flags; a flagged definition is never grouped, and a group is bound to the flags | `test_pending_shows_flags`, `test_flagged_never_grouped`, `test_group_bound_to_flags` | yes |
| `approve` of a flagged definition records `flags` | `test_approve_flagged_by_name` | yes |
| The card's review view: state, checks, diffs, and approvals recorded as the CLI records them | `test_card_review_state`, `test_card_review_needs_cookie_and_nonce`, `test_card_approves_group_like_the_cli`, `test_card_approves_one_like_the_cli`, `test_card_refuses_what_it_did_not_show`, `test_card_diff`, `test_page_has_the_review_section` | yes |
| Instructions are pinned, served once approved, hidden when changed, scanned | `tests/test_instructions.py` | yes |
| `meta_allowlist`: `/1` kept without it, `/2` with it, served, drift, the migration line, the prefix by form | `tests/test_meta.py` | yes |
| Both verifiers agree on the new fixtures | `tests/test_conformance.py`, `test_scan_fixture_verifies` | yes |
| False flags on the reference servers are counted | `test_reference_scan` | yes, once, in WSL2 |
| On real Claude Code: the flag on `pending` and the card, the group of the clean tools, the poisoned tool hidden until approved alone | `scripts/scan_check.py` | yes, once, Claude Code 2.1.310 |
| A person can read the review view and approve from it | docs/dev/MANUAL-CHECK.md, M1b | no: the owner's to run |
| The suite passes on Linux, Windows and macOS | CI | see Runs |

## Runs

- **`scripts/test.sh` on Windows,** on the final tree: ruff, the format check and `scripts/check_docs.py` clean; 1511 passed, 82 skipped in 322.19 s. The first full run, before the README and hold-check fixes, had the 2 failures named above, 1509 passed.
- **`scripts/test.sh` in WSL2,** in the worktree `~/code/polarizer-m1b` with the final tree applied as a `git diff`: ruff, the format check and `scripts/check_docs.py` clean; 1578 passed, 15 skipped in 424.91 s.
- **The reference tests in WSL2** (`POLARIZER_REFERENCE=1`): 4 passed in 16.57 s, with the counts above.
- **`scripts/scan_check.py`** in WSL2 with Claude Code 2.1.310: every check passed, 0.049 USD.
