# Polarizer milestones

These are in build order. The cut line is everything above "Stretch". Sizes are rough: small is days, medium is one to two weeks, large is more. Details for M0 are in docs/m0-plan.md, for M1a in docs/PIN-SPEC.md, for M2a in docs/HOLD-SPEC.md, and the ledger format is in docs/LEDGER-SPEC.md.

The order puts M2b before the practice range, because the range's exfiltration cases need taint. M1b comes after M2b because scanning has the most prior art.

| # | Milestone | Done when | Size |
|---|---|---|---|
| M0 | Pass-through proxy and ledger v1 | Claude Code works through it with only prefixed names changed. Every call is in the ledger. Both verifiers agree on every fixture, each broken fixture gets its status, and the manual checklist is confirmed. | large |
| M1a | Pins | A tool is exposed only while its served definition matches an approved hash. Drift hides it, is recorded, and Claude Code is notified. Startup fails closed on a broken ledger. | medium |
| M2a | Classes and default holds | Every tool has a class in `polarizer.toml`. Writes outside the workspace roots or matching the hold patterns (resolved real paths), destructive calls and egress calls are held with a one-line reason, and `polarizer holds`, `allow` and `deny` decide them. Reads and in-root writes pass. Unclassified tools are held on every call. | medium |
| M3 | The card | Held calls appear on a local page showing what will change, the arguments, why it was held, its history and a recommendation written by code. Approve, deny, timeout and cancel each land in the ledger. Decisions (approve and deny) are fsynced before Polarizer acts on them; expiries and cancels are not, because they only make Polarizer do less (docs/HOLD-SPEC.md, section 5). | large |
| M5 + M6 | Stats and canaries | `polarizer stats` shows holds per session, approval rate, time per decision and the falling-time warning. Canaries are opt-in, capped, never forwarded (a test proves it), revealed after each decision, and scored for catches and false flags. The prediction-gate condition is in. | large |
| M2b | Argument rules and taint | Domain and value allowlists work. Session taint holds egress after untrusted content, with the reason naming the source and time. Grants are revoked on taint. Each has tests. | medium |
| M1b | Scanning and review | Named rules flag every poisoned fixture. False flags on the reference servers are counted and reported. The review UI shows diffs and group-approves unflagged tools. Tool `_meta` and upstream instructions are allowlisted and pinned. | medium |
| M4 | Practice range and eval suite | The range server's modes run (rug pull, poisoned and shadowing descriptions, injected results). Every case is tagged with its OWASP MCP ID. The table shows stop rates with Wilson intervals, a misses list and added latency, with auto mode on and off. | large |
| README | A real run | Numbers from one real session (calls, holds, decisions, catch and false-flag rates, added latency), the eval table, the threat model, the credits and how it was built. | small |
| Stretch | OpenTelemetry | Spans carry the ledger seq and hash, viewable in a local dashboard. The SDK's own middleware does most of it. | small |
| Stretch | OCSF export | `polarizer export --ocsf` maps ledger fields to OCSF names. | small |
| Stretch | Anchoring and signatures | A signed checkpoint (chain id, size, head hash) is anchored in a commit trailer, and verify reports the last anchored position. | medium |
| Stretch | Parallax and ISR adoption | Parallax gets a dual-version verifier and the identical `conformance/` folder. One Parallax task routes its MCP calls through Polarizer, and ISR names what the agents touched. This needs a separate, explicit prompt, run in its own session and branch with that project's tests. | large |

## Status

- **M0:** built in stages 1 to 3. The owner ran parts of its manual check interactively: Esc to cancel and the `/mcp` names (docs/verified-facts.md, Interactive and Manual check follow-up). The hung upstream and closing a session during a call are still unverified.
- **M1a:** built in stages 4 and 5 (docs/STAGE4-NOTES.md, docs/STAGE5-NOTES.md). It pins definitions only: no scanning, no policy, no holds. It is done when the owner's M1a manual check (docs/MANUAL-CHECK.md, M1a section) and CI on all three platforms pass.
- **M2a:** specified in docs/HOLD-SPEC.md, built in stages 6 and 7. Stage 6 (docs/STAGE6-NOTES.md): classes, the rule function, path rules, holds in serve with every ending but shutdown, `holds`, `allow`, `deny` and `--no-holds`. Stage 7 (docs/STAGE7-NOTES.md): session locks and `hold.abandoned` at restart, shutdown during holds, progress while held, `holds --wait` and `--bell`, classes in `pending` and `approve` with `--config`, and the example config checked against the pinned Filesystem server. **Done as of the owner's M2a manual check on Oct 5, 2026 (UTC),** run interactively with Claude Code 2.1.289 (docs/verified-facts.md, M2a check, interactive): a held write was denied and never reached the server, the same call asked again was a new hold and was allowed, a call held past two minutes was allowed and returned, and a destructive call expired after 30 s without running; the ledger verified intact. Stage 7's code passed CI at commit c986088 on all five jobs (Linux with Python 3.11, 3.12 and 3.13, Windows, macOS); the follow-up commit 74cedbe failed on macOS in a test of the check script (docs/STAGE7-NOTES.md, Follow-up: CI). Out of M2a's scope, and still to do: argument rules, session taint and grants (M2b), and the card (M3).

## Carry-forward notes

From the M1a spec round (docs/PIN-SPEC.md, section 8). They change no size or done-when.

- **M2b:** session taint must survive process restarts, because Esc kills the server process and a fresh process would otherwise start clean.
- **M3:** the approval page and pending holds cannot live only inside the stdio proxy process, since Esc terminates it.
- **M3:** Claude Code 2.1.289 moves a held call to the background after about 123 s and the agent keeps working (docs/HOLD-SPEC.md, section 9). Open question: should the card show that a held call has probably been moved to the background once its age passes about 120 s?
- **M5 and M6:** time per decision comes from monotonic elapsed values stored in the entries, not from `ts` differences.
