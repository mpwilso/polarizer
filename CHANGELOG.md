# Changelog

## 0.1.0 (preview, not yet tagged or published)

The first preview. It covers three milestones: M0, M1a and M2a. Taint (M2b), an approval card (M3), and oversight statistics and canaries (M5 and M6) are not built. docs/milestones.md has the plan.

### M0: pass-through proxy and verifiable ledger

- `polarizer serve` is a stdio MCP server that starts the upstream servers in `polarizer.toml`, exposes each upstream tool as `<prefix>__<tool>`, and forwards calls with their arguments unchanged. Tools only: resources, prompts and completions are not exposed, and requests from an upstream to the client are not forwarded.
- Every call is recorded in a hash-chained ledger (docs/LEDGER-SPEC.md): RFC 8785 canonical entries, a chain id bound in the genesis entry, `ledger.head` to catch a lost tail, and arguments kept out of the chain in salted side files that the chain commits to.
- `polarizer verify [--args]` reports one status with its own exit code (`intact`, `tampered`, `invalid`, `not canonical`, `torn tail`, `truncated`), and never writes. `polarizer repair` removes a torn tail and nothing else.
- A standalone reference verifier (`conformance/reference_verify.py`) and generated conformance fixtures; a differential test checks that the two verifiers agree.
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
