# Quickstart (draft)

This is a draft for a future README. It describes Polarizer 0.1 at milestone M1a (pins). It is not published, and Polarizer is not on PyPI. M0, the pass-through that exposed every tool as soon as it connected, is the code at commit 4eaa61a; no flag brings that behavior back.

## What it is

Polarizer is a local proxy between an AI agent and its MCP servers. It exposes each upstream tool as `<prefix>__<tool>`, forwards calls unchanged, and records every call in a hash-chained ledger that `polarizer verify` checks.

From M1a it also pins tool definitions. No tool reaches the agent until you approve its definition with `polarizer approve`, and the agent is shown only the copy you approved. If a server later changes a tool's definition, Polarizer hides the tool, records the change, and tells Claude Code its tool list changed; the tool stays hidden until you approve the new definition.

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

## First run

Nothing is exposed until you approve it, so do this once:

1. **Record what your servers offer,** without Claude Code: `.venv/bin/polarizer serve --config /absolute/path/to/polarizer.toml < /dev/null`. With its input closed, `serve` connects every upstream, lists its tools, stores a copy of each definition and records it in the ledger, then exits. On stderr it says how many tools wait: `polarizer: <n> tools wait for approval; run polarizer pending`.
2. **Read them:** `.venv/bin/polarizer pending --config /absolute/path/to/polarizer.toml`. Every definition is printed in full, with any character outside printable ASCII shown as an escape, so hidden or look-alike characters are visible. The last line names a group: `group <id> covers the <k> new definitions above ...`.
3. **Approve them:** `.venv/bin/polarizer approve --config /absolute/path/to/polarizer.toml --group <id>`. A group approves exactly the definitions `pending` printed; if anything changed in between, it refuses and you run `pending` again. A tool with more than one definition waiting, or one you have decided on before, is approved by name: `polarizer approve --config <path> <prefix> <tool> <hash>`.
4. **Start Claude Code** in the project directory, adding `--mcp-config <file> --strict-mcp-config` if the config is a separate file, and approve the `polarizer` server if asked. `/mcp` lists the approved tools, prefixed.

Later, `polarizer pending` shows anything new or changed. A running Polarizer notices an approval within about a second and tells Claude Code, which lists the tool again without a reconnect (seen in an interactive session with Claude Code 2.1.289 on 2026-10-04). If `/mcp` doesn't show it, reconnect `polarizer` from `/mcp`. `approve` and `reject` refuse to run without a terminal unless given `--allow-no-terminal`, so that an agent doesn't approve things by accident.

## Check the ledger

```
.venv/bin/polarizer verify --config /absolute/path/to/polarizer.toml
```

`intact:` on the first line means the chain checks out. `verify --args` also checks the argument side files. The ledger lives in `~/.local/share/polarizer/` unless `ledger_dir` says otherwise.

## Stated limits

- **Tools only.** Resources, prompts and completions from upstream servers are not exposed.
- **Pins only.** This version pins tool definitions and nothing else: no scanning of descriptions, no policy, and no holds. An approved tool's calls go straight through, recorded in the ledger.
- **Definitions, not behavior.** A server can change what a tool does without changing its definition, and Polarizer won't notice. If you approve a poisoned definition without reading it, Polarizer keeps serving exactly that definition.
- **An agent that can run commands as you can run `polarizer approve`.** The terminal check only stops accidents.
- **Requests to the client are not forwarded.** When an upstream asks the client for input (elicitation, sampling or roots), the tool call fails. A server on the 2026-07-28 protocol gets a one-line error naming what it asked for. A server on an older protocol has its request refused before Polarizer sees it, and the call surfaces as a `protocol-error`.
- **Results pass through the MCP Python SDK.** Fields the protocol doesn't define are dropped. Results follow the protocol version of the client's own connection, so a 2026-07-28 client sees `resultType` and a `serverInfo` stamp. Tools are served from the approved copies, which have no `execution` or `_meta`, so no client sees either. A result the SDK can't parse becomes an error. docs/PROXY-SPEC.md (Results) has the details.
- **Upstreams start once per session.** One that fails to start stays off until Claude Code restarts Polarizer. An upstream whose listing fails has its tools hidden until a listing succeeds again (Polarizer retries after 30 seconds, then less often, up to every 5 minutes); one whose process exits stays hidden until Polarizer restarts.

## How it was built

Polarizer was designed and directed by Matt Wilson. Claude Code wrote most of the code, working from the specs, tests and checks Matt Wilson set. It was built independently of any employer system. It learned from prior art, credited by name, with none of that code used. From mcpclerk it took three ideas: a run-end entry that records how many entries the run wrote, which inspired `ledger.head`; tools hidden from the list that are still refused and recorded when called by name, built in M1a; and an approve-everything switch that exists only as a command-line flag, never in the policy file, which is planned, not built.
