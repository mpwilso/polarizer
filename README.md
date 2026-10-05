<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/brand/lockup-dark.svg">
    <img src="docs/brand/lockup-light.svg" alt="Polarizer: a round filter of parallel lines, with one line turned out of line" height="72">
  </picture>
</p>

<p align="center"><b>Agents call the tools. You decide the risky ones.</b></p>

<p align="center"><a href="https://github.com/mpwilso/polarizer/actions/workflows/ci.yml"><img src="https://github.com/mpwilso/polarizer/actions/workflows/ci.yml/badge.svg" alt="CI status"></a></p>

Polarizer is a local gateway between an AI agent and the MCP servers it uses (MCP is the protocol AI agents use to call outside tools). It lets routine calls through, holds the risky ones until a person allows them, and writes every call and decision to a ledger that anyone can check. It runs on your own machine, started by the agent's client, such as Claude Code.

Status: a portfolio project, built to show how I design, test and judge an AI tool. Version 0.1, a preview.

Jump to [an example hold](#what-a-hold-looks-like), [the proof](#proof), [the limits](#known-limits), [setup](#setup) or [how it was built](#how-it-was-built).

## Why it exists

An MCP server can change a tool's description after you trusted it. A permission prompt that names only the tool can't tell a write inside your project from a write to `.git/hooks`, where git runs code. And a person who approves prompt after prompt stops reading. Polarizer pins what you approved, holds calls by what they would touch, and records every decision so it can be checked later.

The next milestones measure whether a person's approvals still catch anything. That measurement does not exist yet.

## What a hold looks like

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

For the denied call, the agent only ever saw `polarizer: fs__write_file was not allowed`. Both blocks are copied from the check's results file, as recorded in [docs/verified-facts.md](docs/verified-facts.md#the-deny-and-allow-steps-as-printed).

## What's different

- **A changed tool disappears until you approve it again.** If a server changes a tool's definition after you approved it, the agent stops seeing that tool until you approve the new definition.
- **Holds look at what a call would touch.** The tool's class and the call's resolved paths decide, not the tool's name.
- **You see the exact call; the agent learns nothing.** A held call shows its exact arguments, every unusual character escaped. A refused call tells the agent only that it was not allowed.
- **Every call, hold and decision goes into a hash-chained ledger,** and a separately written verifier checks it as well as Polarizer's own.
- **It fails closed.** A tool with no class is held on every call, and a ledger that doesn't verify stops startup.

## Compared with Claude Code's permission prompts

Claude Code's own prompt for an MCP tool comes before the call reaches Polarizer, without the call's details. Polarizer adds what that prompt doesn't show: whether the tool's definition changed since you approved it, which paths the call would touch, and its exact arguments, with a ledger of what you decided. It has only been used with Claude Code, so nothing here says it works with other clients.

## Proof

- **CI on five jobs:** Linux with Python 3.11, 3.12 and 3.13, Windows and macOS, all passing at 9270c97, the last commit CI has run; locally, 784 tests passed and 13 skipped at that commit. ([milestones](docs/milestones.md#status), [stage 7 notes](docs/dev/STAGE7-NOTES.md#follow-up-macos-output-bash-32-wait))
- **Two verifiers agree** on every conformance fixture and on 5,000 randomly damaged chains per test run. Both were written separately from the same spec by the same builder, so a misreading they share isn't ruled out; the RFC 8785 test vectors, written out by hand from the RFC, check the canonical bytes independently. ([claims table](docs/dev/m0-plan.md#tests-and-claims), [RFC 8785 record](docs/verified-facts.md#rfc-8785-text-fetched-oct-2-2026))
- **A rug pull is caught on real Claude Code:** the rug-pull check passed twice with Claude Code 2.1.289, 9 of 9 checks each time, once inside a Claude Code session and once from a plain terminal. A changed definition was hidden from the agent and reached nothing until it was approved. ([first run](docs/verified-facts.md#rug-pull-check-oct-4-2026-headless-claude-code-21289), [second run](docs/verified-facts.md#rug-pull-check-second-run-oct-4-2026-headless-plain-terminal-claude-code-21289))
- **Approvals reach a running session:** in the interactive M1a check, `/mcp` listed newly approved tools without a reconnect, for a first approval and for a changed definition; the ledger verified intact with 53 entries. ([M1a check](docs/verified-facts.md#m1a-check-interactive-oct-4-2026-observed-by-the-owner))
- **Every kind of hold ending, interactively:** in the M2a check, a denied write (seq 52 to 54), an allowed retry (55 to 58), a write held 128 s and then allowed (59 to 62), and a destructive move that expired after 30 s without running (68 to 70); 71 entries, intact. ([M2a check](docs/verified-facts.md#m2a-check-interactive-oct-5-2026-utc-observed-by-the-owner))
- **Claude Code's own timeout reaches the server:** in the headless live check, Claude Code cancelled a 14 s call after 5.012 s, Polarizer's cancel reached the upstream 1 ms later, and the ledger recorded `cancelled`. ([live check](docs/verified-facts.md#live-check-headless-claude-code-21288))

Not covered: native Windows or macOS with Claude Code, any client but Claude Code, and third-party MCP servers beyond the reference ones. Every claim, in full: [docs/EVIDENCE.md](docs/EVIDENCE.md).

## How it works

<picture><source media="(prefers-color-scheme: dark)" srcset="docs/img/how-it-works-dark.svg"><img src="docs/img/how-it-works-light.svg" alt="How Polarizer works: the agent, Claude Code, sends every tool call to polarizer serve. There, pins list only the tool definitions you approved, a rule function looks at the tool's class and the call's resolved paths, and a risky call is held until you allow or deny it or it times out. Allowed calls go on to the MCP servers. Every call, hold and decision goes into a hash-chained ledger. You run pending, approve, holds, allow, deny and verify in a terminal; they read the ledger and write your decisions to it. The agent's own shell and file tools are not routed through Polarizer." width="680"></picture>

Polarizer exposes each server's tools with a prefix, such as `fs__write_file`, and serves the agent only the definitions you approved, from a stored copy. When a server's definition changes, the tool is hidden, the change is recorded, and Claude Code is told its tool list changed. For each call to an approved tool, one rule function decides from the tool's class and the call's resolved paths whether it runs or waits for you. A held call ends in one of five ways, each recorded: allow, deny, expiry, the client's cancel, or Polarizer's shutdown.

Every entry in the ledger is one line of canonical JSON (RFC 8785) holding the previous entry's hash, so changing, removing or reordering a line breaks the chain, and `ledger.head` catches lines lost from the end. Call arguments stay out of the chain, in salted side files it commits to. `polarizer verify` reports one status with its own exit code and never writes. The specs: [ledger](docs/LEDGER-SPEC.md), [proxy](docs/PROXY-SPEC.md), [pins](docs/PIN-SPEC.md) and [holds](docs/HOLD-SPEC.md).

## Known limits

Polarizer only sees calls routed through it: the agent's shell, its own file tools and servers configured directly in the client are outside it, and an agent whose call is refused can try another route. In the M2a check, the model whose move was refused named Bash `mv` as a route it chose not to take; nothing in Polarizer would have stopped it.

- **Definitions, not behavior.** A server can change what a tool does without changing its definition.
- **Paths only in the arguments you name.** Only top-level arguments listed in `path_args` are checked, not paths nested inside an argument or written in free text.
- **Esc ends held calls.** Esc in Claude Code stops the Polarizer process, and every call it was holding is refused.
- **Backgrounded after about 123 s.** In Claude Code 2.1.289 a waiting call moves to the background and the agent keeps working; the hold still waits for you.
- **Tried only with Claude Code, on Linux (WSL2).** CI runs on Windows and macOS; no live check has.
- **A person who allows without reading.** Polarizer records that you allowed it, and nothing measures that yet.

Every limit, grouped: [docs/LIMITS.md](docs/LIMITS.md).

## Setup

For Linux, WSL and macOS. You need Python 3.11 or later, [uv](https://docs.astral.sh/uv/), Node.js for `npx` servers, and Claude Code.

```sh
uv tool install git+https://github.com/mpwilso/polarizer
polarizer --help
mkdir -p ~/.config/polarizer ~/projects/demo
CONFIG="$HOME/.config/polarizer/polarizer.toml"
```

`uv tool install` works once the repository is public; it doesn't read `uv.lock`, which holds the full set of versions Polarizer was tested with. Save this as `~/.config/polarizer/polarizer.toml`, with `/home/you` replaced by your home directory (on macOS, `/Users/<you>`). It is [polarizer.example.toml](polarizer.example.toml) without its comments: the reference Filesystem server, pinned to an exact version.

```toml
[policy]
workspace_roots = ["/home/you/projects/demo"]
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

Each tool gets a class. `local-read` and `open-world` run unless a path matches a read hold pattern such as `~/.ssh/**`; `local-write` runs only when every path in `path_args` resolves inside a workspace root and matches no write hold pattern, such as `.git/hooks/**`; `destructive` and `egress` are held on every call, and so is a tool with no class. [docs/HOLD-SPEC.md](docs/HOLD-SPEC.md#2-classes-and-the-policy-in-polarizertoml) has every key. Run the server once by hand first, since its first `npx` fetch can miss Polarizer's connect timeout:

```sh
npx -y @modelcontextprotocol/server-filesystem@2026.8.31 "$HOME/projects/demo" < /dev/null
```

**First run, without Claude Code.** No tool is exposed until you approve its definition. With its input closed, `serve` records what each server lists and exits:

```sh
polarizer serve --config "$CONFIG" < /dev/null
polarizer pending --config "$CONFIG"
polarizer approve --config "$CONFIG" --group <group id>
polarizer verify --config "$CONFIG"
```

`serve` ends with `polarizer: <n> tools wait for approval; run polarizer pending`. `pending` prints every definition in full, unusual characters escaped, and a group line near the end: `group <group id> covers the <n> new definitions above ...`. A group approves exactly what `pending` printed; one definition is approved by name with `polarizer approve --config "$CONFIG" <prefix> <tool> <hash>`. `verify` prints `intact: ...` when the chain checks out. `approve`, `allow` and `deny` refuse to run without a terminal unless given `--allow-no-terminal`.

**Claude Code.** Save this as `~/.config/polarizer/mcp.json`, with `command` set to what `command -v polarizer` prints, and start Claude Code with it only:

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

```sh
claude --strict-mcp-config --mcp-config "$HOME/.config/polarizer/mcp.json"
```

Don't put Polarizer in a project `.mcp.json`: every Claude Code session opened in that directory would start it and write to its ledger, which happened while Polarizer was built ([record](docs/verified-facts.md#development-sessions-spawned-the-proxy-from-the-ledger-not-interactive)). Pass the file with `--mcp-config` instead.

**Holds, in a second terminal.** Ask Claude to move a file in `~/projects/demo`; `move_file` is `destructive`, so the call is held.

```sh
CONFIG="$HOME/.config/polarizer/polarizer.toml"
polarizer holds --config "$CONFIG" --wait --bell
polarizer allow --config "$CONFIG" <hold id>
polarizer deny --config "$CONFIG" <hold id> --reason "not that file"
```

`holds --wait` waits for a held call, lists every waiting call with its exact arguments, and exits, and `--bell` rings the terminal first; run it again for the next one. Use `allow` or `deny` for each hold: a denied call tells the agent only `polarizer: <tool> was not allowed`, and an undecided one is refused after `hold_timeout_seconds`. `polarizer serve --config <path> --no-holds` turns the hold rules off for one session; it is a flag only, recorded in the ledger at every start, and pins still apply.

## What's here

- `src/polarizer/`: the proxy, the ledger, pins, holds and the command line.
- `conformance/`: the ledger fixtures and the separately written reference verifier.
- `tests/`: every test; `tests/test_readme.py` runs this README's setup commands.
- `docs/`: the four specs, [the evidence](docs/EVIDENCE.md), [the limits](docs/LIMITS.md) and [the verified facts](docs/verified-facts.md). `docs/dev/` is the build log.
- `docs/brand/`: the logo, drawn by `scripts/brand.py`. The parallel lines are calls that line up with what you approved; the one turned out of line is held ([what the mark means](docs/brand/README.md)).
- `manual/`: the config for the interactive checks.
- `scripts/`: `test.sh` runs every check, as CI does; the live and manual check scripts; `dev/guard.sh`, a guard for my own machine.

## How it was built

I designed Polarizer and directed its build. Claude Code wrote most of the code, in staged rounds: a spec for each milestone, tests written first, and a review at each stop before the next stage began. I ran the interactive checks and both rug-pull checks on real Claude Code myself. It was built independently of any employer system, on personal time and equipment. The specs, stage notes and reviews are in [docs/dev/](docs/dev/README.md).

I also built [Loupe](https://github.com/mpwilso/loupe), [Parallax](https://github.com/mpwilso/parallax) and [ISR](https://github.com/mpwilso/isr).

Credits: from mcpclerk, three ideas, credited by name, with none of its code or text used: a run-end entry that records how many entries the run wrote, which inspired `ledger.head`; tools hidden from the list that are still refused and recorded when called by name; and an approve-everything switch that exists only as a command-line flag, never in the policy file (`--no-holds`). Polarizer is built on the MCP Python SDK and the `rfc8785` package, uses RFC 8785 (JSON Canonicalization Scheme) for its ledger entries, and is tested against the reference Everything and Filesystem MCP servers. No code from any of these projects is copied here.

## What's next

1. **The approval card (M3):** a local page that shows a held call, what it would change, why it was held and its history.
2. **Taint (M2b):** holding outgoing calls after the agent has read untrusted content, and rules on argument values and domains.
3. **The measurement (M5 and M6), the point of the project:** oversight statistics and canaries that show whether a person's approvals still catch a planted bad call.

MIT license: [LICENSE](LICENSE).
