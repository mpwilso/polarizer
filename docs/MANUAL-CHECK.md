# Manual check (M0 and M1a)

**Which code this checks.** Steps 1 to 8 describe M0, the code at commit 4eaa61a, where every upstream tool is exposed as soon as Polarizer connects. From M1a (stage 4 on), Polarizer exposes no tool until a person approves it with `polarizer approve`, and no flag brings M0's behavior back. To run steps 1 to 8 as written, check out commit 4eaa61a. The M1a section at the end runs on the current code: the first run, approving while a session is open, and a rug pull.

Run this in a real, interactive Claude Code session. It covers what no headless run can: `/mcp`, Esc to cancel, and normal use of two upstreams through Polarizer. Each step gives the command, what to expect, and what to paste back if it differs. Run every command from `~/code/polarizer`, in a terminal. Never run the manual check from inside another Claude Code session: that session's tools and settings would be mixed into what you observe.

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

Run this on the current code, from `~/code/polarizer`, in a plain terminal, never from inside another Claude Code session. It answers one open question, whether interactive Claude Code lists the tools again after Polarizer's change notice without a reconnect (docs/PIN-SPEC.md, decision 9), and checks the first run and a rug pull (sections 7 and 9). `scripts/m1a-check.sh` does every step except starting Claude Code and looking at `/mcp`. Run its commands in a second terminal, in `~/code/polarizer`, in this order; each one says what to do in terminal A, asks what you saw, and records it.

1. `scripts/m1a-check.sh reset`: takes a guard snapshot, runs `uv sync --locked` and the Filesystem pre-warm, removes `/tmp/polarizer-rugpull` and `~/.local/share/polarizer-m1a-check`, writes `polarizer.toml` from the example with the check's ledger and the probe's rug-pull mode, and prints the command that starts Claude Code in terminal A.
2. `scripts/m1a-check.sh approve`: checks that `pending` lists exactly a first run's 21 new definitions, approves their group when you type `yes`, and asks whether `/mcp` shows the tools without reconnecting.
3. `scripts/m1a-check.sh rugpull`: after you reconnect `polarizer` from `/mcp`, shows the changed `probe__wait`, asks what `/mcp` lists and what Claude said when asked to call it, and records the drift count and the last refusal.
4. `scripts/m1a-check.sh approve-changed`: approves the changed definition by name when you type `yes`, and asks whether `/mcp` lists `probe__wait` again without reconnecting.
5. `scripts/m1a-check.sh finish`: after you exit Claude Code, runs `verify`, writes `polarizer.toml` back from the example, removes `/tmp/polarizer-rugpull`, lists leftover probe or Filesystem server processes, and runs `scripts/guard.sh check`.

`scripts/m1a-check.sh status` says which step is next. A blank answer or Ctrl+C stops a step and says what was done; running the same step again carries on from there.

When done, run `cat /tmp/m1a-check-results.txt` and paste it.
