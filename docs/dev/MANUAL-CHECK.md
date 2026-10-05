# Manual check (M0, M1a and M2a)

**Which code this checks.** Steps 1 to 8 describe M0, the code at commit 4eaa61a, where every upstream tool is exposed as soon as Polarizer connects. From M1a (stage 4 on), Polarizer exposes no tool until a person approves it with `polarizer approve`, and no flag brings M0's behavior back. To run steps 1 to 8 as written, check out commit 4eaa61a. The M1a and M2a sections at the end run on the current code: M1a's is a headless, scripted rug-pull check that asks nothing; M2a's is an interactive session with a script that finds each hold for you.

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

`polarizer.example.toml` uses POSIX paths (`/home/<you>/...`, `/tmp/polarizer-manual`); on native Windows, the `sed` lines don't apply: edit each path to a real absolute path with a drive.

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

## M2a: holds (current code)

This one needs an interactive Claude Code session, because it asks what Claude Code shows while a call waits for you. Use two plain terminals, both in `~/code/polarizer`, never from inside another Claude Code session: terminal A runs Claude Code, and terminal B runs `scripts/hold-check.sh`. The script finds each hold in the ledger itself, so you never copy an id or a hash. Every command it prints is complete. Each step prints what it did and appends it to `/tmp/hold-check-results.txt`; `scripts/hold-check.sh status` says which step is next. A blank answer or Ctrl+C stops a step with a line saying what was done, and running the step again carries on. Each answer is one line: type a short answer and do not paste multi-line text; anything after the first line is thrown away, with a line saying how much.

The check uses its own ledger, `~/.local/share/polarizer-m2a-check`, and the example's policy: `workspace_roots = ["/tmp/polarizer-manual"]`, the Filesystem server's writes classed `local-write` and `move_file` classed `destructive`. A model session costs whatever your interactive Claude Code session costs for about five short requests.

1. **Terminal B:** `scripts/hold-check.sh reset`. It takes a guard snapshot, runs `uv sync --locked`, writes `manual/mcp.json` if it is missing, pre-warms the pinned Filesystem server, creates `/tmp/polarizer-manual/.git/hooks` and `/tmp/polarizer-manual/note.txt`, moves an earlier check's ledger aside (nothing is deleted), and writes `polarizer.toml` from the example with the check's ledger and `hold_timeout_seconds = 300`. Then it records the tools' definitions with `polarizer serve` and its input closed (no model), and asks you to type `yes` to approve them as one group. It ends with the command for terminal A.
2. **Terminal A:** `claude --mcp-config "$PWD/manual/mcp.json" --strict-mcp-config`, then `/mcp`: `polarizer` should be connected, with its tools listed.
3. **Terminal B:** `scripts/hold-check.sh deny`. It prints a request to type to Claude: a call to `fs__write_file` for a new file under `/tmp/polarizer-manual/.git/hooks`. It then waits with `polarizer holds --wait --bell` until the call is held, rings the terminal's bell, and shows the hold with its exact arguments. The reason should be `write-pattern`, naming `.git/hooks/**`. Answer `y` if it is the call you asked for; the script denies it, then asks what Claude Code showed.
4. **Terminal B:** `scripts/hold-check.sh allow`. The same request again; the script allows the new hold, checks that the forwarded call returned `ok` and that the file holds the text asked for, and asks what Claude Code showed.
5. **Terminal B:** `scripts/hold-check.sh long-wait`. One more write, held. Leave it: the script waits 65 seconds, printing how long the call has been held, then asks what Claude Code showed while it waited and whether you could type in terminal A. Type `yes` to allow it; it asks what Claude Code showed when the call finished. This is the long wait of docs/HOLD-SPEC.md, section 9. With Claude Code 2.1.289, a call still waiting after about 123 seconds is moved to the background as a task and Claude carries on; the result arrives as a task notification when you allow it.
6. **Terminal B:** `scripts/hold-check.sh expire`. It sets `hold_timeout_seconds = 30` in `polarizer.toml` and asks you to reconnect `polarizer` from `/mcp` in terminal A, so it starts again with that timeout; it waits until the ledger shows the new start. Then it prints a request for `fs__move_file`, which is held on every call. Do not decide it: after 30 seconds the hold expires. The script checks that the file was not moved and asks what Claude Code showed and how long the call appeared to run.
7. **Terminal A:** exit Claude Code. **Terminal B:** `scripts/hold-check.sh finish`. It verifies the ledger, lists any open holds, writes `polarizer.toml` again from the example, looks for a leftover probe or Filesystem server, and runs `scripts/guard.sh check`. Then paste the output of `cat /tmp/hold-check-results.txt`.

Before each call reaches Polarizer, Claude Code may ask whether to allow the `polarizer` tool: that is Claude Code's own permission prompt (docs/HOLD-SPEC.md, section 1). Allow it there; the hold comes after. If Claude writes the file with its own file tools instead of `fs__write_file`, nothing reaches Polarizer and the script keeps waiting: press Ctrl+C in terminal B, ask Claude again to use the polarizer tool, and run the step again. If Claude retries a call it was not allowed, the retry is a new hold; the script says so and prints the complete `polarizer deny` command that ends it.

What it shows:

- **A held write never reaches the server until you allow it.** After the deny, the file does not exist; after the allow, it holds exactly the text asked for, and the ledger has `hold.created`, `hold.decided` and `call.sent` with `allowed_by` `hold`.
- **What Claude Code shows** for a denied call, an allowed one, one that waited more than a minute, and one that timed out, in your own words. Nothing else records this: Polarizer never sees Claude Code's screen.
- **A timeout refuses the call:** `hold.expired` with `timeout after 30 s`, then `call.refused`, and the file is not moved.

What it does not show:

- **Esc during a hold.** `tests/test_hold_restart.py` checks it with the two signals Claude Code was seen to send; the interactive check does not ask you to press Esc while a call is held.
- **Claude Code's own limits** on a call that waits for many minutes: the hold timeout here is 300 seconds at most, under the idle timeout read from the binary (docs/HOLD-SPEC.md, section 9).
- **Other servers and other platforms.** Only the pinned Filesystem server and the probe, on the machine you run it on.
