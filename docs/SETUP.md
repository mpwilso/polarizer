# Setup

From an install to a held call in Claude Code. For Linux and WSL; macOS has passed CI but no live check. You need Python 3.11 or later, [uv](https://docs.astral.sh/uv/), Node.js for `npx` servers, and Claude Code. [README.md](../README.md#try-it) has the short version, and [DRILL-GUIDE.md](DRILL-GUIDE.md) a drill that needs none of this.

```sh
uv tool install https://github.com/mpwilso/polarizer/releases/download/v0.3.0/polarizer-0.3.0-py3-none-any.whl
polarizer --help
mkdir -p ~/.config/polarizer ~/projects/demo
CONFIG="$HOME/.config/polarizer/polarizer.toml"
```

That installs the v0.3.0 release. For the latest code instead, install from the repository's default branch with `uv tool install git+https://github.com/mpwilso/polarizer`, which needs git. Neither line reads `uv.lock`, which holds the full set of versions Polarizer was tested with.

**Already using MCP servers in Claude Code?** `polarizer init --from <path>` reads an MCP config, such as a project's `.mcp.json`, and prints a polarizer.toml with those servers as upstreams and the `mcp.json` that starts Polarizer with it; with `--write <dir>` it saves both there instead, never over an existing file. It names every server it can't proxy, such as an HTTP server, and writes a value that looks like a secret as a `${NAME}` placeholder that `serve` reads from its environment. Every tool it lists is held on every call until you give it a class, as below.

Otherwise, save this as `~/.config/polarizer/polarizer.toml`, with `/home/you` replaced by your home directory (on macOS, `/Users/<you>`). It is [polarizer.example.toml](../polarizer.example.toml) without its comments: the reference Filesystem server, pinned to an exact version.

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

Each tool gets a class. `local-read` and `open-world` run unless a path matches a read hold pattern such as `~/.ssh/**`; `local-write` runs when every path in `path_args` resolves inside a workspace root and matches no write hold pattern, such as `.git/hooks/**`, and is held otherwise; `destructive` and `egress` are held on every call, and so is a tool with no class. [HOLD-SPEC.md](HOLD-SPEC.md#2-classes-and-the-policy-in-polarizertoml) has every key. The end of [polarizer.example.toml](../polarizer.example.toml) sketches an issue tracker whose `create_issue` is held, so you see each ticket before it is filed. Run the server once by hand before Polarizer does, since its first `npx` fetch can miss Polarizer's connect timeout:

```sh
npx -y @modelcontextprotocol/server-filesystem@2026.8.31 "$HOME/projects/demo" < /dev/null
```

## First run, without Claude Code

No tool is exposed until you approve its definition. With its input closed, `serve` records what each server lists and exits:

```sh
polarizer serve --config "$CONFIG" < /dev/null
polarizer pending --config "$CONFIG"
polarizer approve --config "$CONFIG" --group <group id>
polarizer verify --config "$CONFIG"
```

`serve` ends with `polarizer: <n> tools wait for approval; run polarizer pending`. `pending` prints every definition in full, unusual characters escaped, and a group line near the end: `group <group id> covers the <n> new definitions above ...`. A group approves exactly what `pending` printed; one definition is approved by name with `polarizer approve --config "$CONFIG" <prefix> <tool> <hash>`. `verify` prints `intact: ...` when the chain checks out. `approve`, `allow` and `deny` refuse to run without a terminal unless given `--allow-no-terminal`.

## Claude Code

Save this as `~/.config/polarizer/mcp.json`, with `command` set to what `command -v polarizer` prints, and start Claude Code with that file and no other MCP config:

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

Don't put Polarizer in a project `.mcp.json`: every Claude Code session opened in that directory would start it and write to its ledger, which happened while Polarizer was built ([record](verified-facts.md#development-sessions-spawned-the-proxy-from-the-ledger-not-interactive)). Pass the file with `--mcp-config` instead. Claude Code's own permission prompt for an MCP tool comes before the call reaches Polarizer, without the call's details, so a held call may be approved twice.

## Holds, in a second terminal

Ask Claude to move a file in `~/projects/demo`; `move_file` is `destructive`, so the call is held.

```sh
CONFIG="$HOME/.config/polarizer/polarizer.toml"
polarizer holds --config "$CONFIG" --wait --bell
polarizer allow --config "$CONFIG" <hold id>
polarizer deny --config "$CONFIG" <hold id> --reason "not that file"
```

`holds --wait` waits for a held call, lists every waiting call with its exact arguments, and exits, and `--bell` rings the terminal first; run it again for the next one. Use `allow` or `deny` for each hold: a denied call tells the agent `polarizer: <tool> was not allowed` and nothing more, and an undecided one is refused after `hold_timeout_seconds`. `polarizer serve --config <path> --no-holds` turns the hold rules off for one session; it exists as a flag and never as a policy key, is recorded in the ledger at every start, and pins still apply. The limits that matter here, such as Esc ending every held call, are in [LIMITS.md](LIMITS.md#held-calls-and-claude-code).
