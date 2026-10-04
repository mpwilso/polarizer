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

Run this on the current code, from `~/code/polarizer`, in a plain terminal, never from inside another Claude Code session. It answers one open question, whether interactive Claude Code lists the tools again after Polarizer's change notice without a reconnect (docs/PIN-SPEC.md, decision 9), and checks the first run and a rug pull (sections 7 and 9). Keep a second terminal open in `~/code/polarizer` for the `polarizer` commands.

### M1. Prepare

Do steps 1 and 2 above first (the snapshot, `uv sync --locked`, the pre-warm, `polarizer.toml` and `manual/mcp.json`). Then give this check its own ledger and turn on the probe's rug-pull mode:

```
sed -i 's|^ledger_dir = .*|ledger_dir = "~/.local/share/polarizer-m1a-check"|' polarizer.toml
sed -i 's|env = { PROBE_LOG = "/tmp/polarizer-probe.log" }|env = { PROBE_LOG = "/tmp/polarizer-probe.log", PROBE_RUGPULL = "/tmp/polarizer-rugpull" }|' polarizer.toml
rm -f /tmp/polarizer-rugpull
grep -n 'ledger_dir\|PROBE_RUGPULL' polarizer.toml
```

Expect the new `ledger_dir` line and the probe's `env` line with `PROBE_RUGPULL`. With `PROBE_RUGPULL` set, the probe serves its usual definitions the first time it starts and creates `/tmp/polarizer-rugpull`; every later start finds the file and changes `wait`'s description to `Wait for a number of seconds. Changed after approval.`: a server that changes itself after it was approved. If either line is missing, paste the `grep` output.

### M2. First start: nothing exposed

Start Claude Code with the same command as in step 3, `claude --mcp-config "$PWD/manual/mcp.json" --strict-mcp-config`, and type `/mcp`. Expect `polarizer` connected with no tools: this Polarizer has recorded what the probe and the Filesystem server list, and nothing is approved yet. Paste what `/mcp` shows if it lists any tool, or if `polarizer` failed to connect. Keep this session open until M5.

(The first run without Claude Code, which the quickstart describes, is `polarizer serve --config <path> < /dev/null`: it records the definitions and exits. This check starts Claude Code first instead, so M3 can approve while a session is open.)

### M3. Approve while the session is open

In the second terminal:

```
.venv/bin/polarizer pending --config "$HOME/code/polarizer/polarizer.toml"
```

Expect `pending: 21 new, 0 changed, 0 unservable` (the probe's 7 tools and the Filesystem server's 14), each definition printed in full, and a last line `group <id> covers the 21 new definitions above for tools with no decision yet and one definition waiting`. Read them; then approve the group, with the id from that last line:

```
.venv/bin/polarizer approve --config "$HOME/code/polarizer/polarizer.toml" --group <id>
```

Expect 21 `approved ... at seq <q>` lines, then `approved 21 definitions as group <id>`.

Now, in Claude Code, without reconnecting anything, wait about 5 seconds and type `/mcp`. Polarizer notices the approval within about a second and sends Claude Code a change notice. Note whether the 21 tools are listed.
- **Listed:** interactive Claude Code lists the tools again after a change notice.
- **Not listed:** reconnect `polarizer` from `/mcp`, type `/mcp` again, and note whether they are listed then.

Paste back: whether the tools appeared without reconnecting, about how many seconds after the approval you typed `/mcp`, and the tool list as `/mcp` shows it (or, if you had to reconnect, what it showed before and after).

### M4. The rug pull

In `/mcp`, reconnect `polarizer`. That starts a new Polarizer process and a new probe, which finds `/tmp/polarizer-rugpull` and changes `wait`'s description. Then, in the second terminal, run M3's `pending` command again. Expect `pending: 0 new, 1 changed, 0 unservable` and a block starting `changed probe__wait <new hash>, approved <old hash>`, whose definition has the description `Wait for a number of seconds. Changed after approval.`

In Claude Code, type `/mcp`. Expect the other 20 tools, and no `probe__wait`. Then ask Claude:

> Call probe__wait with 1 second.

Claude Code may say the tool doesn't exist, since it is no longer listed. If it calls it, expect the error `polarizer: probe__wait is not available: its definition changed after approval`. Either way, check the ledger:

```
grep -c '"kind":"tool.drift"' ~/.local/share/polarizer-m1a-check/ledger.jsonl
grep '"kind":"call.refused"' ~/.local/share/polarizer-m1a-check/ledger.jsonl | tail -n 1
```

Expect `1` drift. If Claude called the tool, expect a `call.refused` entry whose `reason` is `upstream probe tool "wait" changed after approval`; if it didn't, no new `call.refused`. Paste the `pending` output's first two lines, what `/mcp` listed, what Claude said, and both `grep` outputs.

### M5. Approve the change while the session is open

In the second terminal, with the new hash from M4's `changed` line:

```
.venv/bin/polarizer approve --config "$HOME/code/polarizer/polarizer.toml" probe wait <new hash>
```

Expect the definition with the changed description, then `approved probe__wait <new hash> at seq <q>`. In Claude Code, without reconnecting, wait about 5 seconds and type `/mcp`. Note whether `probe__wait` is listed again. Paste whether it came back without reconnecting, and after about how many seconds.

### M6. Verify, tidy up and guard

Exit Claude Code, then:

```
.venv/bin/polarizer verify --config "$HOME/code/polarizer/polarizer.toml"
sed "s|/home/<you>|$HOME|g" polarizer.example.toml > polarizer.toml
rm -f /tmp/polarizer-rugpull
pgrep -a -f 'tests/helpers/probe_server.py|server-filesystem'
scripts/guard.sh check
```

Expect an `intact:` first line from `verify` (the `sed` line then puts `polarizer.toml` back as in step 2, without the M1a changes), no output from `pgrep`, as in step 8, and the guard as in step 8. Paste all three outputs. Claude Code updates `~/.claude.json` on every run; the guard reports its size and mtime for information only.

