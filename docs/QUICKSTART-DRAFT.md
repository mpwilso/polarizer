# Quickstart (draft)

This is a draft for a future README. It describes Polarizer 0.1 as it is today, milestone M0. It is not published, and Polarizer is not on PyPI.

## What M0 is

Polarizer is a local proxy between an AI agent and its MCP servers. M0 is pass-through plus a verifiable log: every upstream tool is exposed as `<prefix>__<tool>`, every call is forwarded unchanged, and every call is recorded in a hash-chained ledger that `polarizer verify` checks. M0 holds nothing yet. It approves no calls, blocks no calls, and pins no tool definitions; those are later milestones.

## Install from a clone

You need Python 3.11 or later, [uv](https://docs.astral.sh/uv/), and Node.js if you run `npx` servers.

```
git clone <this repository> ~/code/polarizer
cd ~/code/polarizer
uv sync --locked
```

This installs Polarizer and its pinned dependencies into `.venv` inside the clone. Nothing is installed globally.

## Configure

1. Copy `polarizer.example.toml` to `polarizer.toml` and edit it. Each `[upstream.<prefix>]` table starts one MCP server. docs/PROXY-SPEC.md lists every key. List any directory the ledger must never be written to in `ledger_forbidden_paths`.
2. Give Claude Code an MCP config file (see `manual/mcp.json.example`) that runs `polarizer serve --config <absolute path to polarizer.toml>`. `--config` must be an absolute path. In your own project this can be a project-scope `.mcp.json`. Inside Polarizer's own repository, never: pass the file with `--mcp-config` instead, so development sessions there never start Polarizer.
3. **Pre-warm every pinned `npx` server once** before first use: run its exact command by hand (for example `npx -y @modelcontextprotocol/server-filesystem@2026.8.31 /some/dir < /dev/null`) and let it exit. The first fetch can take longer than Polarizer's connect timeout (10 seconds by default, 20 at most), and an upstream that misses it stays off until Claude Code restarts Polarizer.
4. Start Claude Code in the project directory, adding `--mcp-config <file> --strict-mcp-config` if the config is a separate file, and approve the `polarizer` server if asked. `/mcp` lists the prefixed tools.

## Check the ledger

```
.venv/bin/polarizer verify --config /absolute/path/to/polarizer.toml
```

`intact:` on the first line means the chain checks out. `verify --args` also checks the argument side files. The ledger lives in `~/.local/share/polarizer/` unless `ledger_dir` says otherwise.

## Stated limits

- **Tools only.** Resources, prompts and completions from upstream servers are not exposed.
- **Nothing is held yet.** M0 forwards every call to a listed tool. It is a pass-through with a verifiable log.
- **Requests to the client are not forwarded.** When an upstream asks the client for input (elicitation, sampling or roots), the tool call fails. A server on the 2026-07-28 protocol gets a one-line error naming what it asked for. A server on an older protocol has its request refused before Polarizer sees it, and the call surfaces as a `protocol-error`.
- **Results pass through the MCP Python SDK.** Fields the protocol doesn't define are dropped. Results follow the protocol version of the client's own connection, so a 2026-07-28 client sees `resultType` and a `serverInfo` stamp, and doesn't see a tool's 2025-11-25 `execution` field. A result the SDK can't parse becomes an error. docs/PROXY-SPEC.md (Results) has the details.
- **Upstreams start once per session.** One that fails to start stays off until Claude Code restarts Polarizer.

## How it was built

Polarizer was designed and directed by Matt Wilson. Claude Code wrote most of the code, working from the specs, tests and checks Matt Wilson set. It was built independently of any employer system. It learned from prior art, credited by name, with none of that code used. From mcpclerk it took three ideas: a run-end entry that records how many entries the run wrote, which inspired `ledger.head`; tools hidden from the list that are still refused and recorded when called by name; and an approve-everything switch that exists only as a command-line flag, never in the policy file. The last two are planned, not built.
