# Polarizer M0 plan (revision 7, Oct 2, 2026)

Read after CLAUDE.md, and with docs/LEDGER-SPEC.md (the ledger format), docs/PROXY-SPEC.md (proxy and command-line behavior), docs/milestones.md and docs/verified-facts.md. docs/PLAN.md is background only. Where it disagrees with these files, these win; the corrections are listed at the end.

## Changes from the last version

- **Stage 3:** results are compared with direct calls, and the SDK's own changes to them are stated limits (PROXY-SPEC.md, Results); the reference Filesystem server is pinned for the manual check and the local reference tests; the live check generates its config in a temp directory and makes one 14 s call; the quickstart draft is `docs/QUICKSTART-DRAFT.md`; M1a must record a failed refresh and never read it as "unchanged".
- **Stage 1 review:** `protected_paths` renamed `ledger_forbidden_paths` ("protected paths" is kept for M2a), a differential fuzz test, `mcp` pinned to 2.2.0, CI actions pinned by commit SHA, and `docs/PUSHING.md`.
- **Revision 7** (the implementation session's step 0, second round):
  - `ledger_forbidden_paths` in polarizer.toml (first named `protected_paths`) replaces `.guard-paths` in the tool. Parallax's two directories are always forbidden, and paths are compared by resolved real path components.
  - `verify` and `repair` take exactly one of `--config` or `--ledger-dir`, both absolute.
  - `ledger.head` holds `chain_id`, `hash` and `seq`. One status order for the whole file: lines, then the head, then a torn tail, with two new order fixtures.
  - `verify` never writes, and reports a missing `ledger.head` on one extra line.
  - v0 check order, messages without a seq, and a refusal of v0 ledgers in `serve` and `repair`.
  - `call.refused` for unknown tools, `client_call_id` may be null, integer `latency_ms`, and `result_bytes` defined.
  - `config.py` (parse and validate only) moves to stage 1.
- **Revision 6** (the implementation session's step 0):
  - `serve` requires an absolute `--config`.
  - The per-line check order is fixed, with two-fault fixtures to pin it.
  - Prefix rules, and tool names that contain `__`.
  - Older-era requests to the client are recorded as `protocol-error`, a stated limit.
  - `session.client` is written exactly once per process.
  - `ledger_dir` is refused inside guarded paths.
  - Guard changes.
  - `live-check.sh` pass criteria.
  - A README quickstart draft that pre-warms `npx` servers.
  - The build order now matches the three implementation stages.
- **Revision 5:**
  - The proxy contract moved to PROXY-SPEC.md. That file adds:
    - tools-only scope;
    - the `polarizer.toml` schema and its error messages;
    - parallel startup with connect timeouts;
    - pagination limits;
    - six call outcomes checked against the SDK;
    - exact command-line output.
  - LEDGER-SPEC.md gained first-run rules and `ledger.head_rebuilt`. `ledger_dir` now only warns inside a git tree.
  - Fixtures come from a deterministic generator. The flat-append claim is now tested by bytes read, not by time.
- **Revision 4:**
  - Lock waits of 2 s.
  - Listening to 2026-07-28 upstreams.
  - Named tests for each protocol version.
  - The Everything server pinned.
  - Timestamped cancellation evidence.
- **Revision 3:**
  - The ledger format moved to LEDGER-SPEC.md.
  - The first spike.
  - The standing rules and the guard.

## What M0 is and when it's done

`polarizer serve` is a stdio MCP server that Claude Code starts from a project-scope `.mcp.json`. It connects to the upstreams in `polarizer.toml`, lists their tools as `<prefix>__<tool>`, forwards calls, and records each call in the ledger. "Unchanged" means every upstream tool works with the same arguments and gives the same results; only the names gain a prefix. M0 is tools only (PROXY-SPEC.md, Scope). The few ways the MCP SDK itself changes results and tool definitions are stated limits (PROXY-SPEC.md, Results).

M0 is done when all of these hold, and you've confirmed the manual checklist:

- that's true in a real Claude Code session;
- every call is in the ledger;
- both verifiers agree on every conformance fixture;
- each broken fixture gets its expected status.

Not in M0: anchoring, signatures (the field name is reserved), OCSF, OpenTelemetry, policy, taint, the approval page, canaries, resources, prompts, and forwarding requests from upstreams to the client.

## Build order

1. **Project setup.**
   - `pyproject.toml` with `mcp>=2.2,<3` and `rfc8785==0.1.4`, plus pytest and ruff for development, managed with uv in `.venv` with `uv.lock` committed.
   - `scripts/test.sh` runs ruff, then pytest.
   - CI with one trivial test: Linux on Python 3.11, 3.12 and 3.13; Windows and macOS on 3.12.
2. **`conformance/`**: `reference_verify.py` first, then `tools/make_fixtures.py` (see Test methods), then the generated valid and broken chains and `expected.json`.
   - The reference verifier is written from LEDGER-SPEC.md, not from Polarizer's code.
   - It uses only the standard library and `rfc8785`, imports nothing from Polarizer, and stays under 80 lines.
3. **`src/polarizer/`**:
   - `canon.py`: the subset check and canonical bytes;
   - `ledger.py`: verify and statuses (read-only);
   - `lock.py`: the ledger lock and its 2 s wait;
   - `writer.py`: the writer thread, fsync policy, first run, `ledger.head` and repair;
   - `sidefiles.py`: argument side files and `verify --args`;
   - `ledgerdir.py`: location, modes, the refusal inside forbidden paths, and the git-tree warning;
   - `config.py`: parse and validate polarizer.toml with tomllib, with no upstream connections (moved here from stage 2, because `verify --config` and `repair --config` need it);
   - `cli.py` with `verify [--args]` and `repair`, and `tests/golden/` for their output.

   Steps 1 to 4 are implementation stage 1.
4. **`tools/make_v0_fixture.py`**, run once.
   - It runs `git -C <parallax> show <commit>:parallax/ledger.py` into a temp directory and runs that file there with `PYTHONDONTWRITEBYTECODE=1`.
   - It writes a few entries and copies the output to `conformance/valid/v0-parallax.jsonl`, with the commit hash in `expected.json`.
   - The file is stdlib-only at `22ef600`. The Parallax package is never imported.
5. **The proxy**, as in PROXY-SPEC.md: `upstream.py`, `proxy.py`, and `serve` added to `cli.py`. Steps 5 and 6 are implementation stage 2.
6. **Test servers.**
   - `tests/helpers/probe_server.py`: a stdlib MCP server that reads stdin on its own thread and runs each call on a worker. Its main tool is `wait(seconds)` (stage 2 added `crash`, `env` and `fail` for tests). It sends progress every second when given a token, logs every inbound message with a timestamp, and can delay its handshake (`PROBE_DELAY`).
   - In-memory fake upstreams built on the SDK's low-level `Server`, covering:
     - both protocol eras, with a TTL cache hint and a listen bus;
     - errors of each outcome type;
     - requests to the client;
     - paging past the limits.
7. **`scripts/live-check.sh`**: one headless `claude -p` run with a small model (`--model haiku`), using `--strict-mcp-config`, `--mcp-config` with a config it generates in a new temp directory, and `--no-session-persistence`. A stdlib wiretap (`scripts/wiretap.py`) between Claude Code and Polarizer timestamps Claude Code's own cancel.
   - **It prints its conditions first,** so a result can be read against them. It reads only its own command line and environment, never a settings file:
     - the exact `claude` arguments it passes;
     - `permission mode: <value>` when it passes `--permission-mode`, otherwise `permission mode: not passed; the user-scope default applies and was not read`;
     - `auto mode: <on or off>` when its own arguments or environment set it, otherwise `auto mode: not set here; user-scope settings may set it and were not read`;
     - the names of set environment variables starting `CLAUDE_` or `ANTHROPIC_` (names only), and the values of `MCP_TIMEOUT`, `MCP_TOOL_TIMEOUT`, `MCP_CONNECT_TIMEOUT_MS` and `CLAUDE_CODE_MCP_TOOL_IDLE_TIMEOUT`.
   - One 14 s call to the probe's `wait` under `MCP_TOOL_TIMEOUT=5000`.
   - **It passes only if both hold:**
     - the probe's log shows `notifications/cancelled` within 2 seconds of Claude Code's cancel;
     - the ledger has a `call.returned` with outcome `cancelled` for that call.
   - **Otherwise** it exits 1 and names the failed check.

   It is manual only: it calls a model, so it's a script, never a CI test. It reports `~/.claude.json` bookkeeping. Steps 7 and 8 are implementation stage 3, with `docs/MANUAL-CHECK.md` (the checklist below) and the claims table.
8. **Manual-check files:** `.mcp.json.example` (the real project-scope `.mcp.json` is gitignored) and `polarizer.example.toml`, with the probe and the pinned Filesystem server, `npx -y @modelcontextprotocol/server-filesystem@2026.8.31 <dir>`. The Everything server stays pinned at `npx -y @modelcontextprotocol/server-everything@2026.8.31 stdio` for the local reference tests. Also `docs/QUICKSTART-DRAFT.md`, a draft and not yet a README. It includes the limits text from PROXY-SPEC.md, and tells people to pre-warm each pinned `npx` server once (run it by hand and stop it) before first use, because a first fetch can take longer than the 10 s connect timeout.

## Test methods

**Fixtures.** `tools/make_fixtures.py` writes every valid chain deterministically, with fixed salts, chain ids, session ids and timestamps.
- **Hashing:** it uses `conformance/reference_verify.py`'s own canonicalization and hash functions, not `ledger.py`'s, so the fixtures don't check the code that wrote them.
- **Broken fixtures:** each one comes from a valid chain through a named mutation function, such as `edit_value`, `delete_middle_line`, `swap_lines`, `tear_last_line`, `reorder_keys`, `insert_float`, `big_integer`, `non_ascii_key`, `extra_top_level_key`, `second_genesis`, `edit_v0_entry` or `mix_v0_v1`. Four fixtures pin the status order with two faults each: `extra_key_and_bad_hash` (expected `invalid`), `bad_seq_and_bad_hash` (expected `tampered`, reason `seq is ...`), `torn_tail_and_truncated` (expected `truncated`) and `tampered_line_and_head_mismatch` (expected `tampered` at the line).
- **Head pairs:** a fixture that needs a `ledger.head` is a file pair, `<name>.jsonl` and `<name>.head`. The reference verifier takes the head path as an optional second argument.
- **`expected.json`:** maps each fixture to its status, line and seq. The seq is `null` for v0.
- **Regeneration:** `tests/test_fixtures.py` regenerates everything into a temp directory and diffs it byte for byte against the committed files.
- **The v0 fixture** is the exception: it comes from Parallax's real code (build step 4) and isn't regenerated.

**Flat append cost.** CI asserts no wall time.
- **The assertion:** a test wraps the writer's file layer to count bytes read and lines parsed per append. It asserts both stay the same for a 100-entry ledger and a 10,000-entry ledger, apart from the new bytes another process wrote.
- **The benchmark:** `scripts/bench_append.py` prints p50 and p95 append time with and without fsync on each CI runner, and asserts nothing.

## Tests and claims

| Claim | Command | Run? (local runs; CI has run stages 1 and 2) |
|---|---|---|
| Claude Code 2.1.287 speaks 2026-07-28 to an SDK 2.2 server, opens `subscriptions/listen`, and re-lists after a change notice | spike, headless | yes |
| Claude Code cancels on its own timeout in both eras, and the upstream behind the proxy gets its own cancel 1 ms later | spike, headless (timestamped logs in verified-facts.md) | yes |
| Claude Code's startup limit is `MCP_TIMEOUT`, 30 s by default; an 8 s handshake works with defaults | binary and spike, headless | yes |
| SDK outcomes: tool error as a result; upstream JSON-RPC error as `MCPError` with code kept; dead upstream as -32000; `InputRequiredResult` returned with `allow_input_required=True` | spike, SDK only | yes |
| A 2026-07-28 upstream works through the proxy (versions, list change through listen, TTL hint, progress, cancel) | spike round 3; `pytest tests/test_eras.py::test_modern_upstream` (memory and stdio) | spike yes, repo yes, Linux (stage 2) |
| A 2025-11-25 upstream works through the proxy while Claude Code's side is 2026-07-28 | spike round 2; `pytest tests/test_eras.py::test_handshake_upstream` | spike yes, repo yes, Linux (stage 2) |
| Advertised capabilities are exactly tools in each era | `pytest tests/test_scope.py` | yes, Linux (stage 2) |
| An `InputRequiredResult` gives `unsupported` with the one-line error; older-era requests to the client are refused and recorded as `protocol-error` | `pytest tests/test_outcomes.py` | yes, Linux (stage 2) |
| Each config error gives its exact message and exit 2; `${NAME}` expansion; the upstream gets only the minimal environment plus its `env` | `pytest tests/test_config.py` | errors and `${NAME}`: yes, Linux; minimal environment: yes, Linux (stage 2) |
| Upstreams connect in parallel; a hung one times out at its `connect_timeout_seconds` while the others serve; no retry | `pytest tests/test_startup.py` | yes, Linux (stage 2; shown by events, not elapsed time, since stage 3) |
| Paging stops at 100 pages or 1,000 tools; names over 128 characters are skipped and recorded | `pytest tests/test_listing.py tests/test_proxy.py::test_prefix_rules` | yes, Linux (stage 2) |
| Each outcome is recorded and returned as in PROXY-SPEC.md | `pytest tests/test_outcomes.py` | yes, Linux (stage 2) |
| Prefixes, exact arguments, `_meta` filter, progress relay, concurrency, and one sent and one returned entry per call | `pytest tests/test_proxy.py tests/test_outcomes.py` | yes, Linux (stage 2) |
| An upstream tool whose own name contains `__` lists and calls correctly, split at the first `__` | `pytest tests/test_proxy.py::test_double_underscore_tool_name` | yes, Linux (stage 2) |
| `session.client` is written exactly once per process, even when two requests arrive at once | `pytest tests/test_proxy.py::test_session_client_once` | yes, Linux (stage 2) |
| `serve` without `--config`, or with a relative path, exits 2 with one line on stderr | `pytest tests/test_golden.py tests/test_config.py` | yes, Linux (stage 2) |
| stdout of `serve` carries only JSON-RPC | `pytest tests/test_stdout.py` | yes, Linux (stage 2) |
| Every `verify`, `verify --args` and `repair` case prints its exact stdout and exit code | `pytest tests/test_golden.py` | yes, Linux |
| The canonical subset holds; non-ASCII keys break it | throwaway differential; `pytest tests/test_canon.py` | throwaway yes, repo yes (Linux) |
| Both verifiers agree on status, line and seq for 5,000 randomly mutated chains per run (bit flips, deleted, swapped and duplicated lines, truncation, reformatted lines, `ledger.head` edits, and structure-level edits: reordered keys with the hash kept, whitespace between tokens, `\uXXXX` escapes, integers as `1.0` or `1e0`, a UTF-8 BOM, a CR before the newline), from a printed seed, under 60 s | `pytest tests/test_differential.py` | yes, Linux: seed 20261002, plus 20,000 cases each on seeds 1 to 5, with and without the structure-level edits, no disagreements |
| RFC 8785's vectors inside the subset give identical bytes from canon.py, rfc8785, the reference verifier and the stdlib; those outside it are rejected for the right reason | `pytest tests/test_rfc8785_vectors.py` | yes, Linux |
| The real CLI prints the golden bytes, UTF-8 with "\n", including for a non-ASCII directory name | `pytest tests/test_cli_subprocess.py` | yes, Linux |
| A differently cased forbidden path: refused on Windows, not refused on Linux, follows the file system on macOS | `pytest tests/test_ledgerdir.py` | Linux case yes; Windows and macOS cases no |
| Fixtures regenerate byte for byte, and both verifiers agree on every one | `pytest tests/test_fixtures.py tests/test_conformance.py` | yes, Linux |
| Bytes read per append don't grow with ledger size; two processes leave one chain; catch-up works | `pytest tests/test_writer.py` | yes, Linux |
| First run: the genesis entry is written once when two processes start together; a missing `ledger.head` is rebuilt and recorded | `pytest tests/test_first_run.py` | yes, Linux |
| `ledger.head` only moves forward; `truncated` is detected; the Windows replace retry works | `pytest tests/test_head.py` (Windows runner for the last) | yes, Linux, with the replace failure simulated; the Windows test: no |
| Startup verify, `verify` and `repair` wait up to 2 s for the lock, then refuse (exit 7); repair re-checks under the lock, refuses any status but `torn tail`, and handles a torn genesis | `pytest tests/test_repair.py` | yes, Linux |
| `verify` creates, deletes and modifies nothing, on a writable and on a read-only directory, and never creates the lock file | `pytest tests/test_verify_readonly.py` | yes, Linux |
| `verify` and `repair` need exactly one of `--config` or `--ledger-dir`, absolute, else one line and exit 2 | `pytest tests/test_golden.py` | yes, Linux |
| Side files: exclusive create, orphans reported, deletion still verifies, tampering exits 8 | `pytest tests/test_args.py` | yes, Linux |
| `ledger_dir` modes; refusal inside a `ledger_forbidden_paths` entry or Parallax's directories (always forbidden), by real path components; the warning inside any other git working tree | `pytest tests/test_ledgerdir.py` | yes, Linux |
| Results through the proxy equal direct results, apart from the SDK's stated limits, which are pinned exactly; an unreadable result gives a fixed line and no result data in the ledger | `pytest tests/test_fidelity.py` | yes, Linux (stage 3) |
| The pinned Everything and Filesystem servers list the same tools and give the same results through the proxy, apart from the stated limits | `POLARIZER_REFERENCE=1 pytest tests/test_reference.py` (local only, never in CI) | yes, Linux (stage 3) |
| live-check's pass and fail decision, on made-up logs | `pytest tests/test_live_check.py` | yes, Linux (stage 3) |
| End to end with Claude Code: the upstream gets `notifications/cancelled` within 2 s of Claude Code's cancel, and the ledger records `cancelled` | `scripts/live-check.sh` (manual) | yes, once, headless: Claude Code 2.1.288, haiku, Linux (stage 3) |

## Manual checklist (M0 isn't done until you confirm)

1. **Forbidden paths.** Before the first real run, confirm that the gitignored `~/code/polarizer/polarizer.toml` lists `~/code/parallax`, `~/code/parallax-backup-before-rewrite`, `~/code/loupe` and `~/code/isr` in `ledger_forbidden_paths`.
2. **A real session.** Use the project-scope `.mcp.json` in `~/code/polarizer`, never user scope. It starts `polarizer serve --config /home/<you>/code/polarizer/polarizer.toml` (an absolute path) in front of the probe and the Filesystem server. Pre-warm the pinned Filesystem server once first. docs/MANUAL-CHECK.md has the exact commands for this checklist. Start `claude` in `~/code/polarizer` and use both servers' tools. Then run `polarizer verify --config /home/<you>/code/polarizer/polarizer.toml` and expect output in this form:

   ```
   intact: 31 entries, 2 sessions, 12 calls
   chain 5f0c9e2a7b14d3e8a1c6f9b2d4e7a0c3, head <64 hex> at seq 30
   ```
3. **Names.** In `/mcp`, the tools appear with prefixes. Note whether any long name is shortened.
4. **Cancel.** Press Esc during a 30 s `wait` call, and check whether the probe log shows `notifications/cancelled`. Verified interactively on Oct 3, 2026, with Claude Code 2.1.288: the cancel reached the probe and the ledger recorded `cancelled` (verified-facts.md, Interactive). Closing a session without pressing Esc during a call is still unverified.
5. **A hung upstream.** Set an upstream's command to something that never answers. Claude Code should still start Polarizer, with the other upstream's tools, within about 10 s.
6. **Optional:** run the benchmark once on native Windows Python.
7. **Guard.** Run `scripts/guard.sh check`. Expect no changes in the guarded repos, Parallax's directories or the MCP config hashes. The `~/.claude.json` size and mtime line is informational, because Claude Code updates that file whenever it runs.

## Credits

Ideas from mcpclerk, credited by name; none of its code is used:
- the run-end entry count, which inspired `ledger.head`;
- hidden tools still refused and recorded when called by name (M1a);
- an approve-everything switch that exists only as a flag, never in the policy file (M3).

## Appendix: M1a

Superseded by docs/PIN-SPEC.md, which wins where the two differ. Kept for history.

**Definition hash.**
- Parse each upstream tool as an SDK `Tool`, keeping the upstream's own name.
- Take `model_dump(by_alias=True, mode="json", exclude_none=True)`. That is how the SDK serializes what it serves.
- `def_hash = hex(sha256(b"POLARIZER-TOOLDEF/1\n" + rfc8785.dumps(form)))`, with full RFC 8785 and no subset, because schemas have arbitrary keys and floats.

Why not the alternatives:
- `exclude_unset` keeps an explicit `null` that the SDK then drops when serving, so a null-versus-absent change would show as drift no client can see.
- The raw wire object isn't handed over by the SDK, and it would include fields that are never served.

**Stored copy.**
- `defs/<hash>.json` holds the exact bytes, checked by rehashing on every read.
- In M1a, Polarizer serves the stored copy without `_meta`, so what Claude Code sees is a subset of what was approved.
- Upstream `instructions` are not passed at all. This fails closed until M1b.

**Approve and pin.**
- `polarizer pending` lists new and changed tools.
- `polarizer approve <prefix> <tool>` shows the definition and records `tool.approved`. That record is fsynced, and `ledger.head` updated, before the tool is exposed.
- `polarizer reject` needs a reason.
- A tool is exposed only if its latest decision is `tool.approved` with a hash equal to the live hash.
- A mismatch hides the tool and records `tool.drift` once per pair of hashes.
- A hidden tool called by name is refused and recorded as `call.refused`.

**Fail closed.** At startup, any status other than `intact` exits. Without that, losing the tail could drop a later `tool.rejected`, leaving an earlier `tool.approved` as the latest decision, and the rejected tool would come back.

**Drift detection.**
- Re-list upstreams with `cache_mode="refresh"` at startup, on each upstream change event (a listen event in 2026-07-28, `tools/list_changed` in the older era), and on each client `tools/list`.
- An upstream event is no longer republished directly. Polarizer re-checks pins first, and republishes only if the set of tools it exposes changed.
- When the exposed set changes, publish `ToolsListChanged()` on the bus. The spike showed Claude Code then re-lists, headless.
- `send_tool_list_changed` is only for older-era clients, and is untested with Claude Code because Claude Code negotiates 2026-07-28.
- Whether M1a also re-checks the pin on each call is decided when M1a is planned.
- A failed refresh of an upstream's tool list must never be treated as "unchanged" for drift detection, and must be recorded. M0 keeps the last good list after a failed refresh and says so only on stderr (STAGE2-NOTES.md, guess 2); that is not enough for pins.

**Tests.**
- A fixed hash for a fixed definition.
- An unknown extra field is dropped from both the hash and the served copy. This pins the SDK's behavior.
- `input_schema` and `inputSchema` on the wire give the same hash and the same bytes.
- `null` and absent give the same hash.
- Key order doesn't matter, and a nested parameter description does.
- Pending tools are hidden. Approve exposes, reject hides, and reject needs a reason. State rebuilt from the ledger alone matches.
- Drift mid-session publishes a change and hides the tool.
- The served copy is a subset of the approved copy.
- A hidden tool called by name is refused and recorded.
- Startup fails closed on every status other than `intact`.

**Your check.** Approve the probe's tool, then restart the probe with a changed description. `polarizer pending` should show it as changed, with both hashes, and Claude Code should no longer list it.

## M1b, in one paragraph

M1b adds the scanning and review layer:
- **Scan rules:** hidden Unicode, control characters, long text, mentions of another upstream's tools, URLs, sensitive paths, and one heuristic for text aimed at the model. They read nested schema descriptions, enum values and defaults.
- **Score both sides:** the rules are scored on poisoned fixtures and on the reference servers' real tool lists, which give the false flags.
- **Review UI:** shows diffs and approves unflagged tools as a group.
- **Allowlists:** a `_meta` allowlist for served tools.
- **Instructions:** upstream `instructions` are pinned and approved like tools.

## Corrections to PLAN.md

These stay until PLAN.md draft 5, which is deferred until after M0.

1. The ledger fields are those in LEDGER-SPEC.md. OCSF names belong in a later export.
2. Claude Code's default hard limit is about 27.8 hours, so Polarizer's own hold timeout governs, not a race. Claude Code sends `notifications/cancelled` when its limit fires.
3. TOML, not YAML; `mcp>=2.2,<3`, not `>=2.1`.
4. Pinning is approve-and-pin, not trust on first use.
5. Hash the whole served tool form, not a named list of fields.
6. Drift notices: in 2026-07-28, list changes travel only over `subscriptions/listen`. Claude Code opens that stream at connect and re-lists when notified (headless). The SDK client caches lists only from 2026-07-28 upstreams with TTL hints, so use `refresh`.
7. The SDK silently drops unknown tool fields, so a poisoned unknown field never reaches the model through Polarizer.
8. The canonical subset needs ASCII keys.
9. Progress is relayed with `report_progress`, not forwarded.
10. Windows and macOS append costs come from the CI benchmark.
11. M2a includes resolved-path rules. Without them, it can only hold whole tools.
12. The rest of M2 is M2b, placed before the practice range.
13. "Unchanged" means only the names gain a prefix, and M0 is tools only.
14. The ledger lives in `ledger_dir`, by default `~/.local/share/polarizer/`. Startup and repair refuse it inside any `ledger_forbidden_paths` entry and Parallax's directories, and warn inside any other git working tree.
