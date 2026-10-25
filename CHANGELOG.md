# Changelog

## Unreleased

- The manual check scripts (`scripts/rugpull-check.sh`, `scripts/m1a-check.sh` and `scripts/hold-check.sh`) no longer hang at exit when tee never sees end of input, which GitHub's macOS runners do about once in 11,000 runs of that plumbing: tee gets 10 s after the script closes its pipe, then is stopped. Measured before and after in one run of ten macOS jobs: 18 hangs in 203,256 runs without the change, 0 in 106,795 with it (docs/dev/macos-hang.md). A rug-pull check test that times out now prints the script's whole output and its processes.

## 0.3.0 (preview, released Oct 24, 2026)

- The README is restructured and about half as long: the setup moved to docs/SETUP.md, the directory listing and the test notes to CONTRIBUTING.md, the credits to docs/related.md, and the how-it-works picture replaces the text diagram. Its install lines name the newest release; `tests/test_readme.py` and `tests/test_drill_guide.py` check them against the newest released heading here, and `tests/test_readme_rules.py` holds the README to its length, dash, placeholder and wording rules.
- `polarizer init --from <path>` reads an MCP config in Claude Code's `mcpServers` shape and prints a polarizer.toml with its stdio servers as upstreams, and the `mcp.json` that starts `polarizer serve` with it; `--write <dir>` creates both files instead, never over an existing one. It never writes Claude Code or MCP config. A server it can't express, such as an HTTP or SSE server, is listed on stderr and in the file with the reason, and an `env` value that looks like a secret is written as a `"${NAME}"` placeholder that `serve` reads from its environment (docs/PROXY-SPEC.md, Starting from an existing MCP config).
- The README is shorter: its proof and limits are summaries, the example hold moved to docs/EVIDENCE.md, the related projects to docs/related.md, and the dropped limits to docs/LIMITS.md. Invariant's mcp-scan now links Snyk's agent-scan, where its repository redirects.
- The README shows a terminal picture of a held write to `.git/hooks` being denied, drawn by `scripts/demo.py` from a real run against the repository's test server; a test checks it against a fresh run.
- A release workflow checks that a published release's tag names the version in pyproject.toml and builds the wheel and sdist (docs/dev/releasing.md). Publishing to PyPI was set up in it, never used, and dropped; the README installs the wheel from the GitHub release.
- CLAUDE.md's read-first list is the milestones and the one spec a task touches, the machine-specific guard notes moved to docs/dev/guard.md, and a Testing section says pytest already sets `-q`.
- docs/dev/QUICKSTART-DRAFT.md and docs/dev/PUSHING.md are removed; both are readable at tag v0.2.0.

## 0.2.0 (preview, released Oct 11, 2026)

- `approve`, `reject`, `allow` and `deny` without a terminal refuse with `<command> needs a terminal: a person decides holds and approvals`. The line no longer names `--allow-no-terminal`, which told an agent, whose shell has no terminal, how to decide its own held call. The flag still works.
- A decision made without a terminal (`tool.approved`, `tool.rejected` or `hold.decided`, written with `--allow-no-terminal`) has `terminal: false` in its data. One made on a terminal has no `terminal` key, as before.
- The README's known limits say that a script with `--allow-no-terminal` can decide holds.
- Writes to a submodule's hooks or config, `.git/modules/**/hooks/**` and `.git/modules/**/config`, and to anything named `.git` at any depth (a gitfile can point git at another directory's config and hooks), are held by the built-in write list, as writes to `.git/hooks` and `.git/config` already were. The built-in list's version in the policy hash goes from 1 to 2, so every existing config's `policy_sha256` changes once.
- CI runs with a read-only token, cancels a branch's older run when a newer push arrives, and runs pull requests only from forks, since a branch push already has its own run. Dependabot proposes GitHub Actions updates monthly, a week after each release.

## 0.1.0 (preview, released Oct 6, 2026)

The first preview. It covers three milestones, M0, M1a and M2a, and drills, the first slice of M5 and M6. Taint (M2b), an approval card (M3), statistics over real holds and live planted calls are not built. docs/milestones.md has the plan.

### M0: pass-through proxy and verifiable ledger

- `polarizer serve` is a stdio MCP server that starts the upstream servers in `polarizer.toml`, exposes each upstream tool as `<prefix>__<tool>`, and forwards calls with their arguments unchanged. Tools only: resources, prompts and completions are not exposed, and requests from an upstream to the client are not forwarded.
- Every call is recorded in a hash-chained ledger (docs/LEDGER-SPEC.md): RFC 8785 canonical entries, a chain id bound in the genesis entry, `ledger.head` to catch a lost tail, and arguments kept out of the chain in salted side files that the chain commits to.
- `polarizer verify [--args]` reports one status with its own exit code (`intact`, `tampered`, `invalid`, `not canonical`, `torn tail`, `truncated`), and never writes. `polarizer repair` removes a torn tail and nothing else.
- A second verifier, `conformance/reference_verify.py`, written separately from the same spec, and generated conformance fixtures; a differential test checks that the two verifiers agree. Both were written by the same builder, so a misreading of the spec that both share is not ruled out; the RFC 8785 test vectors, written out by hand from the RFC, check the canonical bytes independently.
- The ledger directory defaults to `~/.local/share/polarizer` (0700, files 0600) and is refused inside any `ledger_forbidden_paths` entry.

### M1a: pins

- No tool reaches the agent until its definition is approved. `polarizer pending` prints each waiting definition in full, with unusual characters escaped; `polarizer approve` approves one definition by hash or a group exactly as printed; `polarizer reject` needs a reason.
- The agent is served only the stored, approved copy. A changed definition hides the tool, is recorded as `tool.drift`, and Claude Code is told the tool list changed. A hidden tool called by name is refused and recorded.
- A running `serve` notices an approval within about a second. Startup fails closed on any ledger status but `intact`.
- `approve` and `reject` refuse to run without a terminal unless given `--allow-no-terminal`.

### M2a: classes and holds

- Each tool gets a class in `polarizer.toml` (`local-read`, `local-write`, `destructive`, `open-world`, `egress`) and optional `path_args`. Paths are resolved (symbolic links, `..`) and compared with `workspace_roots` and the hold patterns, built in and configured.
- Writes outside the workspace roots or matching a hold pattern, destructive and egress calls, and calls to unclassified tools are held. A held call waits inside `serve` until `polarizer allow` or `polarizer deny`, or is refused after `hold_timeout_seconds` (300 by default). Every hold, decision and outcome is in the ledger.
- `polarizer holds` lists waiting calls with their exact arguments; `--wait` waits for one and `--bell` rings the terminal.
- A restarted `serve` records the open holds a dead process left behind. Shutdown during a hold refuses the call. Progress is sent while a call waits.
- `serve --no-holds` turns the hold rules off for one process. It exists only as a flag and is recorded in the ledger at every start.

### Drills (M5 and M6, first slice)

- `polarizer drill` runs an offline practice session (docs/MEASURE-SPEC.md, docs/DRILL-GUIDE.md): invented held calls, one at a time under the task the agent was given, some planted to be wrong, each allowed or denied and then revealed. 10 to 40 calls (20 by default); a drill of 20 has 6 to 10 planted calls, drawn per drill, and one of 10 has 5. The plan is drawn from a seed by SHA-256, so `--seed` repeats a drill on any machine; without it, the calls of the last two drills are left out.
- Three conditions, kept apart in every result: plain, prediction gate (write what you expect before you see the call) and guided (a hand-written line in plain words under each call, from `drill-set-1-plain.json`). A first drill is guided.
- Drills keep their own hash-chained ledger, by default in `~/.local/share/polarizer-drills`; `serve` refuses a ledger with drill entries.
- `polarizer drill report` gives each condition's catch rate and false-flag rate with Wilson 95% intervals, differences between conditions with Newcombe intervals, counts by kind of planted call (with a caveat line: set 1's shape labels are under review), repeats, and one line per drill. `--export <path>` writes a summary with counts, rates and dates only: no typed text, paths, names, ids or times of day.
- Scenario set 1: 150 scenarios, 60 planted (12 of each of five shapes) and 90 clean, pinned by sha256. A shortcut audit (`python -m polarizer.scenarios audit`) checks that what a call's screen shows before the answer gives little away: no feature value has 90% or more of one answer, and the best single-feature rule scores 64.0% over the scenarios and 64.2% over 1,000 seeded drills, against 60.0% and 59.9% for always answering allow. The same audit runs over the guided screen. A shape check (`python -m polarizer.scenarios shapes`) defines each shape by a mechanical rule and finds 9 of set 1's 60 labels differ from it; a set 2 with corrected labels is planned.

### Tests on Windows and macOS

- Golden files compare the same on every OS: the test helper `mask_paths` writes the separators after a placeholder as `/`, with a test that runs the Windows and POSIX cases on every platform.
- Pseudo-terminal reads accept end of file as well as EIO, as macOS gives; the hold-check script waits for `tee` on bash 3.2, as macOS ships.
- The guide's install test no longer depends on the local uv cache: an offline dry run decides whether to run it or skip, naming the missing package.
- CI builds the wheel, installs it with `uv tool install` and runs `polarizer --help` and `polarizer drill --help` on all five jobs.
