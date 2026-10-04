# Manual check (M0 and M1a)

**Which code this checks.** Steps 1 to 8 describe M0, the code at commit 4eaa61a, where every upstream tool is exposed as soon as Polarizer connects. From M1a (stage 4 on), Polarizer exposes no tool until a person approves it with `polarizer approve`, and no flag brings M0's behavior back. To run steps 1 to 8 as written, check out commit 4eaa61a. The M1a section at the end runs on the current code: a headless, scripted rug-pull check that asks nothing.

Run steps 1 to 8 in a real, interactive Claude Code session. They cover what no headless run can: `/mcp`, Esc to cancel, and normal use of two upstreams through Polarizer. Each step gives the command, what to expect, and what to paste back if it differs. Run every command from `~/code/polarizer`, in a terminal. Never run the manual check from inside another Claude Code session: that session's tools and settings would be mixed into what you observe.

## 1. Prepare

```
scripts/guard.sh snapshot
uv sync --locked
mkdir -p /tmp/polarizer-manual
echo "hello from the manual check" > /tmp/polarizer-manual/note.txt
npx -y @modelcontextprotocol/server-filesystem@2026.8.31 /tmp/polarizer-manual < /dev/null
```

Expect the snapshot path, then `Secure MCP Filesystem Server running on stdio`, and the prompt back within a few seconds. The `npx` line is the pre-warm: a first fetch can take longer than Polarizer's connect timeout. If it fails, paste its output.

## 2. polarizer.toml and manual/mcp.json

```
sed "s|/home/<you>|$HOME|g" polarizer.example.toml > polarizer.toml
sed "s|/home/<you>|$HOME|g" manual/mcp.json.example > manual/mcp.json
grep -A5 ledger_forbidden_paths polarizer.toml
git status --short
.venv/bin/polarizer verify --config "$HOME/code/polarizer/polarizer.toml"
```

Expect:
- `ledger_forbidden_paths` lists `~/code/parallax`, `~/code/parallax-backup-before-rewrite`, `~/code/loupe` and `~/code/isr`;
- `git status --short` prints nothing, because both files are gitignored;
- verify prints `no ledger at /home/<you>/.local/share/polarizer-manual` (exit 2) the first time, or an `intact:` line if a ledger is already there.

If git lists either file, or verify prints anything else, paste the output.

## 3. Start Claude Code

```
claude --mcp-config "$PWD/manual/mcp.json" --strict-mcp-config
```

Polarizer is started only by this explicit `--mcp-config` file; there is no `.mcp.json` at the repo root (CLAUDE.md). `--strict-mcp-config` makes Claude Code ignore every other MCP configuration for this session. If Claude Code asks whether to use the server `polarizer`, allow it. Claude Code may update `~/.claude.json`, which is its own bookkeeping. Then type `/mcp`.

Expect `polarizer` to show as connected, with 20 tools: `probe__wait`, `probe__crash`, `probe__env`, `probe__fail`, `probe__rich`, `probe__invalid`, and 14 `fs__` tools such as `fs__read_text_file` and `fs__list_directory_with_sizes`. Note how each name is shown, and whether any long name is shortened or cut. Paste the tool list as `/mcp` shows it, and say whether anything was shortened.

## 4. Use both servers

Ask Claude:

> Using the polarizer tools, list /tmp/polarizer-manual, read note.txt there, then call probe__wait with 2 seconds.

Expect the file listing, the text `hello from the manual check`, and `waited 2 s`. If a call fails, paste the error Claude shows.

## 5. Esc during a long call

Ask Claude:

> Call probe__wait with 30 seconds.

Press Esc about 5 seconds after the call starts. Then, in another terminal:

```
grep -n 'notifications/cancelled\|stopped after cancel' /tmp/polarizer-probe.log
tail -n 2 ~/.local/share/polarizer-manual/ledger.jsonl
```

Expect a `notifications/cancelled` line, then `call <id> stopped after cancel` with the same timestamp, and a ledger entry `call.returned` with `"outcome":"cancelled"`. The owner observed exactly this on Oct 3, 2026, with Claude Code 2.1.288 (docs/verified-facts.md, Interactive). Closing the session without pressing Esc during a call is still unverified. Paste both outputs if they differ, and roughly when you pressed Esc.

## 6. A hung upstream (optional)

Exit Claude Code. Add this to the end of `polarizer.toml`:

```toml
[upstream.hung]
command = "sleep"
args = ["3600"]
connect_timeout_seconds = 5
```

Start Claude Code again with the same command as in step 3, `claude --mcp-config "$PWD/manual/mcp.json" --strict-mcp-config`, and type `/mcp`. Expect `polarizer` connected within about 10 seconds, with the same 20 tools and no `hung__` tools. If it takes longer or fails, paste what `/mcp` shows. Then exit Claude Code and remove those four lines again.

Then check for a leftover `sleep`:

```
pgrep -a -f '^sleep 3600$'
```

Expect no output. Polarizer can't stop an upstream process itself: the SDK keeps the process handle, and Polarizer gives upstreams at most 1 s to close when it shuts down (docs/PIN-SPEC.md, section 8, step 2). `sleep` ignores end of input, so it ends only when the SDK sends it SIGTERM, about 2.5 s after its connect timeout, while Polarizer is still running. If you exited Claude Code sooner than that, a `sleep 3600` is left running, and this prints its pid and command. Stop it with `kill <pid>`, and paste the output and roughly how long the session lasted.

## 7. Verify the ledger

Exit Claude Code, then:

```
.venv/bin/polarizer verify --config "$HOME/code/polarizer/polarizer.toml"
```

`--config` takes `ledger_dir` from polarizer.toml, so this checks `~/.local/share/polarizer-manual`, the ledger steps 3 to 6 wrote. Expect output in this form, with your own numbers:

```
intact: 31 entries, 2 sessions, 12 calls
chain 5f0c9e2a7b14d3e8a1c6f9b2d4e7a0c3, head <64 hex> at seq 30
```

Any other first line, or a nonzero exit, means paste the output.

## 8. Leftover upstreams and the guard

```
pgrep -a -f 'tests/helpers/probe_server.py|server-filesystem'
scripts/guard.sh check
```

`pgrep` lists any probe or Filesystem server still running. Expect no output once Claude Code has exited: both end when their stdin closes. If it lists any, paste it; stop each with `kill <pid>`.

Expect no changes in the guarded repos, Parallax's directories or the MCP config hashes. The `~/.claude.json` size and mtime line is informational, because Claude Code updates that file on every run. Paste the whole output.

## M1a: pins (current code)

Run `scripts/rugpull-check.sh` from `~/code/polarizer` in a plain terminal, then paste the output of `cat /tmp/rugpull-check-results.txt`.

It asks nothing and runs headless `claude -p` (haiku) three times, so it costs a few cents. Everything lives in a new temp directory with its own ledger; it never touches `polarizer.toml`, `manual/mcp.json` or any other ledger. Each run gets an mcp config that differs only in `PROBE_PHASE`, so the probe's definitions depend on the run, not on how many times anything started. Pass or fail is read from the ledger and the probe's log, never from what the model says. What each part proves:

- **Priming** (no model) records the probe's original definitions and approves them as a group, as a person's first run does. It proves nothing by itself.
- **Run A** (original, approved): an approved tool works through real Claude Code. The probe receives `tools/call wait`, and the ledger has `call.sent` and `call.returned` with outcome `ok`.
- **Run B** (changed description, not approved): a definition changed after approval is caught. The ledger has `tool.drift` for probe wait with different `approved_hash` and `live_hash`, the probe receives no `tools/call wait`, and B's session has no `call.sent` for `probe__wait`.
- **The approval** (no model, through the library code `polarizer approve probe wait <hash>` runs): the ledger has `tool.approved` for the new hash.
- **Run C** (changed, approved): approving the new definition by name brings the tool back. The call reaches the probe again, with `call.sent` and `call.returned` outcome `ok`.
- **verify:** the ledger is intact at the end.

What it does not prove:

- **Mid-session re-listing in one live session.** Each run is a new Claude Code process with a new Polarizer, and B's change is there from the probe's first listing. That was answered interactively on 2026-10-04 with `scripts/m1a-check.sh`: approving while a session was open showed the tools in `/mcp` without a reconnect, both for the first approval and for approving the changed definition (docs/verified-facts.md, M1a check, interactive).
- **A definition that changes during a session.** Only the tests cover that (`tests/test_pins.py::test_drift_mid_session_handshake`).
- **Anything interactive,** such as `/mcp`, Esc, or what a person sees.

`scripts/m1a-check.sh`, the interactive check it replaces, stays in the repo, marked superseded.
