# Evidence

What has been checked, and where each result is recorded. [README.md](../README.md#what-was-measured) condenses it, and this page holds the example hold the README used to show. Nothing below has been run on native Windows or macOS with Claude Code, with any other client, or with a third-party MCP server other than the reference servers named.

- **CI.** Five jobs: Linux (ubuntu-24.04) with Python 3.11, 3.12 and 3.13, and Windows and macOS with Python 3.12. All five passed at commit 7afd4e1, in CI run 37390364747, the latest run recorded as passing on all five ([docs/verified-facts.md](verified-facts.md#ci-run-37390364747-at-7afd4e1-observed-by-the-owner)), and at d50da88 before it, in CI run 37388909612 ([docs/verified-facts.md](verified-facts.md#ci-run-37388909612-at-d50da88-oct-5-2026-utc-observed-by-the-owner)); see also [docs/milestones.md](milestones.md#status). On Linux, `scripts/test.sh` ran ruff, the docs check and 1283 tests passed, 13 skipped, at d50da88; 7afd4e1 changed only two docs files after it, and no local run at 7afd4e1 is recorded. The per-job test counts in CI are not recorded.
- **Two verifiers.** Polarizer's verifier and the reference verifier, `conformance/reference_verify.py`, agree on every conformance fixture, and on 5,000 randomly damaged chains per test run; locally, also on 20,000 cases each for seeds 1 to 5, with no disagreement ([dev/m0-plan.md](dev/m0-plan.md#tests-and-claims)). The two were written separately from the same spec, by the same builder, so a misreading of the spec that both share is not ruled out. The RFC 8785 test vectors, written out by hand from the RFC's text, are an independent check of the canonical bytes both rely on ([verified-facts.md](verified-facts.md#rfc-8785-text-fetched-oct-2-2026), `tests/test_rfc8785_vectors.py`).
- **Reference servers.** The pinned Everything and Filesystem servers (2026.8.31) ran through Polarizer locally: all 27 tools hashed, and each reached clients exactly as its stored copy ([docs/verified-facts.md](verified-facts.md#stage-4-oct-3-2026)).
- **Headless live check, M0.** `scripts/live-check.sh`, run once with Claude Code 2.1.288 and `claude -p`: a 14 s call under `MCP_TOOL_TIMEOUT=5000`. Claude Code cancelled it 5.012 s after the call, Polarizer's own cancel reached the upstream 1 ms later, and the ledger verified intact with 6 entries and the call recorded `cancelled` ([docs/verified-facts.md](verified-facts.md#live-check-headless-claude-code-21288)).
- **Esc, interactive, M0.** With Claude Code 2.1.288, Esc during a 30 s call: the upstream received a cancel, and the ledger recorded `call.sent` and `call.returned` `cancelled` at seq 21 and 22 ([docs/verified-facts.md](verified-facts.md#esc-during-a-long-call-interactive-claude-code-21288)).
- **Rug-pull check, headless, M1a.** `scripts/rugpull-check.sh` passed twice with Claude Code 2.1.289, nine of nine checks each time: once from inside a Claude Code session, at commit a46cbc8 ([first run](verified-facts.md#rug-pull-check-oct-4-2026-headless-claude-code-21289)), and once from a plain terminal with no `CLAUDE_*` or `ANTHROPIC_*` variables set ([second run](verified-facts.md#rug-pull-check-second-run-oct-4-2026-headless-plain-terminal-claude-code-21289)). In each, run A called the tool; in run B the server changed the definition, `tool.drift` was recorded at seq 24, the agent was not offered the tool, and nothing reached the server; after the approval at seq 26, run C's call went through (seq 30 and 31). Each ledger verified intact with 32 entries. It does not show the refusal of a hidden tool called by name (the model never tried; tests cover it).
- **M1a check, interactive.** With Claude Code 2.1.289, approving a first group and then a changed definition while a session was open: `/mcp` listed the tools again without a reconnect both times, and the ledger verified intact with 53 entries. Its seq ranges are not recorded ([docs/verified-facts.md](verified-facts.md#m1a-check-interactive-oct-4-2026-observed-by-the-owner)).
- **M2a check, interactive.** With Claude Code 2.1.289 at commit c986088, from two terminals: a write to `.git/hooks` was held and denied, and the file was not created (seq 52 to 54); the same call asked again was a new hold, allowed, and written (seq 55 to 58); a write held for 128 s was allowed and returned, after Claude Code reported moving it to the background at 123 s (seq 59 to 62); and a `move_file` held as destructive expired after 30 s without running (seq 68 to 70). The ledger verified intact with 71 entries in 3 sessions ([docs/verified-facts.md](verified-facts.md#m2a-check-interactive-oct-5-2026-utc-observed-by-the-owner)). The other two of a hold's five endings, the client's cancel and Polarizer's shutdown, are covered by tests: `tests/test_holds.py::test_client_cancel_ends_the_hold`, and `tests/test_hold_restart.py::test_esc_during_a_hold` and `::test_stdin_closed_during_a_hold`.
- **Drills, as a tool.** The drill tests run in all five CI jobs (the pseudo-terminal ones on Linux and macOS only), and passed in all five at 7afd4e1 (CI run 37390364747) and at d50da88 (CI run 37388909612); see the badge for later runs. The shortcut audit of scenario set 1, using only what a call's screen shows before the answer, finds no feature value with 90% or more of one answer, and the best single-feature rule (argument length) right 64.0% of the time over the 150 scenarios and 64.2% over 1,000 seeded drills, against 60.0% and 59.9% for always answering allow ([docs/MEASURE-SPEC.md](MEASURE-SPEC.md#validation), [stage 8 notes](dev/STAGE8-NOTES.md#1-the-shortcut-audit)). The shape check finds 9 of the 60 planted scenarios labelled with a shape their mechanical rule doesn't give ([docs/MEASURE-SPEC.md](MEASURE-SPEC.md#shapes)). No drill results from people other than the author's own tool checks exist.

docs/verified-facts.md also lists what is not verified, such as how Claude Code ends a backgrounded held call when the session exits.

## The hold example

In the interactive M2a check, I asked Claude to write a file into `.git/hooks`, and the call was held. In a second terminal, `polarizer holds --wait` printed:

```
holds: 1 open

hold a81e649da6775874 fs__write_file local-write
held by write-pattern: argument "path": /tmp/polarizer-manual/.git/hooks/hold-check-c2713114.txt matches .git/hooks/**
waiting about 0m00s; times out after 300 s
session cbb8b462c2196ca7 started 2026-10-05T00:34:04.198Z, running
args_commit f5b482532450b8990343ca1f2cd273725f97bf77ee142f0053e9f106cdad3617, 103 bytes of arguments
{
  "path": "/tmp/polarizer-manual/.git/hooks/hold-check-c2713114.txt",
  "content": "M2a hold check c2713114"
}
```

I denied it, and the file was never written. The same call, asked for again, was a new hold, and I allowed that one. The check script's summary of the ledger for the two holds:

```
seq 52 hold.created: fs__write_file, class local-write, held by write-pattern: argument "path": /tmp/polarizer-manual/.git/hooks/hold-check-c2713114.txt matches .git/hooks/**
seq 53 hold.decided: deny (M2a hold check: deny)
seq 54 call.refused: hold a81e649da6775874 was denied

seq 55 hold.created: fs__write_file, class local-write, held by write-pattern: argument "path": /tmp/polarizer-manual/.git/hooks/hold-check-c2713114.txt matches .git/hooks/**
seq 56 hold.decided: allow
seq 57 call.sent: fs__write_file, allowed_by hold
seq 58 call.returned: outcome ok, latency_ms 36
```

For the denied call, the agent only ever saw `polarizer: fs__write_file was not allowed`. Both blocks are copied from the check's results file, as recorded in [docs/verified-facts.md](verified-facts.md#the-deny-and-allow-steps-as-printed).
