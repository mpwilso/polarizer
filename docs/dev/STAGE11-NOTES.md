# Stage 11 notes

Read after docs/dev/STAGE9-NOTES.md. This file records stage 11: M3, the card (docs/CARD-SPEC.md). It is numbered 11 because stage 10, live planted calls inside `serve` (docs/MEASURE-SPEC.md, section 11), was named in the measure spec before M3 and is built after it, by the owner's decision; its notes will be docs/dev/STAGE10-NOTES.md.

## Step 0

Read on Nov 7, 2026 (UTC) at 38d9438 (v0.4.1), `git status` clean, on Windows 11 (native), Python 3.12.10, uv 0.12.2, after `uv sync --locked`. `tests/test_holds.py`, `tests/test_hold_cli.py` and `tests/test_stats.py` passed first (63 passed, 1 skipped), to see that the suite runs here. The development guard was not run, for Stage 9's reason: its `.guard-paths` belongs to the author's WSL2 machine.

What the brief asked for, checked against the code:
- **`decisions.HoldDecider.decide`** is the whole of `allow`'s and `deny`'s steps 4 to 7, already separate from the command line, so the card calls it unchanged and passes `elapsed_ms` as one new optional argument. Nothing else about `hold.decided` changes, and `serve` reads none of its other fields.
- **`holds.block`** (which calls `holds.render_block`) renders a hold exactly as `polarizer holds` prints it, side file check included, so the card's exact-call box is that function's output.
- **`stats.pattern_of` and `stats.is_builtin`** already read a built-in pattern from a hold's reason, for stage 9's rule that built-in patterns never get a suggestion; the card's recommendation uses the same two functions, so the two rules can't drift apart.
- **`stats` already prefers `elapsed_ms`** (stage 9, `test_stats_prefers_a_monotonic_elapsed_ms`); the card's new end-to-end test checks it from a real card decision.
- **No hold entry records the resolved paths,** so the card resolves them itself (CARD-SPEC.md, section 14, deviation 10).

## The spec

docs/CARD-SPEC.md was written first, from the owner's answers (its section 13), with every guess as a numbered deviation (section 14). The ones that change what the brief said:
1. **The cookie is not the token** (deviation 1). Browsers send a host's cookies to every port of it (RFC 6265, section 8.5), so a cookie that held the token would hand it to any other local server the browser visits, an agent's development server among them. The cookie is a second random value that opens nothing alone.
2. **The page always needs the token** in its query, a reload included (deviation 2), and **the state needs the nonce too** (deviation 4), for the same reason.
3. **`card` needs a terminal** unless `--allow-no-terminal` is given (deviation 6), since an agent that started it would read the token.
4. **`elapsed_ms` starts when the card first sends a hold** in a state answer (deviation 7), and a decision for a hold the card never sent is refused (deviation 8).
5. **The recommendation also says deny** for a destructive or egress call whose reason names a built-in pattern (deviation 9).

## The build

- **`src/polarizer/card.py`** (new): the rows, the recommendation, the paths, the ended texts, the `Card` (secrets, nonces, the read of the ledger, the shown times, deciding), the HTTP handler with section 3's checks in order, a server bound to 127.0.0.1 without `SO_REUSEADDR` (and with `SO_EXCLUSIVEADDRUSE` on Windows), and `run`. The handler's `log_message` writes nothing.
- **`src/polarizer/card_page/`** (new package data): `index.html`, `card.js` and `card.css`. No inline script or style, no `on...=` attribute, no `innerHTML`; text is placed with `textContent`.
- **`src/polarizer/decisions.py`:** `HoldDecider.decide` takes `elapsed_ms`.
- **`src/polarizer/cli.py`:** `card` with `--config` or `--ledger-dir`, `--port`, `--allow-no-terminal`, the location check, and `card.run`.
- **`tests/test_card.py`** (new, 32 tests), **`tests/helpers/cardledger.py`** (the fixture, from named pieces), **`tests/test_card_check.py`** (3), and the golden files `card_state_mixed.json` and `card_usage.txt`.
- **`scripts/card_check.py`** (new): the headless check, manual only.
- **Docs:** CARD-SPEC.md; LEDGER-SPEC.md's `hold.decided` row; HOLD-SPEC.md's open question marked decided; PROXY-SPEC.md's command pointer; LIMITS.md's held-calls line and a Card section; EVIDENCE.md; README.md's Try it and Not built; milestones.md; CHANGELOG.md; CLAUDE.md's read-first list; CONTRIBUTING.md's layout line; docs/dev/README.md; docs/dev/MANUAL-CHECK.md's M3 section, for the owner; verified-facts.md (Stage 11, and Card check).
- **`tests/test_readme.py`'s pinned limit** `There is no page or notification for holds yet.` now pins `There is no notification for holds outside a terminal or the card`: the card made the old limit false, and LIMITS.md says a limit leaves when the code changes it.

**The tests passed on their first run** with the golden files written, apart from the test file's own mistakes, fixed before the code ran: a temporary directory named with `<` and `>` (not allowed on Windows), a tamper that edited text living in a side file rather than the ledger, and a restore that put back the ledger from before a repair, which `ledger.head` then reads as truncated. The full suite then failed in two documentation tests: a JSON block in CARD-SPEC.md not labelled `json` (`test_docs_fences_pair_up`), and the pinned limit above (`test_limits_and_evidence_moved`). Both were fixed in the docs and the one test.

## Not run

- **The owner's interactive check** (docs/dev/MANUAL-CHECK.md, M3): a person reading the page in a real browser while Claude Code waits. This is what M3's done-when needs, and it is the owner's to run.
- **The browser test on Windows or macOS,** and on CI: it needs a headless Chromium, which CI's runners don't have, so it skips there. It ran in WSL2.
- **The guard** (Step 0).

## Windows hazards in the new code

- `SO_REUSEADDR` on Windows lets another program bind a port already in use, so the card turns it off and asks for `SO_EXCLUSIVEADDRUSE`.
- Ctrl+C on a native Windows console ends `serve_forever` through Python's own `KeyboardInterrupt`, since the server polls every 0.25 s; SIGTERM exists only on POSIX, where the card handles it. The subprocess test sends both signals on POSIX and only terminates the process on Windows.
- The paths the card prints are native: the tests build each expected line with `paths.resolve`, so they compare on every platform.

## Claims

| Claim | Checked by | Run |
|---|---|---|
| One open line, loopback only, `--port` honored, exit 0 on SIGTERM and SIGINT | `test_open_line_and_loopback_only`, `test_card_with_config` | yes |
| The token, cookie, nonce, Host, Origin and content type are each required, and compared in constant time | `test_token_required`, `test_cookie_is_not_the_token`, `test_secrets_compared_in_constant_time`, `test_host_header_checked`, `test_state_needs_cookie_and_nonce`, `test_decide_needs_post_origin_and_json` | yes |
| Every answer carries the policy headers; no request log | `test_headers_on_every_answer`, `test_no_request_log` | yes |
| A card shows section 4's rows, notes, recommendation and block | `test_card_state_golden`, `test_card_rows`, `test_rejected_definition_and_history_counts`, `test_background_note_at_120_s`, `test_paths_with_config`, `test_paths_through_a_link`, `test_recommendation_rule` | yes (the link case in WSL only) |
| A card decision equals the CLI's, but for `elapsed_ms`, and is refused with the CLI's lines | `test_allow_through_card_matches_the_cli`, `test_card_refusals_are_the_cli_lines`, `test_card_and_cli_race`, `test_not_shown_is_refused`, `test_malformed_decisions` | yes |
| `elapsed_ms` is monotonic, fsynced before the answer, and preferred by `stats` | `test_elapsed_ms_is_monotonic`, `test_decision_is_fsynced_before_the_answer`, `test_stats_uses_the_card_time` | yes |
| Endings leave the queue; a broken ledger shows its problem | `test_endings_leave_the_queue`, `test_state_when_not_intact` | yes |
| The page obeys the policy and works in a browser | `test_page_obeys_the_policy`, `test_page_names_the_ledger_and_escapes_it`, `test_card_page_in_a_browser` | yes (the browser in WSL only) |
| On real Claude Code, a held write is denied and then allowed through the card | `scripts/card_check.py` | yes, once, Claude Code 2.1.310 |
| A person can read and decide on the page | docs/dev/MANUAL-CHECK.md, M3 | no: the owner's to run |
| The suite passes on Linux, Windows and macOS | CI | see Runs |

## Runs

- **`tests/test_card.py` on Windows:** 30 passed, 2 skipped (the link case and the browser test); in WSL2, on a copy of the tree: 32 passed in 6.85 s.
- **`scripts/card_check.py`** in WSL2 with Claude Code 2.1.310: every check passed, 0.032 USD (docs/verified-facts.md, Card check).
- **`scripts/test.sh` on Windows** before the docs fixes: 1 failed (`test_docs_fences_pair_up`), 1324 passed, 81 skipped in 264.28 s. After them, with `tests/test_card_check.py` added: ruff, the format check and `scripts/check_docs.py` clean, 1328 passed, 81 skipped in 273.62 s.
