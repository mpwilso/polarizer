# Polarizer

[![ci](https://github.com/mpwilso/polarizer/actions/workflows/ci.yml/badge.svg)](https://github.com/mpwilso/polarizer/actions/workflows/ci.yml)

## What it is

Polarizer is a local MCP gateway that sits between an AI agent and the MCP servers it uses. It pins each tool's definition so a server can't change it unnoticed, holds risky calls until a person allows them, and records every call in a hash-chained ledger that anyone can verify. It runs on your own machine as a stdio MCP server that your client, such as Claude Code, starts.

## Status

Version 0.1, a preview. It is not on PyPI.

Built:

- **M0:** the pass-through proxy and the verifiable ledger.
- **M1a:** pins on tool definitions.
- **M2a:** tool classes, and holds for risky calls.

Planned and not built:

- **M2b:** taint (holding outgoing calls after the agent has read untrusted content) and rules on argument values and domains.
- **M3:** an approval card, a local page that shows a held call and what it would change.
- **M5 and M6:** oversight statistics and canaries.

The goal of the project is to measure whether a person's approvals still catch anything: how often they allow, how long they look, and whether they notice a planted bad call. **That measurement does not exist yet.** Today Polarizer holds calls and records what the person decided; nothing yet tells you whether those decisions were any good. [docs/milestones.md](docs/milestones.md) has the whole plan.

It has been used only with Claude Code (versions 2.1.287 to 2.1.289) as the client, and every live check ran on Linux (WSL2).

## What it does not do

Polarizer only sees calls routed through it; the agent's own shell, its file tools and MCP servers configured directly in the client are outside it, and an agent whose call is refused can try another route itself. In the M2a check, the model whose move was refused named Bash `mv` as a route it chose not to take; nothing in Polarizer would have stopped it.

**Scope**

- Tools only. Resources, prompts and completions from upstream servers are not exposed.
- Requests from an upstream server to the client (elicitation, sampling, roots) are not passed on, and the tool call fails.
- No scanning of tool descriptions, no rules on argument values or domains, and no taint. A call that no rule holds goes straight through, recorded in the ledger.
- Results pass through the MCP Python SDK, which drops fields the protocol doesn't define and turns a result it can't parse into an error. Tools are served in the 2026-07-28 form, without `execution` or `_meta`. [docs/PROXY-SPEC.md](docs/PROXY-SPEC.md#results) lists each change.
- Each upstream starts once per session. One that fails to start stays off until the client restarts Polarizer. An upstream whose tool listing fails has its tools hidden until a listing succeeds again (Polarizer retries after 30 seconds, then less often, up to every 5 minutes); one whose process exits stays hidden until Polarizer restarts.

**Definitions, not behavior**

- Pins check a tool's definition (name, description, parameters, annotations), not what the tool does. A server can change its behavior without changing its definition, and Polarizer won't notice.
- If you approve a poisoned definition without reading it, Polarizer keeps serving exactly that definition.
- A class is your statement about a tool. Polarizer can't check it, and a tool can reach files in ways that never appear in its arguments (its own configuration, its working directory, a path it computes, a process it starts).
- A decision is not instant. A call already under way when you reject its tool is not stopped.

**Paths**

- Only the top-level arguments you name in `path_args` are checked. A path nested inside an argument (a list of edits, each with its own path) is not checked, and a path inside free text (a shell command, a URL, a script) never is.
- Paths are resolved when the call arrives. A symbolic link created or changed after that is not seen, and an upstream that sees a different file system (a container, another machine) resolves paths its own way.
- A `~` in a hold pattern means the home directory in Polarizer's own `HOME` when it starts, not necessarily your account's home.
- The built-in patterns protect Claude Code's settings in their default place. If you move them with `CLAUDE_CONFIG_DIR`, the patterns don't follow; add the new place to `write_hold_patterns`.
- On Windows, the device names `COM1` to `COM3` and `LPT1` to `LPT3` written with a superscript digit are not recognized as device names.
- `polarizer.example.toml` uses POSIX paths. On native Windows, edit each one to a real absolute path with a drive, such as `C:/Users/<you>/...`; a `workspace_roots` entry without a drive is refused there as not absolute.

**Annotations**

- A tool's own annotations (`readOnlyHint`, `destructiveHint`, `openWorldHint`) come from the server. Polarizer shows them as a hint and uses them only for a server you mark with `trust_annotations`, which then chooses its own tools' classes.

**Held calls and Claude Code**

- A held call waits inside the Polarizer process that Claude Code started. Pressing Esc in Claude Code stops that process and ends every call it was holding: none of them reaches the server, and the ledger says so. Don't press Esc to go and decide a hold.
- If you allow a call and that process dies before it forwards it, the call does not run.
- In Claude Code 2.1.289, a call still running after about 123 seconds is moved to the background, and the agent keeps working. It can make other calls, or ask for the same call again, which is a new hold. The first hold is unchanged: nothing runs until you allow it, and it is refused at the timeout. This was observed once and may differ in other versions.
- Claude Code's own permission prompt for an MCP tool comes before the call reaches Polarizer, so a held call may be approved twice: once in Claude Code, without the details, and once with `polarizer allow`.
- If `MCP_TOOL_TIMEOUT` is set below the hold timeout, Claude Code cancels the call first.
- There is no page or notification for holds yet. Run `polarizer holds --wait` in another terminal.

**People**

- A hold is only as good as the person reading it. If you allow without reading, Polarizer records that you allowed it, and nothing measures that yet.
- An agent that can run commands as you can also run `polarizer approve` or `polarizer allow`. They refuse to run without a terminal unless told they are run by a script, which only stops accidents.

**The ledger**

- Someone who can write to the ledger directory can rewrite the ledger and its head together. There is no anchoring or signature yet.
- Call arguments are kept in side files in plain text, protected only by file permissions (0600 in a 0700 directory). Deleting one removes it, and the chain still verifies.
- Each entry's time is the wall clock. The chain proves order, not when anything happened.

## Quickstart (Linux, WSL and macOS)

You need Python 3.11 or later, [uv](https://docs.astral.sh/uv/), Node.js for `npx` servers, and Claude Code.

### 1. Install

```sh
uv tool install git+https://github.com/mpwilso/polarizer
polarizer --help
```

This works once the repository is public. It installs Polarizer into its own environment and puts `polarizer` on your `PATH` (`uv tool dir --bin` prints where). Polarizer pins its direct dependencies exactly; the full set it was tested with is in `uv.lock`, which `uv tool install` does not read.

### 2. Write polarizer.toml

Make a directory for the config, and a workspace for the agent to write in:

```sh
mkdir -p ~/.config/polarizer ~/projects/demo
CONFIG="$HOME/.config/polarizer/polarizer.toml"
```

Save this as `~/.config/polarizer/polarizer.toml`, with `/home/you` replaced by your home directory (`echo $HOME` prints it; on macOS it is `/Users/<you>`). It is [polarizer.example.toml](polarizer.example.toml) cut down to the reference Filesystem server, pinned to an exact version:

```toml
[policy]
# local-write calls run without a hold only inside these directories.
workspace_roots = ["/home/you/projects/demo"]
# A held call is refused if nobody decides within this many seconds.
hold_timeout_seconds = 300

[upstream.fs]
command = "npx"
args = ["-y", "@modelcontextprotocol/server-filesystem@2026.8.31", "/home/you/projects/demo"]
connect_timeout_seconds = 20

[upstream.fs.tools]
read_file = { class = "local-read", path_args = ["path"] }
read_text_file = { class = "local-read", path_args = ["path"] }
read_media_file = { class = "local-read", path_args = ["path"] }
read_multiple_files = { class = "local-read", path_args = ["paths"] }
list_directory = { class = "local-read", path_args = ["path"] }
list_directory_with_sizes = { class = "local-read", path_args = ["path"] }
directory_tree = { class = "local-read", path_args = ["path"] }
search_files = { class = "local-read", path_args = ["path"] }
get_file_info = { class = "local-read", path_args = ["path"] }
list_allowed_directories = { class = "local-read" }
write_file = { class = "local-write", path_args = ["path"] }
edit_file = { class = "local-write", path_args = ["path"] }
create_directory = { class = "local-write", path_args = ["path"] }
move_file = { class = "destructive", path_args = ["source", "destination"] }
```

The ledger goes in `~/.local/share/polarizer` unless you set `ledger_dir`. [docs/PROXY-SPEC.md](docs/PROXY-SPEC.md#configuration-polarizertoml) lists every key, including `ledger_forbidden_paths` for directories the ledger must never be written to.

**Classifying tools.** Each `[upstream.<prefix>]` table starts one MCP server, and its tools are exposed as `<prefix>__<tool>`. Under `[upstream.<prefix>.tools]`, give each tool a class:

- `local-read` reads local data and changes nothing. It runs, unless a path matches a read hold pattern such as `~/.ssh/**`.
- `local-write` changes local files. It runs when every path in its `path_args` resolves inside a workspace root and matches no write hold pattern (such as `.git/hooks/**` or your shell start-up files); otherwise it is held.
- `destructive` deletes, moves or overwrites. It is held on every call.
- `open-world` reads from outside the machine. It runs, under the same read hold patterns.
- `egress` sends data out or acts outside the machine. It is held on every call.

A tool with no class is held on every call, and `serve` says at start how many have none. `path_args` names the arguments that hold paths. [docs/HOLD-SPEC.md](docs/HOLD-SPEC.md#2-classes-and-the-policy-in-polarizertoml) has every key, including `write_hold_patterns` and `read_hold_patterns`.

**Pre-warm the server once.** The first `npx` fetch can take longer than Polarizer's connect timeout, so run it by hand once and let it exit:

```sh
npx -y @modelcontextprotocol/server-filesystem@2026.8.31 "$HOME/projects/demo" < /dev/null
```

### 3. First run, without Claude Code

No tool is exposed until you approve its definition. With its input closed, `serve` connects to every upstream, records each tool definition it lists, and exits:

```sh
polarizer serve --config "$CONFIG" < /dev/null
polarizer pending --config "$CONFIG"
polarizer approve --config "$CONFIG" --group <group id>
polarizer verify --config "$CONFIG"
```

- `serve` ends with `polarizer: <n> tools wait for approval; run polarizer pending`.
- `pending` prints every waiting definition in full, any character outside printable ASCII shown as an escape, with each tool's class next to what its annotations suggest. Read them. A line near the end names a group: `group <group id> covers the <n> new definitions above ...`.
- `approve --group` approves exactly the definitions `pending` printed. If anything changed in between, it refuses, and you run `pending` again. One definition is approved by name instead: `polarizer approve --config "$CONFIG" <prefix> <tool> <hash>`.
- `verify` prints `intact: ...` when the chain checks out.

`approve`, `allow` and `deny` refuse to run without a terminal unless given `--allow-no-terminal`, so that an agent doesn't approve things by accident.

### 4. Add it to Claude Code

Save this as `~/.config/polarizer/mcp.json`, with `command` set to the path that `command -v polarizer` prints and `/home/you` replaced as before. `--config` must be an absolute path.

```json
{
  "mcpServers": {
    "polarizer": {
      "command": "/home/you/.local/bin/polarizer",
      "args": ["serve", "--config", "/home/you/.config/polarizer/polarizer.toml"]
    }
  }
}
```

Then start Claude Code with that file only:

```sh
claude --strict-mcp-config --mcp-config "$HOME/.config/polarizer/mcp.json"
```

Approve the `polarizer` server if asked. `/mcp` lists the approved tools with their prefix, such as `fs__read_text_file`.

Pass the file with `--mcp-config`; don't put Polarizer in a project `.mcp.json`. A project `.mcp.json` starts Polarizer in every Claude Code session opened in that directory, including ones you never meant to route through it, and each of those writes to the ledger. That happened while Polarizer was built: development sessions in its own repository started it three times and added 12 entries to a real ledger ([docs/verified-facts.md](docs/verified-facts.md#development-sessions-spawned-the-proxy-from-the-ledger-not-interactive)). With `--strict-mcp-config`, Claude Code uses only the servers in that file and ignores its other MCP configurations, so servers configured elsewhere are not loaded beside Polarizer. Whether that flag also turns off claude.ai connectors is not verified.

Later, `polarizer pending --config "$CONFIG"` shows anything new or changed. A running Polarizer notices an approval within about a second and tells Claude Code, which lists the tool again without a reconnect.

### 5. Holds, in a second terminal

Ask Claude to move a file in `~/projects/demo` with the Filesystem tools. `move_file` is `destructive`, so the call is held. In a second terminal:

```sh
CONFIG="$HOME/.config/polarizer/polarizer.toml"
polarizer holds --config "$CONFIG" --wait --bell
polarizer allow --config "$CONFIG" <hold id>
polarizer deny --config "$CONFIG" <hold id> --reason "not that file"
```

- `holds --wait` waits until a call is held, then lists every waiting call with its exact arguments, every unusual character escaped, and exits. `--bell` rings the terminal first. Run it again for the next hold. Without `--wait`, `holds` lists what waits now.
- `allow` forwards the call. `deny` refuses it; the agent sees only `polarizer: <tool> was not allowed`. Use one or the other for each hold.
- If you do neither, the call is refused after `hold_timeout_seconds`.

`polarizer verify --config "$CONFIG" --args` also checks the argument side files. To run one session with every hold rule off, start `polarizer serve --config <path> --no-holds`; it is a command-line flag only, never a config key, and every start with it is recorded in the ledger. Pins still apply.

## How it works

**The ledger and verify.** Every entry is one line of canonical JSON (RFC 8785) holding its sequence number, time, kind, data, the previous entry's hash and its own hash, so changing, removing or reordering any line breaks the chain. The first entry binds a random chain id. `ledger.head` records the latest security-relevant entry, so losing lines from the end is caught too. Call arguments are kept out of the chain, in salted side files that the chain commits to. `polarizer verify` reports exactly one status (`intact`, `tampered`, `invalid`, `not canonical`, `torn tail` or `truncated`), each with its own exit code, and never writes. A standalone verifier, `conformance/reference_verify.py`, implements the format from the spec alone, and the two verifiers are tested against each other. Detail: [docs/LEDGER-SPEC.md](docs/LEDGER-SPEC.md).

**The proxy.** `serve` starts each upstream, exposes its tools with a prefix, forwards calls with their arguments unchanged, and records `call.sent` and `call.returned` (or `call.refused`) for each. Detail: [docs/PROXY-SPEC.md](docs/PROXY-SPEC.md).

**Pins and drift.** A tool's definition is hashed and a copy is stored. A tool is exposed only while its latest decision is an approval of the hash it has now, and the agent is served the stored copy. When a server's definition changes, the tool is hidden, `tool.drift` is recorded with both hashes, and Claude Code is told the tool list changed. The tool stays hidden until you approve the new definition. A hidden tool called by name is refused and recorded. Startup refuses a ledger that is not intact, so a lost rejection can't bring a tool back. Detail: [docs/PIN-SPEC.md](docs/PIN-SPEC.md).

**The hold flow.** For each call to an approved tool, one rule function decides from its class and its resolved paths whether it runs or is held. A held call's arguments are written to a side file and `hold.created` is recorded with a hold id; the call waits inside `serve`, and nothing reaches the server. It ends in one of five ways, each recorded:

- **allow:** `hold.decided`, fsynced before the call is forwarded, then `call.sent` and `call.returned`;
- **deny:** `hold.decided`, then `call.refused`;
- **expiry** after `hold_timeout_seconds`: `hold.expired`, then `call.refused`;
- **the client's cancel**, such as Claude Code's own timeout: `hold.expired`, then `call.refused`;
- **Polarizer's shutdown**, such as Esc in Claude Code: `hold.expired`, then `call.refused`.

A new `serve` records `hold.abandoned` for the open holds a dead process left behind. Detail: [docs/HOLD-SPEC.md](docs/HOLD-SPEC.md).

## Evidence

What has been checked, and where each result is recorded. Nothing below has been run on native Windows or macOS with Claude Code, with any other client, or with a third-party MCP server other than the reference servers named.

- **CI.** Five jobs: Linux (ubuntu-24.04) with Python 3.11, 3.12 and 3.13, and Windows and macOS with Python 3.12. All five passed at commit 9270c97 ([docs/milestones.md](docs/milestones.md#status), M2a). On Linux (WSL2, Python 3.12.3), `scripts/test.sh` ran ruff, the docs check and 784 tests passed, 13 skipped, at that commit ([docs/dev/STAGE7-NOTES.md](docs/dev/STAGE7-NOTES.md#follow-up-macos-output-bash-32-wait)). The per-job test counts in CI are not recorded.
- **Two verifiers.** Polarizer's verifier and the standalone reference verifier agree on every conformance fixture, and on 5,000 randomly damaged chains per test run; locally, also on 20,000 cases each for seeds 1 to 5, with no disagreement ([docs/dev/m0-plan.md](docs/dev/m0-plan.md#tests-and-claims)).
- **Reference servers.** The pinned Everything and Filesystem servers (2026.8.31) ran through Polarizer locally: all 27 tools hashed, and each reached clients exactly as its stored copy ([docs/verified-facts.md](docs/verified-facts.md#stage-4-oct-3-2026)).
- **Headless live check, M0.** `scripts/live-check.sh`, run once with Claude Code 2.1.288 and `claude -p`: a 14 s call under `MCP_TOOL_TIMEOUT=5000`. Claude Code cancelled it 5.012 s after the call, Polarizer's own cancel reached the upstream 1 ms later, and the ledger verified intact with 6 entries and the call recorded `cancelled` ([docs/verified-facts.md](docs/verified-facts.md#live-check-headless-claude-code-21288)).
- **Esc, interactive, M0.** With Claude Code 2.1.288, Esc during a 30 s call: the upstream received a cancel, and the ledger recorded `call.sent` and `call.returned` `cancelled` at seq 21 and 22 ([docs/verified-facts.md](docs/verified-facts.md#esc-during-a-long-call-interactive-claude-code-21288)).
- **Rug-pull check, headless, M1a.** `scripts/rugpull-check.sh`, run once with Claude Code 2.1.289 at commit a46cbc8: nine of nine checks passed. Run A called the tool (seq 20 and 21); in run B the server changed the definition, `tool.drift` was recorded at seq 24, the agent was not offered the tool, and nothing reached the server; after the approval at seq 26, run C's call went through (seq 30 and 31). The ledger verified intact with 32 entries. It does not show the refusal of a hidden tool called by name (the model never tried; tests cover it), and it ran from inside a Claude Code session ([docs/verified-facts.md](docs/verified-facts.md#rug-pull-check-oct-4-2026-headless-claude-code-21289)).
- **M1a check, interactive.** With Claude Code 2.1.289, approving a first group and then a changed definition while a session was open: `/mcp` listed the tools again without a reconnect both times, and the ledger verified intact with 53 entries. Its seq ranges are not recorded ([docs/verified-facts.md](docs/verified-facts.md#m1a-check-interactive-oct-4-2026-observed-by-the-owner)).
- **M2a check, interactive.** With Claude Code 2.1.289 at commit c986088, from two terminals: a write to `.git/hooks` was held and denied, and the file was not created (seq 52 to 54); the same call asked again was a new hold, allowed, and written (seq 55 to 58); a write held for 128 s was allowed and returned, after Claude Code reported moving it to the background at 123 s (seq 59 to 62); and a `move_file` held as destructive expired after 30 s without running (seq 68 to 70). The ledger verified intact with 71 entries in 3 sessions ([docs/verified-facts.md](docs/verified-facts.md#m2a-check-interactive-oct-5-2026-utc-observed-by-the-owner)).

docs/verified-facts.md also lists what is not verified, such as how Claude Code ends a backgrounded held call when the session exits.

## How it was built

Polarizer was designed and directed by Matt Wilson. Claude Code wrote most of the code under that direction, in staged rounds: a spec for each milestone, tests written first, and a review at each stop before the next stage began. The specs and the build log are in [docs/](docs/) and [docs/dev/](docs/dev/README.md). It was built independently of any employer system, on personal time and equipment.

**Credits.** From mcpclerk, three ideas, credited by name, with none of its code or text used: a run-end entry that records how many entries the run wrote, which inspired `ledger.head`; tools hidden from the list that are still refused and recorded when called by name (M1a); and an approve-everything switch that exists only as a command-line flag, never in the policy file (M2a, `--no-holds`). Polarizer is built on the MCP Python SDK and the `rfc8785` package, uses RFC 8785 (JSON Canonicalization Scheme) for its ledger entries, and is tested against the reference Everything and Filesystem MCP servers. No code from any of these projects is copied into this repository.

## License

MIT. See [LICENSE](LICENSE).
