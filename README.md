<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/brand/lockup-dark.svg">
    <img src="docs/brand/lockup-light.svg" alt="Polarizer: a round filter of parallel lines, with one line turned out of line" height="72">
  </picture>
</p>

<p align="center"><b>Human in the loop only works if the human is still looking.</b></p>

<p align="center"><img src="docs/img/demo.svg" alt="A terminal session: polarizer holds shows an agent's write to .git/hooks/pre-commit, held by the .git/hooks pattern with its exact arguments; polarizer deny refuses it, and the agent is told only that the call was not allowed" width="720"></p>

<p align="center"><a href="https://github.com/mpwilso/polarizer/actions/workflows/ci.yml"><img src="https://github.com/mpwilso/polarizer/actions/workflows/ci.yml/badge.svg" alt="CI status"></a></p>

Polarizer is a local MCP gateway: it sits between an AI agent and the MCP servers it uses (MCP is the protocol AI agents use to call outside tools), pins the tool definitions you approved, holds risky calls until a person allows them, and keeps a hash-chained ledger that `polarizer verify` checks. It also includes drills, offline practice sessions in which a person allows or denies invented calls and measures how well they catch the planted mistakes. It runs on your own machine, started by the agent's client, such as Claude Code. It guards only the calls that pass through it, not the agent's own shell or file tools.

Status: v0.2, a preview. Built: the gateway and its ledger, pins on tool definitions, holds by tool class and path, and drills ([docs/MEASURE-SPEC.md](docs/MEASURE-SPEC.md)). Not built: statistics over real holds, live planted calls inside a running gateway, holds after the agent reads untrusted content, and an approval page. Drills are practice sessions with invented scenarios, not measurements of a person's real approvals, and no results from people exist yet beyond the author's own tool checks.

Jump to [a drill](#try-a-drill), [an example hold](docs/EVIDENCE.md#the-hold-example), [the proof](#proof), [the limits](#known-limits), [setup](#setup) or [how it was built](#how-it-was-built).

## Why it exists

An MCP server can change a tool's description after you trusted it. A permission prompt that names only the tool can't tell a write inside your project from a write to `.git/hooks`, where git runs code. And a person who approves prompt after prompt stops reading ([sources](docs/MEASURE-SPEC.md#2-sources)). Polarizer pins what you approved, holds calls by what they would touch, and records every decision so it can be checked later. A hold only helps if the person still reads it. Drills are the first part of measuring that, under practice conditions; measuring it during real work is not built yet.

## Try a drill

A drill shows you invented agent calls one at a time, under the task the agent was given, and you allow or deny each; some were changed to be wrong on purpose, and after each answer you see whether you were right and why. It runs offline: no agent, no account, no MCP server, nothing sent anywhere.

One call from a guided drill, trimmed, and the answer after allowing it:

```text
call 3 of 10
task: Add a "Running the tests" section to README.md in the garden-planner project.
hold 0000000000000003 fs__edit_file local-write
held by outside-roots: argument "path": /home/river/code/garden-planner/README.md is outside every workspace root
...
In plain words: Edit README.md in the garden-planner project, adding a "Running the tests" section saying "Run make test." above the License heading.
allow or deny?
```

```text
Clean call. You allowed it.
why: The edit adds the section the task asked for, before the License heading. It is held only because the path is outside every workspace root.
```

Install Polarizer as in [Setup](#setup)'s first line, then:

```sh
polarizer drill --calls 10 --condition guided
polarizer drill report
```

A guided drill of 10 calls takes about five minutes and adds a line in plain words under each call. The report gives your catch rate and false-flag rate, each with a 95% interval. [docs/DRILL-GUIDE.md](docs/DRILL-GUIDE.md) walks through one, for readers who are not engineers.

## What's different

- **A changed tool disappears until you approve it again.** If a server changes a tool's definition after you approved it, the agent stops seeing that tool until you approve the new definition.
- **Holds look at what a call would touch.** The tool's class and the call's resolved paths decide, not the tool's name.
- **You see the exact call; the agent learns only that it was refused.** A held call shows its exact arguments, every unusual character escaped. A refused call tells the agent only that it was not allowed.
- **Every call, hold and decision goes into a hash-chained ledger,** and a separately written verifier checks it as well as Polarizer's own.
- **For the calls it sees, it fails closed:** a tool with no class is held on every call, and a ledger that doesn't verify stops startup. The agent's own shell and file tools never reach Polarizer ([Known limits](#known-limits)).

## Compared with Claude Code's permission prompts

Claude Code's own prompt for an MCP tool comes before the call reaches Polarizer, without the call's details. Polarizer adds what that prompt doesn't show: whether the tool's definition changed since you approved it, which paths the call would touch, and its exact arguments, with a ledger of what you decided. It has only been used with Claude Code, so nothing here says it works with other clients.

## Proof

- **CI on five jobs:** Linux with Python 3.11, 3.12 and 3.13, Windows and macOS.
- **Two verifiers agree** on every conformance fixture and on 5,000 randomly damaged chains per test run. Both were written from the same spec by the same builder; the RFC 8785 test vectors, written out by hand from the RFC, check the canonical bytes independently.
- **A rug pull is caught on real Claude Code:** the rug-pull check passed twice with Claude Code 2.1.289, using the repository's own test server.
- **Holds, interactively:** a write to `.git/hooks` was held, denied and never written, then allowed on a retry; a destructive move expired without running. Tests cover the other two endings.
- **Drills, checked as a tool:** in a shortcut audit, the best rule on a single surface feature scores 4 points above always answering allow. No results from people yet.

Not covered: native Windows or macOS with Claude Code, any client but Claude Code, and third-party MCP servers beyond the reference ones. Every claim, with its record and the example hold: [docs/EVIDENCE.md](docs/EVIDENCE.md).

## How it works

```text
agent (Claude Code)
  |  every MCP tool call
  v
polarizer serve  . . . . . . . . . . . .>   ledger
  pins: only the definitions you approved   every call, hold and
  rule function: the tool's class and the   decision, hash-chained
        call's resolved paths decide              ^
  holds: a risky call waits for you               |  read the ledger,
  |  allowed calls                                |  record decisions
  v                                               |
MCP servers                                 you, in a terminal: pending,
                                            approve, holds, allow, deny, verify
```

The agent's own shell and file tools don't pass through Polarizer. Polarizer exposes each server's tools with a prefix, such as `fs__write_file`, and serves the agent only the definitions you approved, from a stored copy. When a server's definition changes, the tool is hidden, the change is recorded, and Claude Code is told its tool list changed. For each call to an approved tool, one rule function decides from the tool's class and the call's resolved paths whether it runs or waits for you. A held call ends in one of five ways, each recorded: allow, deny, expiry, the client's cancel, or Polarizer's shutdown.

Every entry in the ledger is one line of canonical JSON (RFC 8785) holding the previous entry's hash, and a separate file, `ledger.head`, records the latest entry that changes what may run, such as an approval or a hold decision. `polarizer verify` detects a line that was edited, removed or reordered, and lines lost from the end back past the entry `ledger.head` records. It does not detect a rewrite by someone who can write to the whole ledger directory, since they can rewrite the ledger and its head together. Call arguments stay out of the chain, in salted side files it commits to. `polarizer verify` reports one status with its own exit code and never writes. The specs: [ledger](docs/LEDGER-SPEC.md), [proxy](docs/PROXY-SPEC.md), [pins](docs/PIN-SPEC.md), [holds](docs/HOLD-SPEC.md) and [drills](docs/MEASURE-SPEC.md).

## Related projects

Pins, holds and tamper-evident logs exist in several other MCP gateways and scanners, and Polarizer credits three of mcpclerk's ideas ([How it was built](#how-it-was-built)). What Polarizer adds is holds decided by a call's resolved path arguments and its tool class, a ledger checked by a separately written verifier and RFC 8785 test vectors, and drills that measure the person's catch rate and false-flag rate with intervals. The projects, and how they compare: [docs/related.md](docs/related.md).

## Known limits

Polarizer only sees calls routed through it: the agent's shell, its own file tools and servers configured directly in the client are outside it, and an agent whose call is refused can try another route. In the M2a check, the model whose move was refused named Bash `mv` as a route it chose not to take; nothing in Polarizer would have stopped it.

- **Definitions, not behavior.** A server can change what a tool does without changing its definition.
- **Paths only in the arguments you name.** Only top-level arguments listed in `path_args` are checked, not paths nested inside an argument or written in free text.
- **A local chain, not a signature.** Someone who can write to the ledger directory can rewrite the ledger and its head together.
- **Esc ends held calls.** Esc in Claude Code stops the Polarizer process, and every call it was holding is refused.
- **A person who allows without reading.** Polarizer records that you allowed it; only drills measure it, and only in practice.
- **Tried only with Claude Code, on Linux (WSL2).** CI runs on Windows and macOS; no live check has.

Every limit, grouped, including the ones about drills: [docs/LIMITS.md](docs/LIMITS.md).

## Setup

For Linux, WSL and macOS. You need Python 3.11 or later, [uv](https://docs.astral.sh/uv/), Node.js for `npx` servers, and Claude Code.

```sh
uv tool install https://github.com/mpwilso/polarizer/releases/download/v0.2.0/polarizer-0.2.0-py3-none-any.whl
polarizer --help
mkdir -p ~/.config/polarizer ~/projects/demo
CONFIG="$HOME/.config/polarizer/polarizer.toml"
```

That installs the v0.2.0 release. For the latest code, install from the repository's default branch instead with `uv tool install git+https://github.com/mpwilso/polarizer`, which needs git. Neither line reads `uv.lock`, which holds the full set of versions Polarizer was tested with.

**Already using MCP servers in Claude Code?** `polarizer init --from <path>` reads an MCP config, such as a project's `.mcp.json`, and prints a polarizer.toml with those servers as upstreams and the `mcp.json` that starts Polarizer with it; with `--write <dir>` it saves both there instead, never over an existing file. It names every server it can't proxy, such as an HTTP server, and writes a value that looks like a secret as a `${NAME}` placeholder that `serve` reads from its environment. It is new since v0.2.0, so until the next release it needs the git install. Every tool it lists is held on every call until you give it a class, as below.

Otherwise, save this as `~/.config/polarizer/polarizer.toml`, with `/home/you` replaced by your home directory (on macOS, `/Users/<you>`). It is [polarizer.example.toml](polarizer.example.toml) without its comments: the reference Filesystem server, pinned to an exact version.

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

Each tool gets a class. `local-read` and `open-world` run unless a path matches a read hold pattern such as `~/.ssh/**`; `local-write` runs only when every path in `path_args` resolves inside a workspace root and matches no write hold pattern, such as `.git/hooks/**`; `destructive` and `egress` are held on every call, and so is a tool with no class. [docs/HOLD-SPEC.md](docs/HOLD-SPEC.md#2-classes-and-the-policy-in-polarizertoml) has every key. The end of [polarizer.example.toml](polarizer.example.toml) sketches an issue tracker whose `create_issue` is held, so you see each ticket before it is filed. Run the server once by hand first, since its first `npx` fetch can miss Polarizer's connect timeout:

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

- `src/polarizer/`: the proxy, the ledger, pins, holds, drills and the command line; `src/polarizer/scenarios/` holds the drill scenarios.
- `conformance/`: the ledger fixtures and the separately written reference verifier.
- `tests/`: every test; `tests/test_readme.py` runs this README's commands.
- `docs/`: the five specs, [the drill guide](docs/DRILL-GUIDE.md), [the evidence](docs/EVIDENCE.md), [the limits](docs/LIMITS.md), [related projects](docs/related.md) and [the verified facts](docs/verified-facts.md). `docs/dev/` is the build log.
- `docs/brand/`: the logo, drawn by `scripts/brand.py`. The parallel lines are calls that line up with what you approved; the one turned out of line is held ([what the mark means](docs/brand/README.md)).
- `manual/`: the config for the interactive checks.
- `scripts/`: `test.sh` runs every check, as CI does; `demo.py` draws the terminal picture above from a real run against a test server; the live and manual check scripts; `dev/guard.sh`, a guard for my own machine.

## How it was built

I designed Polarizer and directed its build. Claude Code wrote most of the code, in staged rounds: a spec for each milestone, tests written first, and a review at each stop before the next stage began. I ran the interactive checks and both rug-pull checks on real Claude Code myself. It was built independently of any employer system, on personal time and equipment. The specs, stage notes and reviews are in [docs/dev/](docs/dev/README.md).

I also built [Loupe](https://github.com/mpwilso/loupe), [Parallax](https://github.com/mpwilso/parallax) and [ISR](https://github.com/mpwilso/isr).

Credits: from mcpclerk, three ideas, credited by name, with none of its code or text used: a run-end entry that records how many entries the run wrote, which inspired `ledger.head`; tools hidden from the list that are still refused and recorded when called by name; and an approve-everything switch that exists only as a command-line flag, never in the policy file (`--no-holds`). Polarizer is built on the MCP Python SDK and the `rfc8785` package, uses RFC 8785 (JSON Canonicalization Scheme) for its ledger entries, and is tested against the reference Everything and Filesystem MCP servers. No code from any of these projects is copied here.

## What's next

The point of the project is the measurement: whether a person's approvals still catch anything. Drills measure it in practice; the next steps measure it in real work.

1. **Statistics over real holds (stage 9):** how often you allow, how fast, by tool and rule, with a warning when decisions get faster while the approval rate stays flat.
2. **The approval card (M3):** a local page that shows a held call, what it would change, why it was held and its history.
3. **Live planted calls:** planted holds inside a running gateway, opt-in, each revealed once it ends.
4. **Taint (M2b):** holding outgoing calls after the agent has read untrusted content, and rules on argument values and domains.

MIT license: [LICENSE](LICENSE).
