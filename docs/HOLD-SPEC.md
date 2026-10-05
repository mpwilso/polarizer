# Hold spec (M2a)

This is the behavior contract for M2a: tool classes and default holds. The ledger format is in LEDGER-SPEC.md, `serve` and the command line are in PROXY-SPEC.md, pins are in PIN-SPEC.md, and the facts this relies on are in verified-facts.md. Everything M0 and M1a do stays as those files say, except where this file changes it. Each change is listed under Deviations and guesses at the end (section 17), and the owner's answers to the spec round's questions are in section 16.

docs/PLAN.md does not exist in the repo, so nothing here relies on it. Where this file mentions canaries, it uses milestones.md's M5 + M6 row.

## 1. What M2a is and is not

**In M2a:**
- **Classes.** Every exposed tool has a class set in `polarizer.toml`: `local-read`, `local-write`, `destructive`, `open-world` or `egress` (section 2).
- **Default holds.** One rule function decides, for every call that routes to an approved tool, whether it runs at once or is held for a person (section 4).
- **Path rules.** A per-tool list of argument names that hold paths. Each such path is resolved (symbolic links, `..`) before it is compared with the workspace roots and with the hold patterns (section 3).
- **The hold.** A held call stays pending inside `serve` while serve waits for a decision entry that another process writes to the ledger (section 6). It ends with an allow, a deny, an expiry, the client's cancel or Polarizer's shutdown, and each ending is recorded.
- **The commands.** `polarizer holds`, `polarizer allow` and `polarizer deny` (section 8).
- **Restart.** A new `serve` records the open holds a dead process left behind (section 7).

**Not in M2a:**
- taint, grants, and domain or value rules on arguments (M2b);
- the card, `polarizer card` (M3), apart from the ledger kinds it will share;
- stats and time per decision (M5), and canaries (M6; section 12 says what M2a keeps open for them);
- scanning of descriptions (M1b).

**What holding protects.** A call that the rules hold does not reach its upstream until a person allows it, and never reaches it if the person denies it or does nothing. The ledger records the hold, the decision and the outcome, so afterwards it shows what was asked, who decided, and whether the call ran.

**What holding does not protect:**
- **Only calls routed through Polarizer.** MCP servers configured directly in Claude Code, the agent's own shell, its file editing tools and any other program are outside it.
- **A held call is a decision by a person,** who may allow without reading. Polarizer can show the exact arguments; it cannot make anyone read them. Measuring that is M5's job.
- **Annotations are untrusted.** `readOnlyHint`, `destructiveHint` and `openWorldHint` come from the server. They are shown as a suggestion and used only for an upstream the config marks as trusted (section 2).
- **Path rules cover only the arguments named in config.** A path in an argument the config doesn't name is not checked, and a path inside a free-text argument (a shell command, a URL, a script) is never checked.
- **A tool can reach files by means that never appear in its arguments:** its own configuration, its working directory, a path it computes, a file it was told about in an earlier call, or another process it starts. A class is a statement about the tool, made by the person who wrote the config, and Polarizer cannot check it.
- **Resolution happens when the call arrives.** A symbolic link created or changed after that, by an earlier call, another process or the agent's shell, is not seen. An upstream that sees a different file system (a container, another machine) resolves paths its own way.
- **Claude Code asks first.** Claude Code's own permission prompt for an MCP tool, where it applies, comes before the call reaches Polarizer. A held call may therefore be approved twice: once in Claude Code, without the details, and once in `polarizer allow`.

## 2. Classes and the policy in polarizer.toml

### The classes

| Class | Means | M2a default |
|---|---|---|
| `local-read` | reads local data and changes nothing | runs, logged; held when a path argument matches a read hold pattern (section 3) |
| `local-write` | changes local files | runs when every path argument resolves inside a workspace root and matches no write hold pattern; held otherwise |
| `destructive` | deletes, moves or overwrites, or changes something hard to undo | held on every call; a path argument that a path rule would hold is named in the reason (section 4) |
| `open-world` | reads from outside the machine (web pages, issues, mail) | runs, logged (taint is M2b) |
| `egress` | sends data outside the machine, or acts there | held on every call; a path argument that a path rule would hold is named in the reason (section 4) |

A tool with no class is **unclassified** and held on every call (fail closed).

### The keys

```toml
ledger_dir = "~/.local/share/polarizer"
ledger_forbidden_paths = ["~/code/parallax", "~/code/loupe", "~/code/isr"]

# Optional. With no [policy] table, every default below applies.
[policy]
# Directories a local-write tool may write inside without a hold. Default: none, so every
# local-write call is held. At most 32; each must exist when serve starts.
workspace_roots = ["~/code/myproject"]
# How long a held call waits for a decision before it is denied. 1 to 1200; default 300.
hold_timeout_seconds = 300
# Added to the built-in hold patterns (section 3); the built-in ones cannot be removed.
write_hold_patterns = ["deploy/**", "~/.config/systemd/**"]
read_hold_patterns = ["~/.kube/**"]

[upstream.fs]
command = "npx"
args = ["-y", "@modelcontextprotocol/server-filesystem@2026.8.31", "/home/<you>/code/myproject"]
# Optional, default false. When true, a tool of this upstream with no class below takes the
# class its annotations suggest (section 4). A class given below always wins.
trust_annotations = false

# Optional. One entry per tool, keyed by the upstream's own tool name (no prefix).
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

[upstream.web]
command = "/opt/web-mcp/bin/web-mcp"
args = ["--stdio"]

[upstream.web.tools]
fetch = { class = "open-world" }
post_comment = { class = "egress" }
```

The Filesystem tool names above are the 14 tools the pinned server lists, checked in stage 7 against its real listing with `test_example_config_matches_reference_servers` (section 13; verified-facts.md, Stage 7), which also checks that each `path_args` name is a property of that tool's input schema. Stage 7 added `read_file`, the server's deprecated name for `read_text_file`, which the spec round's list had left out.

**`[policy]` keys:**
- `workspace_roots`: list of strings, default empty. `~` is expanded, each result must be absolute, and there are at most 32. `serve` resolves each with `os.path.realpath` at start and refuses to start if one doesn't exist. Polarizer never asks the client for its roots, although Claude Code advertises the capability: the answer would come from the session the holds guard (section 16, decision 10). Commands that only read the config (`verify`, `repair`, `pending`, `holds`, `allow`, `deny`) don't check existence.
- `hold_timeout_seconds`: integer from 1 to 1200, default 300.
- `write_hold_patterns`, `read_hold_patterns`: lists of patterns (section 3), default empty, at most 64 each.
- Unknown keys are an error.

**Per-upstream keys,** added to PROXY-SPEC.md's four:
- `trust_annotations`: boolean, default false.
- `tools`: a table of tables, default empty. Each key is an upstream tool name; with the prefix it must make an exposed name Polarizer could serve (`^[A-Za-z0-9._-]{1,128}$`). TOML needs quotes around a name with a dot. Each value has:
  - `class`: required, one of the five classes;
  - `path_args`: list of argument names, default empty, at most 16, each 1 to 128 characters.
  - Unknown keys are an error.
- A tool named under `tools` that the upstream doesn't list is not an error: an upstream can add it later. It has no effect until listed.

**The policy hash.** `policy_sha256 = hex(sha256(b"POLARIZER-POLICY/1\n" + rfc8785.dumps(form)))`, where `form` is `{"builtin_patterns": 1, "hold_timeout_seconds": <n>, "read_hold_patterns": [...], "upstreams": {<prefix>: {"tools": {<tool>: {"class": <c>, "path_args": [...]}}, "trust_annotations": <bool>}}, "workspace_roots": [<resolved roots>], "write_hold_patterns": [...]}`. Every upstream appears, with an empty `tools` table if it has none. `builtin_patterns` is a version number, raised whenever section 3's built-in lists change. `session.started` already records `config_sha256` over the file's bytes; the policy hash says what the parsed policy was.

### Errors

Each goes to stderr as one line and exits 2, as PROXY-SPEC.md's config errors do. `serve` starts no upstream.

```
polarizer.toml: "policy" must be a table
polarizer.toml: [policy] unknown key "roots"
polarizer.toml: [policy] workspace_roots must be a list of absolute paths
polarizer.toml: [policy] workspace_roots has more than 32 entries
polarizer.toml: [policy] workspace root /home/me/code/gone does not exist
polarizer.toml: [policy] hold_timeout_seconds must be an integer from 1 to 1200
polarizer.toml: [policy] write_hold_patterns must be a list of at most 64 patterns
polarizer.toml: [policy] read_hold_patterns must be a list of at most 64 patterns
polarizer.toml: [policy] pattern "a/**b" is not valid: "**" must be a whole part
polarizer.toml: [upstream.fs] "trust_annotations" must be true or false
polarizer.toml: [upstream.fs] "tools" must be a table of [upstream.fs.tools] entries
polarizer.toml: [upstream.fs.tools] "write file" is not a tool name Polarizer can expose
polarizer.toml: [upstream.fs.tools] "write_file" must be a table
polarizer.toml: [upstream.fs.tools] "write_file" unknown key "paths"
polarizer.toml: [upstream.fs.tools] "write_file" is missing "class"
polarizer.toml: [upstream.fs.tools] "write_file" class must be one of local-read, local-write, destructive, open-world, egress
polarizer.toml: [upstream.fs.tools] "write_file" path_args must be a list of at most 16 argument names
```

The pattern error's reason is one of: `"**" must be a whole part`, `an empty part`, `"." or ".." as a part`, `"~" only as the first part, followed by "/"`, `a backslash; use "/"`, `longer than 1024 characters`, or `empty`. Text from the file in these lines goes through `polarizer.text.safe`, as upstream text does.

### Existing configs, and running with nothing held

- **A config with no classes** loads as it did. Every tool is unclassified, so every call to it is held. Once per process, after startup has listed the upstreams, serve writes `polarizer: <n> of <m> listed tools have no class in polarizer.toml; every call to them is held` to stderr, where `<m>` counts every tool the upstreams listed, in any pin state, and `<n>` those with no class (none configured, and no trusted annotation). With `<n>` at 0 nothing is written.
- **`polarizer serve --config <path> --no-holds`** runs with the hold rules off: every call that routes to an approved tool runs, whatever its class or paths. It is a command-line flag only. There is no config key for it, and a key such as `holds = false` is an unknown key. It is never the default. At every start with it, serve records `policy.loaded` with `holds` `"off"` (section 5), fsynced before anything is served, and writes `polarizer: warning: started with --no-holds; no call is held` to stderr. Pins, routing and the ledger are unchanged by it. The idea of a switch that exists only as a flag, never in the policy file, is credited to mcpclerk by name (CLAUDE.md, Clean room); nothing else of it is used.
- **The manual check and the scripts** classify their tools (section 11), so they keep working without `--no-holds`.

### Classes taken from annotations

A tool of an upstream with `trust_annotations = true` that has no entry under `tools` takes the class its annotations suggest (section 4). Every annotation set suggests a class, so such a tool never counts toward the unclassified line above: a trusted server chooses its own class, and nothing else would say so. Once per process, right after the unclassified line (or where it would be), serve writes:

```
polarizer: <n> of <m> listed tools take their class from annotations (trust_annotations is on for <prefixes>)
```

`<m>` counts every tool the upstreams listed, in any pin state, as in the unclassified line. `<n>` counts the listed tools of trusted upstreams that have no `tools` entry. `<prefixes>` are the prefixes of the trusted upstreams those tools belong to, in config order, separated by `, `. With `<n>` at 0 nothing is written, and nothing is written under `--no-holds`, where no class is used. The line is informational: it changes no verdict, and a configured class still wins.

## 3. Paths

### Which values are paths

A tool's `path_args` names top-level argument keys. For each name, in the order the config lists them:
- **missing** (the key is absent, or the arguments are not an object): the call is held, rule `path-missing`;
- **a string:** one path;
- **a list of strings:** each is a path, in order; an empty list has nothing to check; more than 256 elements is held, `path-unresolvable`;
- **anything else** (a number, null, an object, a list holding a non-string): held, `path-not-string`.

Nested keys (`edits[0].path`) cannot be named in M2a; they come in M2b, and the README states the limit (section 16, decision 3).

### Resolving a path

A path is resolved before any comparison. Resolution never asks the upstream and never follows the upstream's own rules, which Polarizer can't know. So whatever Polarizer can't resolve the way the operating system would is held, rule `path-unresolvable`, with one of these fixed reasons:

| Input | Reason in the record |
|---|---|
| not absolute (`a/b`, `./a`; on Windows also `C:a`, `\a` and anything without a drive or UNC root); kept held, with no key to resolve against a directory (section 16, decision 4) | `not an absolute path` |
| starts with `~` | `starts with ~, which the server may expand` |
| contains a NUL character | `contains a NUL character` |
| a `..` part after a part that does not exist | `".." after a part that does not exist` |
| more than 40 symbolic links followed, or a loop | `too many symbolic links` |
| a part that can't be examined (permission denied, an I/O error) | `cannot be examined: <the operating system's message, through safe()>` |
| resolution takes longer than 2 s (a hung network mount) | `took longer than 2 s to resolve` |
| 8 earlier resolutions in this process are still stuck (below) | `too many path resolutions are stuck` |
| Windows only: a `:` after the drive (an alternate data stream), a part ending in a dot or a space, a reserved device name (`CON`, `NUL`, `COM1` and the rest, with or without an extension), or a `\\?\` or `\\.\` prefix | `a Windows name Polarizer does not resolve` |

Otherwise the path is resolved as POSIX `realpath` does, part by part: each part that exists is examined with `lstat`; a symbolic link is replaced by its target (relative targets against the link's directory) and resolution continues there; `..` applies to the path resolved so far. Once a part does not exist, the rest is appended as written, which is how a call that creates a file is checked. Resolution runs in a worker thread, so a slow file system never blocks the event loop, and the 2 s bound is measured there.

**Stuck resolutions.** Each path is resolved on a daemon thread of its own. A thread blocked in the file system can't be stopped, so a resolution that passes its bound is left running and counts as **stuck** until it returns. While 8 are stuck, every further path is held at once as `path-unresolvable` with the reason `too many path resolutions are stuck`, and no thread is started for it. The count drops as stuck threads return, and paths are resolved again once it is below 8. The count is per process, over every call. This bounds the threads a hung mount can leave behind.

The result is the **resolved path**. It is what every comparison below uses, and what the record shows.

### Comparing with the workspace roots

A resolved path is inside a root by the same rule `ledger_forbidden_paths` uses (`ledgerdir.is_inside`): resolved real paths compared part by part with the platform's case rule (`os.path.normcase`), never as string prefixes, plus `os.path.samefile` on the nearest existing ancestor where both exist, which covers case-insensitive volumes. So `/a/proj2` is not inside `/a/proj`, a differently cased path is inside on Windows, and on macOS it is inside when the volume is case-insensitive (the default). Roots are resolved once, at start.

### Hold patterns

A pattern is a `/`-separated list of parts, on every platform. In a part, `*` matches any run of characters and `?` one character; every other character is literal. A part that is exactly `**` matches zero or more whole parts. There are no character classes or braces.
- **Anchored** patterns start with `/`, `~/` or, on Windows, a drive (`C:/`). `~` is expanded once, at start. They match the resolved path from its root.
- **Floating** patterns are every other pattern. They match at any depth, as if they started with `**/`. So `.git/hooks/**` matches `/x/y/.git/hooks/pre-commit`, and `.env*` matches `/x/.env.local`.
- **A trailing `/**`** matches the directory itself too, so `.git/hooks/**` also holds a write that replaces `.git/hooks` with a link.
- **Case:** on Windows and macOS, a pattern and a path are compared after `casefold()` and Unicode NFC normalization of both. That holds more on a case-sensitive macOS volume, which is the safe direction. On Linux the comparison is exact.
- **Separators:** a Windows path's `\` is read as `/` for matching.

**Built-in write hold patterns,** always applied to every `local-write` path, in this order:
1. `.git/hooks/**` and `.git/config` (a hooks path or `core.fsmonitor` in `.git/config` runs code);
2. `.github/workflows/**`;
3. `~/.ssh/**`;
4. `.env*`;
5. shell start-up files: `~/.bashrc`, `~/.bash_profile`, `~/.bash_login`, `~/.bash_logout`, `~/.profile`, `~/.zshrc`, `~/.zshenv`, `~/.zprofile`, `~/.zlogin`, `~/.config/fish/**`, and on Windows `~/Documents/PowerShell/**` and `~/Documents/WindowsPowerShell/**`;
6. Claude Code's own configuration: `.claude/**`, `.mcp.json`, `~/.claude.json`, `~/.claude/**`. `CLAUDE.md` is not in the list, since agents edit it routinely (section 16, decision 9).

**Built-in read hold patterns,** always applied to every `local-read` and `open-world` path: `~/.ssh/**`, `~/.gnupg/**`, `~/.aws/**`, `~/.config/gh/**`, `~/.netrc`, `~/.git-credentials`, `~/.claude/.credentials.json` and `.env*`.

**Polarizer's own files,** checked before any pattern, rule `polarizer-files`: a path inside `ledger_dir` is held for every class (the argument side files hold arguments in plain text), and a path that is the `--config` file, by resolved path or `samefile`, is held for `local-write`.

The config's lists are added after the built-in ones. The built-in lists can't be removed: there is no removal key, and `--no-holds` is the only way around them (section 16, decision 1). Items 1 and 6 of the write list, the whole read list and Polarizer's own files go beyond the decision as given (section 17, deviations 2 and 3).

### Windows and macOS

- **macOS.** `/tmp`, `/var` and `/etc` are symbolic links into `/private`, and resolution follows them like any link, so a root of `/tmp/x` is stored as `/private/tmp/x`. `realpath` does not change the letter case of an existing name, and doesn't resolve firmlinks (`/System/Volumes/Data/Users/...` and `/Users/...` name one directory): both are covered for roots by `samefile` on the nearest existing ancestor, and for patterns by the casefold rule. File names may come back in NFD from the file system while the model sends NFC; patterns compare after NFC normalization, and roots by `samefile`.
- **Windows.** Paths are resolved with Python's `os.path.realpath`, which follows symbolic links and junctions and returns long names for existing parts (no `PROGRA~1`). The names in section 3's table that Polarizer does not resolve are held. Drive-relative (`C:a`) and root-relative (`\a`) paths are not absolute. A UNC path (`\\server\share\x`) is absolute and is compared like any other; it is outside every root unless a root is on that share. Case is compared with `normcase`, as for `ledger_forbidden_paths`.
- **Which tests run where** is in section 13: the signal tests are POSIX only, the symlink tests are skipped where Windows refuses `os.symlink`, the permission test is POSIX only, and the Windows names test runs only on Windows. Nothing in this section has run on Windows or macOS; the CI runners are the first.

### Known limits

Found in the M2a review (Oct 4, 2026), and stated in the README draft (section 14):
- **Windows device names with superscript digits.** Windows also reserves `COM` and `LPT` followed by a superscript one, two or three (U+00B9, U+00B2, U+00B3). The reserved-name check knows only the ASCII digits, so such a name is not held as a Windows name: it is resolved like any other name and judged by the roots and patterns.
- **Claude Code's configuration directory can be moved.** Claude Code reads its configuration from the directory named by `CLAUDE_CONFIG_DIR` when that variable is set. The built-in patterns name `~/.claude/**`, `~/.claude.json` and the project's `.claude/**` and `.mcp.json` only, and Polarizer does not follow the variable, so a moved directory is not covered unless the config adds it to `write_hold_patterns`.
- **`~` is the process's home.** `~` in a pattern is expanded once, at start, with `os.path.expanduser`, which reads the serve process's `HOME` on POSIX (`USERPROFILE` on Windows), not the account's home directory from the system's user database. A serve started with another `HOME` applies the `~/` patterns, built-in and configured, to that directory.

## 4. The rule function

### Order

`evaluate(policy, prefix, tool, arguments, definition) -> Verdict(action, rule, reason, class, class_from)` is a pure function apart from path resolution. `action` is `allow` or `hold`. It runs after routing (PROXY-SPEC.md and PIN-SPEC.md, section 6, Calls): a name that matches no listed tool, or a tool that isn't approved, is refused exactly as in M1a and never reaches it.

The tiers are deny, then hold, then allow. M2a has no deny rule of its own: every M2a rule either holds or allows, and the tier exists so M2b's value and domain rules slot in before the holds. One limit acts like a deny and comes after the function: a call it would hold, when 16 holds of this session are already open, is refused at once (section 6, Too many holds).

1. **`--no-holds`:** allow, rule `holds-off`. Nothing else is checked.
2. **The class.** The configured class, `class_from` `"config"`. Else, when the upstream has `trust_annotations = true`, the class the approved definition's annotations suggest (below), `class_from` `"annotations"`. Else none.
3. **The hold tier,** first match wins:
   - no class: `unclassified`;
   - `destructive`: `destructive`;
   - `egress`: `egress`. Both are unconditional; when the tool has `path_args`, its paths are still evaluated so that the reason can name one (below);
   - `local-write` with no `path_args`: `write-unchecked`;
   - then, for `local-write`, `local-read` and `open-world`, each configured argument in order, each of its paths in order: `path-missing`, `path-not-string`, `path-unresolvable`, `polarizer-files`, then for `local-write` `write-pattern` and `outside-roots`, and for the other two `read-pattern`.
4. **The allow tier:** `local-write` with every path inside a root, `inside-roots`; `local-read`, `local-read`; `open-world`, `open-world`.

### Every case

| # | Class | `path_args` | Condition on the arguments | Action | Rule | Reason (one line) |
|---|---|---|---|---|---|---|
| 1 | any | any | serve started with `--no-holds` | allow | `holds-off` | none recorded |
| 2 | none | any | any | hold | `unclassified` | `<prefix>__<tool> has no class in polarizer.toml` |
| 3 | `destructive` | any | any | hold | `destructive` | `class destructive is held on every call`; with `path_args`, `; ` and the reason of the first of rows 6 to 12 that applies, as for `local-write` |
| 4 | `egress` | any | any | hold | `egress` | `class egress is held on every call`; with `path_args`, `; ` and the reason of the first of rows 6 to 9 and 14 that applies, as for `local-read` |
| 5 | `local-write` | none | any | hold | `write-unchecked` | `class local-write has no path_args, so its paths cannot be checked` |
| 6 | `local-write`, `local-read`, `open-world` | yes | a named argument is missing | hold | `path-missing` | `argument "<a>" is missing` |
| 7 | the same | yes | a named argument is not a string or a list of strings | hold | `path-not-string` | `argument "<a>" is not a string or a list of strings` |
| 8 | the same | yes | a path can't be resolved (section 3's table) | hold | `path-unresolvable` | `argument "<a>": <fixed reason>` |
| 9 | the same | yes | a path resolves inside `ledger_dir` | hold | `polarizer-files` | `argument "<a>": <resolved> is inside Polarizer's ledger directory` |
| 10 | `local-write` | yes | a path is the `--config` file | hold | `polarizer-files` | `argument "<a>": <resolved> is Polarizer's config file` |
| 11 | `local-write` | yes | a path matches a write hold pattern | hold | `write-pattern` | `argument "<a>": <resolved> matches <pattern>` |
| 12 | `local-write` | yes | a path is outside every root, or no roots are configured | hold | `outside-roots` | `argument "<a>": <resolved> is outside every workspace root` |
| 13 | `local-write` | yes | every path inside a root, no pattern matched | allow | `inside-roots` | none recorded |
| 14 | `local-read`, `open-world` | yes | a path matches a read hold pattern | hold | `read-pattern` | `argument "<a>": <resolved> matches <pattern>` |
| 15 | `local-read` | yes | no row above, inside or outside the roots | allow | `local-read` | none recorded |
| 16 | `local-read` | none | any | allow | `local-read` | none recorded |
| 17 | `open-world` | yes | no row above | allow | `open-world` | none recorded |
| 18 | `open-world` | none | any | allow | `open-world` | none recorded |
| 19 | any class from annotations | as configured | as the rows for that class | as those rows | as those rows | the reason gains ` (class from annotations)` |
| 20 | any held row | any | 16 holds of this session already open | refused at once, no hold | `too-many-holds` | `too many held calls: 16 already wait in this session` |

`<resolved>` and `<a>` go through `safe()`, and the whole reason is cut to 1 KiB. `<pattern>` is printed as configured, `~` unexpanded.

**Paths of destructive and egress calls** (rows 3 and 4). The hold doesn't depend on them, but a person deciding a move should see that its destination is `~/.ssh/authorized_keys`. So when the tool has `path_args`, the paths are evaluated in the same order as for the other classes: a `destructive` tool's as `local-write` (Polarizer's files, the write patterns, the roots) and an `egress` tool's as `local-read` (the ledger directory, the read patterns), since what egress sends out is read. The first rule that would have held is appended to the reason after `; `, with that rule's own reason; a path that can't be resolved appends its fixed reason the same way. The rule stays `destructive` or `egress`, the action stays `hold`, and when no path rule applies the reason is the plain one. For example:

```
class destructive is held on every call; argument "destination": /home/me/.ssh/authorized_keys matches ~/.ssh/**
```

A class from annotations has no `path_args`, so its reason is always the plain one with the suffix of row 19.

### Annotations

**The suggested class,** from the approved definition's `annotations`, with the MCP defaults for an absent hint (`readOnlyHint` false, `destructiveHint` true, `openWorldHint` true):

| `readOnlyHint` | `destructiveHint` | `openWorldHint` | Suggests |
|---|---|---|---|
| true | (ignored) | false | `local-read` |
| true | (ignored) | true or absent | `open-world` |
| false or absent | true or absent | (ignored) | `destructive` |
| false or absent | false | false | `local-write` |
| false or absent | false | true or absent | `egress` |

A tool with no annotations therefore suggests `destructive`. A hint that isn't a boolean counts as absent. Annotations are part of the definition hash (PIN-SPEC.md, section 2), so a changed hint is drift, and a trusted upstream's class can't change without a new approval.

**A contradiction** is a configured class that holds less than the suggestion. Rank `local-read` and `open-world` 0, `local-write` 1, `destructive` and `egress` 2. A tool contradicts its annotations when the suggested rank is higher than the configured rank. The other direction (the config holds more than the annotations ask) is never reported: holding more is the person's choice. `pending` shows contradictions and unclassified approved tools (section 8). The config always wins over the annotations; a contradiction changes no verdict.

## 5. Ledger kinds

The format, the hash, the statuses and the exit codes don't change. LEDGER-SPEC.md already allows new kinds whose data stays inside the subset. Every field below is ASCII-keyed and holds strings, integers, booleans, null or lists of strings.

| Kind | `data` | Written by | fsynced |
|---|---|---|---|
| `policy.loaded` | `session`, `holds` (`"on"` or `"off"`), `policy_sha256`, `classified` (number of configured tool entries), `workspace_roots` (the resolved roots), `hold_timeout_seconds` | `serve`, at start, right after `session.started` | yes, with `ledger.head` updated, before any upstream is connected |
| `hold.created` | `session`, `hold` (16 lowercase hex characters, random), `tool` (the exposed name), `args_commit`, `class` (or null), `class_from` (`"config"`, `"annotations"` or null), `rule`, `reason`, `timeout_seconds` | `serve` | no |
| `hold.decided` | `hold`, `args_commit` (the hold's), `decision` (`"allow"` or `"deny"`), `actor` (`"person"`), `reason` (deny's folded `--reason`, or null) | `polarizer allow` and `deny`; later M3's card | yes, with `ledger.head` updated, before the command reports success; `serve` fsyncs the ledger itself before acting on an allow (section 6) |
| `hold.expired` | `session`, `hold`, `reason`: `timeout after <n> s`, `the client cancelled the call` or `polarizer shut down while the call was held` | `serve`, the holding process | no |
| `hold.abandoned` | `session` (the process writing it), `hold`, `held_by` (the session that created the hold) | `serve`, at start | no |

**Changed kinds,** each gaining optional fields:
- **`call.sent`** of a held call that was allowed has `hold`, the hold id. A call that was never held has no `hold` key, so every existing `call.sent` is unchanged.
- **`call.sent`** also has `allowed_by` (decided, section 16, decision 5): the rule that let a call run without a hold (`holds-off`, `inside-roots`, `local-read` or `open-world`), or `"hold"` for a call that was allowed from a hold. A call that never passed through the rule function has no `allowed_by` key; in M2a every forwarded call passes through it, and every `call.sent` written before M2a has neither key.
- **`call.refused`** that ends a held call has `hold`. Its `reason` is `hold <id> was denied`, `hold <id> expired: <hold.expired's reason>`, `the client cancelled the call before it was forwarded`, `polarizer shut down before the call was forwarded`, `hold <id> was allowed, but its side file <problem>` (section 6, Allow), or, when a decision arrived but routing has changed since (section 6), M1a's reason for the hidden tool. A refusal for `too-many-holds` has no `hold` (no hold was created) and the reason in row 20. LEDGER-SPEC.md's description of `call.refused` widens from "a name that matches no listed tool" to "a call Polarizer never forwarded".
- **`session.started`** is unchanged.

**`verify --args`** counts `hold.created`'s `args_commit` as a reference to its side file, as it counts `call.sent`'s (LEDGER-SPEC.md, Part 3). So the side file of a hold that was denied, expired or abandoned is matching, missing or tampered like any other, never orphaned, and a tampered one exits 8. A file that both a `call.sent` and a `hold.created` refer to (an allowed hold) is counted once, at the `call.sent`'s seq, as before. The output's format, the statuses and the exit codes don't change.

**What the fields leave out, on purpose.** `hold.created` records nothing taken from the MCP request apart from the tool name and the arguments' commitment: no request id, no `client_call_id`, no progress token, no `_meta`. `call.sent` still records `client_call_id` once the call is forwarded. Section 12 says why.

### SECURITY_KINDS

`SECURITY_KINDS` (`src/polarizer/writer.py`) gains `hold.decided` and `policy.loaded`. Its rule since M1a: entries are fsynced, and `ledger.head` updated, when losing them could make Polarizer do more than the ledger shows, or undo a person's decision.
- **`hold.decided`** with `allow` makes Polarizer forward a call. It must be durable before anything acts on it, exactly as `tool.approved` is (PIN-SPEC.md, section 8). A `deny` has the same kind, and the writer fsyncs by kind. That costs about a millisecond per decision, and it keeps a person's decision as durable as their approvals, which M5 counts.
- **`policy.loaded`** with `holds` `"off"` makes Polarizer forward calls it would otherwise hold. Losing it would hide that a session ran unguarded, so it is durable before serving starts. With `holds` `"on"` it is fsynced too, because the writer fsyncs by kind; it is one entry per start.
- **Not included:** `hold.created`, `hold.expired` and `hold.abandoned` only ever make Polarizer do less (hold, refuse, or record what a dead process left). They are written like `call.sent`, fsynced when the writer's queue empties. A lost `hold.created` leaves a decision with nothing to act on; a lost `hold.expired` or `hold.abandoned` is written again by the next start (section 7).

LEDGER-SPEC.md's fsync policy said "later milestones' holds and decisions" are security-state entries. This spec narrows that to decisions and `policy.loaded` (section 17, deviation 5).

### Endings, and one terminal entry per call

**Each hold ends exactly once.** The endings are `hold.decided`, `hold.expired` and `hold.abandoned`. The first in seq order is the hold's ending, and anything later for the same hold has no effect. Both writers make a second ending rare: `allow`, `deny` and serve's own `hold.expired` are conditional appends (section 6, The conditional append), checked under the ledger lock after catching up, so none of them writes an ending after another has been written. Only a hand-edited ledger or a writer that ignores this spec can add a second, and the fold ignores it.

**Each held call gets at most one terminal entry,** from this set: `call.returned` (after `call.sent`), `call.refused`, or `hold.abandoned`. It gets exactly one whenever a live process ends the call, and whenever the process died while the call was still held, once a later start has run. The two cases left without one are a process that died after an allow; the table says how each reads.

| How the hold ended | Entries after `hold.created`, in order | Terminal entry | Reads as |
|---|---|---|---|
| allow, then forwarded | `hold.decided` allow, `call.sent` (with `hold`), `call.returned` | `call.returned` | as any forwarded call |
| allow, then killed between `call.sent` and `call.returned` | `hold.decided` allow, `call.sent` | none | outcome unknown, as in M0 (LEDGER-SPEC.md, fsync policy) |
| allow, then killed before `call.sent` | `hold.decided` allow; later `hold.abandoned` is not written, because the hold already ended | none | allowed, never forwarded: with no `call.sent`, nothing reached the upstream |
| allow, but routing changed since | `hold.decided` allow, `call.refused` (M1a's reason, with `hold`) | `call.refused` | never forwarded |
| allow, but the side file fails its check | `hold.decided` allow, `call.refused` (`hold <id> was allowed, but its side file <problem>`, with `hold`) | `call.refused` | never forwarded |
| deny | `hold.decided` deny, `call.refused` | `call.refused` | never forwarded |
| timeout | `hold.expired` (timeout), `call.refused` | `call.refused` | never forwarded |
| the client's cancel while waiting | `hold.expired` (client), `call.refused` | `call.refused` | never forwarded |
| the client's cancel after an allow, before forwarding | `hold.decided` allow, `call.refused` (`the client cancelled the call before it was forwarded`) | `call.refused` | never forwarded |
| shutdown while waiting | `hold.expired` (shutdown), `call.refused` | `call.refused` | never forwarded |
| shutdown after an allow, before forwarding | `hold.decided` allow, `call.refused` (`polarizer shut down before the call was forwarded`) | `call.refused` | never forwarded |
| killed while waiting | nothing; the next start writes `hold.abandoned` | `hold.abandoned` | never forwarded; the process died |

So for a held call, "never forwarded" is exactly "no `call.sent` with its `hold`", and a `call.sent` without `call.returned` still reads as outcome unknown. The one case without a terminal entry and without a `call.sent` is the allowed hold whose process died before forwarding; the table says how it reads, and section 7 says what the person sees. A call that was never held keeps M0's rules.

## 6. The hold in serve

### Creating a hold

When `evaluate` returns `hold`, serve, in a shielded scope as it does for `call.sent`:
1. writes the argument side file (LEDGER-SPEC.md, Part 3), as for any call;
2. appends `hold.created` with a new random hold id;
3. adds the hold to its own in-memory table of open holds, keyed by id, with the args_commit, an event to wake on and the monotonic time the append resolved;
4. writes `polarizer: held <tool> as hold <id> (<rule>); run polarizer holds` to stderr.

If the writer can't record the hold, the call isn't held or forwarded: the client gets M0's line, `polarizer: <name> was not called: the ledger could not record it`.

The in-memory table is only a way to wake the waiting handler. What a hold is, its arguments and how it ended are all in the ledger and the side file, and every other process reads them from there. The waiting MCP request itself can only live in serve, and dies with it (decision 1). The request's own copy of the arguments is never forwarded: after an allow, serve forwards what it reads back from the side file (The endings, Allow), so what reaches the upstream is exactly what `holds` showed the person.

### Waiting

The handler waits for the first of: its hold's ending, the timeout, the client's cancel, or shutdown.

- **The watch.** serve's once-a-second ledger catch-up (PIN-SPEC.md, section 8) runs every 0.25 s while any hold of this session is open, and once a second otherwise. It is the same `fstat` comparison, so an idle ledger costs one `fstat` per tick. A catch-up hands each adopted entry to the hold fold as well as to pin state.
- **What wakes a hold.** An adopted ending whose `hold` is in serve's own table, and which is the first ending for that hold. A `hold.decided` whose `args_commit` differs from the hold's is ignored, with one stderr line (`polarizer: ignored a decision for hold <id> with another args_commit`), and the hold keeps waiting. A decision for a hold id serve didn't create (another session's, or one that doesn't exist) changes nothing in this process. That is how serve honors only decisions for its own holds.
- **Progress.** Every 10 s while waiting, serve calls `ctx.session.report_progress(<seconds waited>, None, None)`: the progress value is the whole seconds waited on the monotonic clock, with no total and no message. Each value is above the one before, as MCP asks of progress: with the default interval that is the whole seconds waited, and with an injected interval under a second (tests) a value is the one before plus one (section 17, Stage 7). That reports under the client's own progress token and does nothing if the client gave none (the SDK's docstring; verified-facts.md, Spike). A failure to send it is ignored. The interval can be injected for tests.
- **What the model sees while waiting:** nothing. The call takes time. No progress message carries text.

### The endings

**Allow.** serve:
1. calls `fsync` on the ledger itself (counted through `FileOps`), as it does before acting on an adopted `tool.approved`. If that fsync fails, the writer stops (PIN-SPEC.md, section 8), the call is not forwarded, and the client gets the "could not record it" line;
2. routes the call again (M0 and M1a's checks). A tool that was rejected, drifted, hidden by a failed refresh or lost while the call waited is refused with M1a's reason and the hold's id in `call.refused`, and the client gets M1a's line, `polarizer: <name> is not available: <why>`. The policy is not evaluated again: the person decided on these arguments;
3. reads the hold's side file back and checks it against the hold's `args_commit` exactly as `verify --args` does (the sha256 of the whole file must equal its name), then parses the arguments from the bytes after the 32-byte salt. They must be JSON whose value is an object or null. If any of that fails, the call is refused: `call.refused` with `hold` and the reason `hold <id> was allowed, but its side file <problem>`, where `<problem>` is one of the fixed texts `is missing`, `does not match its name`, `is unreadable` or `does not hold JSON arguments`. Nothing is forwarded, and the client gets `polarizer: <name> was not allowed`. The operating system's message for an unreadable file goes only to serve's stderr, through `safe()`;
4. writes `call.sent` with the hold's `args_commit` (the same side file), `hold` and `allowed_by` `"hold"`, then forwards the arguments parsed in step 3, never the request's in-memory copy, and records `call.returned` exactly as M0 does (decided, section 16, decision 5, and the owner's decision on forwarded arguments).

**Deny.** serve appends `call.refused` (`hold <id> was denied`, with `hold`), and the client gets one `isError` line: `polarizer: <name> was not allowed`.

**Timeout.** After `hold_timeout_seconds` on the monotonic clock, serve appends `hold.expired` (`timeout after <n> s`) through the conditional append, then `call.refused` (`hold <id> expired: timeout after <n> s`). The client gets the same line, `polarizer: <name> was not allowed`. If the conditional append finds an ending already there (the person decided in the last moment), serve acts on that ending instead: the ledger's order decides.

**The client's cancel** (`notifications/cancelled`, or Claude Code's own timeout, which sends one: verified-facts.md, Spike): the handler sees `CancelledError`. In a shielded scope, serve appends `hold.expired` (`the client cancelled the call`) conditionally, then `call.refused` (`hold <id> expired: the client cancelled the call`), and re-raises. The client gets nothing; the SDK has already abandoned the request. If an allow was already in the ledger, the hold's ending is that allow, and serve appends only `call.refused` with `the client cancelled the call before it was forwarded`. If a deny was, `call.refused` says `hold <id> was denied`.

**Shutdown.** The shutdown path (PIN-SPEC.md, section 8, step 1) cancels every handler, held ones included. A held handler records `hold.expired` (`polarizer shut down while the call was held`) and `call.refused` the same way, in its shielded scope. If an allow was already in the ledger, its ending is that allow, and serve appends only `call.refused` with `polarizer shut down before the call was forwarded`. This is best effort, like all shutdown work: if the process is killed first, the hold stays open in the ledger, and the next start records `hold.abandoned` for it (section 7). Shutdown ends every hold of the process, not just the one in view.

**The writer stops** while a hold waits (another process wrote something that doesn't chain, a fold failed, or an fsync failed): serve can no longer read decisions or record anything. Every waiting handler wakes at once and answers `polarizer: <name> was not called: the ledger could not record it`, with nothing recorded, as M1a does for calls. The holds stay open in the ledger, and the next start records them as abandoned.

**The model's line is the same for every ending that refuses:** `polarizer: <name> was not allowed`. It carries no rule, reason, path or reason text, because it reaches the model and the details may have been steered by injected content. The person sees the details in `holds`.

### The conditional append

`allow`, `deny` and serve's `hold.expired` must not write an ending after another has been written, and must check that under the same lock as the write. The writer gains one facility: `append(kind, data, check=<function>)`. In the writer thread, under the ledger lock, after catching up with the file, it calls `check` against the hold fold as it now stands. If `check` raises, nothing is written and the future raises that refusal; otherwise the entry is appended as usual, fsynced if its kind requires. No format changes: it is the order of operations inside one append.

The CLI's `Decider` today reads pin state when it opens the ledger and appends later, so two `approve` commands at once can both pass their checks. That is harmless for approvals. For holds it would give two endings, so `allow` and `deny` use the conditional append.

### Concurrency

- **Many holds at once.** Each held call has its own hold, handler, timer and ending. Their decisions can arrive in any order, and each wakes only its own handler. Two holds for identical arguments are two holds with two ids and two side files; the salt makes their `args_commit` differ.
- **A decision is single use.** It names one hold id, and a hold ends once. A model that retries a call, with the same or different arguments, makes a new hold; allowing the old one forwards only the old call, if its handler still waits.
- **Too many holds.** A call that would be held while 16 holds of this session are open is refused at once: `call.refused` with the reason in row 20, no side file and no `hold.created`, and the client gets `polarizer: <name> was not allowed`. This bounds what a looping model can leave for a person to read.
- **Two sessions on one ledger** (two Claude Code windows, each with its own serve): each serve wakes only on its own holds. `holds` lists both sessions' holds, and `allow` works for either.

### What M1a's limits become

PIN-SPEC.md, section 6, says a decision made just before a call may not apply to it. For a held call the window moves: routing is checked when the call arrives and again after the allow, so a pin decision made while the call waits does apply. Between the second routing check and the forward, the M1a limit still holds, in the same few milliseconds.

## 7. Restart, and what a person can know after Esc

### Session liveness

Polarizer must tell whether the process that created a hold is still running, without process ids or names. Each serve holds a lock for its whole life:
- At start, before `session.started`, serve creates `<ledger_dir>/sessions/<session>.lock` (directory 0700, file 0600, created exclusively) and takes an exclusive lock on it with `lock.py`'s primitives: `fcntl.flock` on POSIX, `msvcrt.locking` on byte 0 on Windows. It holds that lock until it exits. The operating system releases it when the process ends, however it ends, SIGKILL included.
- **Running** means the lock is held: a non-blocking attempt to take it fails as "would block".
- **Ended** means the attempt succeeds. The prober releases it at once.
- **Unknown** means the file is missing or can't be opened or locked for any other reason.
- If serve can't create or lock its own file, it writes `polarizer: warning: cannot create the session lock <path>: <message>; this session's holds will show as unknown` and goes on. Its holds then show as unknown and are never abandoned automatically.
- The files are never deleted in M2a: one empty file per session (section 16, decision 6).

`holds` probes with a shared lock on a read-only descriptor, so it creates and writes nothing. Two probes at once can make each other read "running" for a moment; that only delays an abandonment or shows a dead session as running once. On Windows the operating system releases a dead process's locks, but not necessarily at once, so a just-ended session may read as running for a moment. That is from memory of Windows' locking documentation. Stage 7's `test_killed_process_releases_its_session_lock` kills a process that holds its session lock and probes for up to 10 s; it runs on every platform, but has run only on Linux so far.

### At start

After `policy.loaded` and before connecting upstreams, serve folds the open holds (built during the startup verification pass, with no second read) and, for each hold of another session that is still open, probes that session. For each session that has **ended**, it appends `hold.abandoned` for each of its open holds, through the conditional append (section 6), so a hold that another process ended meanwhile, with an allow or another start's `hold.abandoned`, gets none. A session that is running or unknown is left alone.

This narrows decision 7, which said "every hold of an earlier session". Two Claude Code sessions can share one ledger, so an earlier session can still be running and waiting on its holds. Only a session proven ended is abandoned (section 17, deviation 6).

### Between Esc and the next start

What happens, from verified-facts.md (Interactive): Esc during a call made Claude Code send SIGINT to serve, then SIGTERM about 100 ms later, and serve exited within about 50 ms of the SIGTERM. A new serve started about 12 s later in one observed session; what started it is not known.

**What the person can know:**
- With the shutdown path, every held call of that process gets `hold.expired` (shutdown) and `call.refused`. `polarizer holds` then lists none of them: they are no longer open.
- If the process was killed before recording that, `holds` still lists the holds, with `ended; no process will act on a decision`, because the session lock was released when the process died.
- Every one of those calls was never forwarded: none has a `call.sent`. That is certain from the ledger alone.
- Esc ends every hold of that process at once, not only the call on screen.
- **A hold that was allowed, and whose session then died before the call was forwarded,** is not listed by `holds`: the allow is its ending, so it is no longer open, and no later start writes `hold.abandoned` for it. The ledger shows `hold.decided` allow and no `call.sent` with that hold, which reads as "allowed, never forwarded" (section 5). The call did not run. If the agent retries it in a new session, that is a new hold, and the person is asked again.

**What the person cannot know:**
- Whether Claude Code will start a new serve, and when. It did after about 12 s once, by an unknown trigger.
- Whether the model will retry the call in the new session. A retry is a new hold with a new id; a decision on the old one never carries over.
- Anything Claude Code shows. Polarizer never sees Claude Code's screen, and the model's view of the interrupted call is Claude Code's.

**What happens to a decision made in between:** `allow` or `deny` on a hold whose session has ended still records `hold.decided`, prints its result, and writes `polarizer: warning: the session that held this call has ended; no process will act on this decision` to stderr. No process acts on it. The next start finds the hold already ended and writes no `hold.abandoned` for it. When the session state is unknown, the warning is `polarizer: warning: cannot tell whether the session that held this call is running`.

**Advice for the README:** do not press Esc to go and decide a hold. The call looks slow because it is waiting for you; Esc ends it, and every other held call of that session with it.

## 8. The command line

### Syntax

```
polarizer serve --config <absolute path> [--no-holds]
polarizer holds (--config <absolute path> | --ledger-dir <absolute path>) [--wait [--bell]]
polarizer allow (--config <absolute path> | --ledger-dir <absolute path>) <hold id> [--allow-no-terminal]
polarizer deny (--config <absolute path> | --ledger-dir <absolute path>) <hold id> [--reason <text>] [--allow-no-terminal]
```

**Shared rules,** as for the pin commands (PIN-SPEC.md, section 7):
- `--config` and `--ledger-dir`: exactly one, absolute, with the same usage lines naming the command. `--config` is read with `require_env=False`, roots are not checked for existence, and no command here starts an upstream.
- `holds` only reads, like `verify` and `pending`: it creates, deletes and modifies nothing, and checks no locations. Its session probe takes a shared lock on an existing file and writes nothing.
- `allow` and `deny` write, so they check the location as `repair` and `approve` do: a forbidden `ledger_dir` is refused (exit 2), and a git working tree gets the warning.
- **A terminal.** `allow` and `deny` refuse when stdin is not a terminal, unless `--allow-no-terminal` is given: `polarizer: allow needs a terminal; pass --allow-no-terminal if this is a script` (or `deny`), exit 2, nothing written. The check comes after the usage checks and before the config is read. It is a speed bump only: an agent that can run `polarizer allow` can pass the flag too. It stops accidents, not an agent that means to allow its own calls.
- **No ledger:** `holds` prints `no ledger at <dir>` on stdout; `allow` and `deny` refuse with `polarizer: no ledger at <dir>`. Exit 2.
- Results go to stdout, refusals to stderr as one line starting `polarizer: `. Every refusal exits 2, and ledger statuses keep their codes. No exit code is added.

### `polarizer holds`

It reads the ledger and `ledger.head` as `verify` does. If the status isn't `intact`, it prints `verify`'s output for that status, lists nothing, and exits with that status's code. Otherwise it lists every open hold (a `hold.created` with no ending), in seq order, and exits 0.

```
holds: <n> open

hold <hold id> <tool> <class>
held by <rule>: <reason>
waiting about <age>; times out after <timeout> s
session <session> started <ts>, <state>
args_commit <64 hex>, <b> bytes of arguments
<arguments>

hold <hold id> <tool> unclassified
...
```

- **With nothing open,** the whole output is `holds: nothing is held`.
- **`<class>`** is the class, with ` (from annotations)` when `class_from` is `"annotations"`, or `unclassified`.
- **`<age>`** is now minus `hold.created`'s `ts`, as `<m>m<ss>s` under an hour and `<h>h<mm>m` from then on. It is the wall clock, which can step on WSL2 by about 1.1 s (verified-facts.md), so it says "about". The timeout itself is measured by serve on the monotonic clock.
- **`<ts>`** is the `session.started` `ts` of the hold's session, or `unknown` if that entry isn't found.
- **`<state>`** is `running`, `ended; no process will act on a decision`, or `state unknown`.
- **`<b>`** is the side file's size minus its 32-byte salt.
- **`<arguments>`** are the side file's JSON, rendered with `json.dumps(obj, indent=2, ensure_ascii=True)` in the key order stored, with DEL written as its JSON escape too, exactly as `pending` renders definitions (PIN-SPEC.md, section 7). Every character outside printable ASCII therefore appears as an escape, so hidden and look-alike characters show. Long values are printed whole; pipe the output to a pager for a long one.
- **A side file that fails its check** is listed in place of the arguments as one line, `arguments: side file args/<args_commit>.bin <problem>; this hold cannot be allowed`, where `<problem>` is `is missing`, `does not match its name`, `is unreadable: <the operating system's message>` or `does not hold JSON arguments`. The hash check is the one `verify --args` does.
- **Text from the ledger is escaped:** the tool, rule, reason, ts and session pass through the same function `pending` uses for ledger text, so a hand-edited ledger can't send terminal escapes.
- **Several sessions** are listed together, each hold naming its own.

**`--wait`.** Without open holds whose session is running, `holds --wait` waits: every 0.25 s it compares the ledger's size with what it read (`fstat`, no lock), and reads again under the lock only when the file grew. As soon as at least one open hold of a running session exists, it prints the listing as above and exits 0. A status other than `intact`, seen at any read, prints that status's output and exits with its code. Ctrl+C ends it without a traceback, by the default SIGINT action. This exists because nothing else tells the person that a call is held in M2a: serve's stderr goes to Claude Code's log, which recorded stderr only at connect in the one interactive check (verified-facts.md, Interactive).

**`--bell`** (only with `--wait`; decided, section 16, decision 11). While waiting, `holds --wait --bell` writes one BEL character (0x07, `\a`) to stdout the first time each new open hold of a running session appears, then carries on exactly as `--wait` does. Since `--wait` ends as soon as such a hold exists, that means: at the read that finds them, one BEL per open hold of a running session, written and flushed before the listing, then the listing, then exit 0. A hold seen at an earlier read whose session was not running gets its BEL only once that session reads as running. Without `--bell` the output is exactly as above. It is only a convenience: whether the terminal beeps, flashes or does nothing is the terminal's choice, and nothing depends on it. `--bell` without `--wait` is a usage error, `polarizer: --bell goes with --wait`, exit 2.

### `polarizer allow`

1. checks the location;
2. refuses a malformed id: `polarizer: <arg> is not a hold id (16 lowercase hex characters)`;
3. opens the ledger as a writer (2 s lock wait, full verification). A status other than `intact` gives serve's line, `polarizer: <verify's first line>; run polarizer verify`, with that status's code;
4. finds the hold in the folded ledger (an unknown id is refused as in step 5) and reads and checks its side file;
5. appends `hold.decided` (`allow`) through the conditional append, whose check refuses, writing nothing:
   - an id with no `hold.created`: `polarizer: no hold <id> in this ledger`;
   - a hold already decided: `polarizer: hold <id> was already decided at seq <q>: <allow or deny>`;
   - an expired hold: `polarizer: hold <id> expired at seq <q>: <reason>`;
   - an abandoned hold: `polarizer: hold <id> was abandoned at seq <q>`;
   - a side file that failed step 4: `polarizer: hold <id> cannot be allowed: side file args/<args_commit>.bin <problem>`;
6. prints the hold's block exactly as `holds` prints it, then `allowed hold <id> at seq <q>`, and exits 0;
7. probes the hold's session; if it has ended or is unknown, writes section 7's warning to stderr. The exit code stays 0: the decision is recorded.

The block is printed after the append succeeds, so the terminal shows what was allowed, once, with the seq that records it.

### `polarizer deny`

The same steps, except:
- `--reason` is optional. When given, it is folded to one line and cut to 1 KiB as `reject`'s is; a reason that is empty after folding is refused with `polarizer: --reason is empty; leave it out or give a reason`, exit 2, before the ledger is opened. Without it, `reason` is null.
- A side file problem doesn't stop a deny.
- The result line is `denied hold <id> at seq <q>`.

### Usage lines

Besides the shared lines, exit 2: `polarizer: allow needs <hold id>`, `polarizer: deny needs <hold id>`, and `polarizer: --no-holds goes with serve` for any other command given it.

### `pending` and `approve` with --config

When given `--config`, `pending` and `approve` also read the policy. `--ledger-dir` output is unchanged, so every existing golden file stays as it is.
- **In `pending`,** each `new` or `changed` block gains one line after its header and its `not in a group` or `more than one definition` line: `class <class>; annotations suggest <suggested>`, where `<class>` is the configured class, `<x> (from annotations)` for a trusted upstream, or `none: every call is held`. When the configured class holds less than the suggestion, the line is `class <class>, but annotations suggest <suggested>`.
- **A classes section** follows the last block (and the group line, if any), separated by one blank line. It lists every tool whose latest decision approves its live hash and which is unclassified or contradicted, by prefix and tool name:

  ```
  classes: <u> approved tools have no class, <c> contradict their annotations
  no class <prefix>__<tool> <def_hash>; annotations suggest <suggested>
  contradicts <prefix>__<tool> <def_hash>: class <class>, annotations suggest <suggested>
  ```

  With none of either, the section is left out. When nothing waits for a decision but the section has lines, the output is `pending: nothing waits for a decision`, a blank line, then the section.
- **In `approve`** (one definition), the same class line follows the printed definition.
- The suggestion comes from the stored copy's `annotations`, which is the definition the person approves.

### Golden files

Every row gets a file under `tests/golden/`, on a ledger built by a deterministic helper, with `holds`' clock injected and each session's lock held or released by the test, comparing stdout byte for byte with the exit code. The ledger behind `holds_mixed.txt` is built in the test helper from named pieces, one function per hold, each with a docstring saying what that hold shows, so a reviewer can read what each block in the golden file is (decided by the owner).

| Command and situation | Golden file | Exit |
|---|---|---|
| `holds`, nothing open | `holds_nothing.txt` | 0 |
| `holds`: three open holds from two sessions, one running and one ended; a write-pattern hold whose arguments hold non-ASCII text, a control character and DEL; an unclassified hold; a class from annotations; one hold already denied and one expired, both left out | `holds_mixed.txt` | 0 |
| `holds`, one hold whose session lock file is missing | `holds_state_unknown.txt` | 0 |
| `holds`, each side file problem (missing, altered, not JSON) | `holds_side_file_<problem>.txt` | 0 |
| `holds` on each non-intact status (tampered, invalid, torn tail, truncated) | `holds_<status>.txt` | that status's code |
| `holds`, locked; no ledger | `holds_locked.txt`, `holds_no_ledger.txt` | 7, 2 |
| `holds --wait --bell`, two open holds of a running session found at one read | `holds_wait_bell.txt` (stdout's bytes: two BEL characters, then the listing) | 0 |
| `allow` an open hold of a running session | `allow_one.txt` | 0 |
| `allow` an open hold of an ended session (stdout; the warning on stderr is compared too) | `allow_ended_session.txt` | 0 |
| `allow` refusals: bad id, no such hold, already decided, expired, abandoned, side file altered, broken ledger, no terminal | `allow_refused_<case>.txt` (stdout empty; the stderr line compared) | 2, or the status's code |
| `deny` without and with `--reason` | `deny_one.txt`, `deny_with_reason.txt` | 0 |
| `deny` refusals: empty reason, already decided, no terminal | `deny_refused_<case>.txt` (stderr) | 2 |
| usage errors for `holds`, `allow`, `deny` and `--no-holds` | `usage_holds.txt` (stderr, one line per case) | 2 |
| `pending --config`: blocks with class lines, a contradiction, an unclassified approved tool | `pending_classes.txt` | 0 |
| `pending --config`, nothing waiting, one unclassified approved tool | `pending_classes_only.txt` | 0 |
| `approve --config` one definition | `approve_one_with_class.txt` | 0 |

## 9. Time

**Polarizer's own timeout.** `hold_timeout_seconds`, default 300, at most 1200, measured by serve on the monotonic clock from the moment `hold.created` is written. On timeout the call is refused, with `hold.expired` and `call.refused` recorded (section 6).

**What is verified about Claude Code and a long pending call:**
- **The hard limit** per call is the first of the server's `timeout` in the MCP config, `MCP_TOOL_TIMEOUT`, or a default of 100,000,000 ms (about 27.8 hours). Read from the binary; the default was never observed. Observed headless: with `MCP_TOOL_TIMEOUT=5000`, a 14 s call was cancelled after 5 s with `notifications/cancelled`, and progress every 2 s did not extend the limit (verified-facts.md, Hard per-call timeout and Spike). So a person who sets `MCP_TOOL_TIMEOUT` below the hold timeout gets Claude Code's cancel first: the hold ends as `the client cancelled the call`.
- **The idle timeout** is 1,800,000 ms (30 minutes) for stdio by default, reset by a response or a progress notification. That is read from the binary only. Headless, with `CLAUDE_CODE_MCP_TOOL_IDLE_TIMEOUT=6000` and no progress, a 14 s call completed normally: the idle timeout did not fire. Decision 5 cited it as headless; it is binary (section 17, deviation 9). Whether it applies at all is unknown. The 1200 s cap keeps a hold under it either way, and the progress every 10 s costs nothing.
- **Esc** ends serve (section 7).

**What the owner observed interactively** (the M2a check, Oct 5, 2026, Claude Code 2.1.289; verified-facts.md, M2a check, interactive):
- **While a call is held,** Claude Code shows its call line (`polarizer - fs__write_file (MCP)(path: ..., content: ...)`), and the person can keep typing.
- **After about 123 s, Claude Code moves a still-running call to the background** as a task, says so, and the agent keeps working. The held call still waits in serve, and the hold is unchanged; when the person decides, the result reaches the agent as a task-completed notification. In the check, a call held for 128 s and then allowed returned `ok` that way. So while the first call waits, the agent can make other calls, including the same call again, which is a new hold with its own id (section 6), and `polarizer holds` lists both.
- **The default `hold_timeout_seconds` (300) is deliberately longer than that window.** A person usually needs more than two minutes to notice a hold, read it and decide, and a call that Claude Code has moved to the background but that still waits for the person is acceptable: it is still held, still bound to its exact arguments, and still refused if nobody answers. A shorter default would refuse calls a person was about to allow, to avoid a state that does no harm.
- **A deny and an expiry** reach the agent as the one line `polarizer: <tool> was not allowed`. A retry after a deny is a new hold; the earlier deny does not carry over.

**What is not verified, and is not assumed:**
- whether `TaskStop` on a call Claude Code moved to the background sends `notifications/cancelled` to Polarizer (if it does, the hold ends as `the client cancelled the call`; if not, it waits for the person or the timeout);
- what happens to a backgrounded held call when the session exits (Claude Code says the task "does not survive exiting this session"; whether serve sees end of input, a signal or a cancel first is unknown);
- whether the 123 s before a call is moved to the background is fixed or configurable;
- whether an interactive session applies the idle timeout, and whether it honors progress there;
- whether anything else in Claude Code ends a call that waits for 20 minutes.

**Open question for M3:** should the card show that a held call has probably been moved to the background, once its age passes about 120 s, so the person knows the agent may already have gone on (and may have asked again)? The 123 s is Claude Code's, observed once, and may change between versions.

## 10. Failing closed

- **The policy can't be loaded.** Any config error stops `serve` before any upstream starts, exit 2, as every config error does (section 2). There is no fallback policy.
- **No `[policy]` table.** Every default applies: no roots, so every `local-write` call is held, and every unclassified tool is held.
- **A path can't be resolved,** is missing or isn't a string: the call is held (section 3).
- **A local-write tool with no path arguments configured:** held on every call (row 5).
- **The ledger can't record a hold:** the call is neither held nor forwarded (section 6).
- **The writer stops while calls wait:** every held call is answered as unrecordable and nothing is forwarded (section 6).
- **serve's own fsync before forwarding an allow fails:** the call is not forwarded and the writer stops (section 6).
- **A hold's side file fails its check:** `allow` refuses (section 8).
- **Startup with a ledger that isn't intact** exits as in M1a; a fold of a hold entry that raises is treated as a pin-state fold failure (PIN-SPEC.md, section 8): refuse to start, or stop the writer once running.

## 11. Existing tests, the manual check and the scripts

- **In-memory tests.** `tests/helpers/rig.py` gains `classify(config, cls="local-read")`, which adds a `tools` entry of that class, with no path arguments, for every tool name each configured fake or the probe declares. `rig.gateway()`, `rig.proxied()` and `rig.prime()` gain `policy=`, defaulting to the classified policy, so M0 and M1a tests see no holds without `--no-holds`. Hold tests pass their own policy. Entry counts grow by one `policy.loaded` per session; tests already count by kind.
- **Stdio tests** (`test_stdout`, `test_fidelity`'s raw tests, `test_reference`, the shutdown tests) write configs through a helper that classifies the probe's seven tools, and the reference servers' tools as the example config does.
- **`polarizer.example.toml`** gains a `[policy]` table with `workspace_roots = ["/tmp/polarizer-manual"]`, the probe's tools classified `local-read` (all seven are harmless test tools), and the Filesystem tools as in section 2. The manual check therefore holds `fs__write_file` outside the directory and `fs__move_file` always, which is what its M2a section exercises.
- **`scripts/live-check.sh`, `scripts/rugpull-check.sh` and `scripts/m1a-check.sh`** generate or edit their tomls; each classifies the probe's `wait` (and the others) as `local-read`, so their pass criteria don't change. Their tests check the generated toml.
- **docs/MANUAL-CHECK.md** gains an M2a section (stage 7): ask Claude to write a file under `.git/hooks` in the manual directory, run `polarizer holds --wait` in another terminal, read the hold, deny it; ask again and allow it; ask for a `move_file` and let it time out with `hold_timeout_seconds` set to 30; note what Claude Code showed each time and how long the call appeared to run. And the long wait in section 9.
- **`--no-holds`** is documented in QUICKSTART-DRAFT.md as the way to run with nothing held, with the warning that it is recorded in the ledger at every start.

## 12. Canaries later (M6)

A canary is a hold that is never forwarded, offered to see whether the person catches it. M2a builds none, but it keeps a later canary indistinguishable from a real hold until the person decides. `hold.created` records only what serve could also produce for a call it made up: the session, a random hold id, a tool name, an args_commit with its side file, a class, a rule and a reason. Nothing from the MCP request (request id, `client_call_id`, progress token, `_meta`) is recorded until `call.sent`, which only an allowed call gets. `holds` prints the same block for any hold; hold ids are random, not counters; the ending kinds and `allow`/`deny` behave the same for any hold. A held call's first ledger entry is its `hold.created`, so no earlier entry gives a real one away. **What M6 will have to add:** a generator inside serve, so a canary has the same session as real holds and its session reads as running; plausible arguments and timing alongside real calls, since a hold that appears while the agent is idle gives itself away to someone watching Claude Code; a commitment in every `hold.created`, real ones included (for example `sha256(salt || "real" or "canary")`, with the salt in a side file), so the reveal after the decision can be checked; a reveal entry written after each decision; the ending of a canary that is allowed (never forwarded, and no `call.*` entry, since there is no call); and the cap and opt-in from milestones.md. Adding the commitment field to every hold then changes no M2a reader.

**Canaries and the cap of 16 open holds** (section 6, Too many holds; decided by the owner). A canary is an open hold of its session, so it counts toward the 16. M6 must not issue a canary when the cap is near: a real call refused at once because a canary took the last place would tell anyone watching that one of the waiting holds is not real, which gives the canary away. M6 must choose how near is too near, and must say in its disclosure to the person that canaries are never issued close to the cap, and why.

## 13. Tests

"Default" means the test runs in `scripts/test.sh` and CI on every platform. "Reference" means it runs only with `POLARIZER_REFERENCE=1`, locally, never in CI. "POSIX" means it is skipped on Windows, with the reason in the test. "Windows" means it runs only there. "Symlinks" means it is skipped, with the reason, where `os.symlink` raises `OSError` (Windows without the symlink privilege).

### `tests/test_policy_config.py`

| Test | Claim | Suite |
|---|---|---|
| `test_policy_errors` | Each error line in section 2 comes out exactly, exit 2, and serve starts no upstream. | Default |
| `test_no_policy_table_holds_everything` | A config with no `[policy]` and no `tools` loads; every tool is unclassified; serve writes the `<n> of <m> listed tools have no class` line once, after startup listing. | Default |
| `test_no_holds_is_a_flag_only` | `--no-holds` records `policy.loaded` with `holds` `"off"`, fsynced before any upstream connects (counted through `FileOps`), and writes the warning; `holds = false` in `[policy]` or at the top level is an unknown-key error. | Default |
| `test_policy_loaded_fields` | `policy.loaded` follows `session.started`, with the policy hash of a fixed config equal to a value written in the test, the resolved roots, the count of configured entries and the timeout. | Default |
| `test_roots_must_exist_for_serve_only` | A missing root stops serve with its line; `holds`, `pending`, `verify` with `--config` don't check it. | Default |
| `test_annotation_class_line` | With a trusted upstream and tools without a `tools` entry, serve writes the annotations line once with `<n>`, `<m>` and the prefixes; with none, or under `--no-holds`, it writes nothing. | Default |
| `test_example_config_matches_reference_servers` | Every tool named in `polarizer.example.toml` for `fs` is listed by the pinned Filesystem server, and every listed tool is named. | Reference |

### `tests/test_paths.py`

| Test | Claim | Suite |
|---|---|---|
| `test_symlink_escape_is_held` | A root R with `R/link` pointing outside: a write to `R/link/x` resolves outside and is held, `outside-roots`. | Default; Symlinks |
| `test_dotdot_escape_is_held` | `R/../outside/x` resolves outside and is held; `R/a/../b` with `a` existing stays inside and runs. | Default |
| `test_dotdot_after_missing_part_is_held` | `R/new/../../x`, where `new` doesn't exist, is `path-unresolvable`. | Default |
| `test_symlink_into_a_pattern_is_held` | `R/h` linked to `R/.git/hooks`: a write to `R/h/pre-commit` is `write-pattern`. | Default; Symlinks |
| `test_dangling_link_resolves_to_target` | A write through a link whose target doesn't exist is judged by the target. | Default; Symlinks |
| `test_link_loop_is_held` | A loop is `too many symbolic links`. | Default; Symlinks |
| `test_unreadable_part_is_held` | A directory with no search permission makes a path below it `cannot be examined`. | POSIX: permissions work differently on Windows |
| `test_relative_tilde_nul_are_held` | `a/b`, `~/x` and a path with NUL are held with their fixed reasons. | Default |
| `test_slow_resolution_is_held` | With the resolver replaced by one that sleeps past the bound (the bound injected), the call is held, `took longer than 2 s to resolve`. | Default |
| `test_path_arg_not_a_string_is_held` | A number, null, an object and a list holding a number are each `path-not-string`; a missing argument is `path-missing`; an empty list runs. | Default |
| `test_more_than_256_paths_is_held` | A list of 257 strings is `path-unresolvable`. | Default |
| `test_stuck_resolutions_are_capped` | With a resolver that blocks, 8 paths pass the bound and stay stuck; the 9th is held at once with `too many path resolutions are stuck` and its resolver never runs; once the blocked threads return, paths resolve again. | Default |
| `test_pattern_matching` | Anchored, floating, `**` at the end, `*` and `?`, against fixed paths, with the case rule given as a parameter: exact (Linux), and casefold plus NFC (Windows and macOS). | Default |
| `test_case_rule_on_this_platform` | On this platform's real file system, a write to `R/.GIT/hooks/x` inside a root is held on Windows and macOS (casefold) and runs on Linux, where `.GIT` is another directory. | Default |
| `test_roots_compare_like_forbidden_paths` | Inside-root checks use `ledgerdir.is_inside`: `/a/proj2` is not inside `/a/proj`, and a differently cased root follows the platform. | Default |
| `test_windows_names_are_held` | A stream colon, a trailing dot or space, `CON` and `nul.txt`, and a `\\?\` prefix are each held with the Windows reason; `C:a` and `\a` are not absolute. | Windows |
| `test_polarizer_files_are_held` | A write to the config file, and a read or write inside `ledger_dir`, are `polarizer-files`, also through a symlink. | Default |

### `tests/test_rules.py`

| Test | Claim | Suite |
|---|---|---|
| `test_rule_table` | One case per row of section 4's table (rows 1 to 20), each asserting action, rule and reason exactly; parametrized with the row number as its id. | Default |
| `test_first_match_order` | A local-write call with two path arguments, the first outside the roots and the second on a pattern, records the first argument's rule; reversing the config order reverses it. | Default |
| `test_builtin_patterns_cannot_be_removed` | Each built-in write and read pattern holds, with a config whose own lists are empty. | Default |
| `test_annotation_suggestions` | Each row of the suggestion table, plus no annotations (`destructive`) and a non-boolean hint (absent). | Default |
| `test_contradiction_rank` | `local-read` configured and `destructive` suggested contradicts; `destructive` configured and `local-read` suggested doesn't; `local-read` against `open-world` doesn't. | Default |
| `test_trusted_annotations_classify` | A tool with no class on a `trust_annotations` upstream takes the suggested class, recorded with `class_from` `"annotations"` and the reason suffix; a configured class wins. | Default |
| `test_destructive_and_egress_name_the_path` | A destructive tool's path on a write pattern, outside the roots, unresolvable or missing, and an egress tool's path on a read pattern, each append that rule's reason to the class's reason, with the rule unchanged; a path that no rule holds leaves the plain reason. | Default |

### `tests/test_holds.py` (in-memory gateway unless noted)

| Test | Claim | Suite |
|---|---|---|
| `test_held_call_does_not_reach_upstream_until_allowed` | A held call records `hold.created`, the upstream sees no call through ten watch intervals, and after `allow` it sees exactly one call with the exact arguments, and the client gets its result. | Default |
| `test_deny_never_reaches_upstream` | After `deny`, the client gets `polarizer: <name> was not allowed`, the ledger has `hold.decided` deny and `call.refused` with `hold`, and the upstream never sees the call. | Default |
| `test_expiry_never_reaches_upstream` | With the timeout injected at 0.2 s, the hold records `hold.expired` (timeout) and `call.refused`, the client gets the same line, and the upstream sees nothing. | Default |
| `test_unclassified_tool_is_held` | A call to a tool with no class is held with rule `unclassified`. | Default |
| `test_decision_is_bound_to_the_arguments` | Two calls to one tool with different arguments make two holds; allowing the first forwards only its arguments, and the second stays held. A hand-written `hold.decided` naming the second hold with the first's `args_commit` is ignored, with the stderr line. | Default |
| `test_decision_is_single_use` | A second `allow` or `deny` of a decided hold is refused and writes nothing; a hand-appended second `hold.decided` has no effect; a retry of the same call is a new hold that the old decision doesn't cover. | Default |
| `test_decision_for_another_session_is_ignored` | Two gateways on one ledger, each with a held call: allowing the first's hold forwards only through the first, and the second's stays held. | Default |
| `test_two_holds_decided_out_of_order` | Holds A then B; deny B, then allow A: B is refused first, A forwarded after, each with exactly one ending and one terminal entry. | Default |
| `test_client_cancel_ends_the_hold` | `notifications/cancelled` while held records `hold.expired` (client) and `call.refused`, nothing else, and the upstream sees nothing. | Default |
| `test_cancel_after_allow_before_forward` | With the forward held back after the allow is adopted, a client cancel records only `call.refused` with `the client cancelled the call before it was forwarded`; the hold's ending is the allow. | Default |
| `test_allow_racing_timeout` | With the decision's append and the timeout's conditional append ordered both ways (through `FileOps`), exactly one ending is written, and serve acts on whichever the ledger has first. | Default |
| `test_reroute_after_allow` | A tool rejected (M1a) while its call is held is refused after the allow, with M1a's reason and `hold`, and M1a's client line. | Default |
| `test_writer_stop_ends_waiting_holds` | A line that doesn't chain, appended while two calls are held: both clients get the "could not record it" line at once, nothing is forwarded, and nothing more is written. | Default |
| `test_too_many_holds` | With 16 holds open, a 17th call that would be held is refused at once, with no side file and no `hold.created`; one that would run still runs. | Default |
| `test_progress_during_a_hold` | With the interval injected at 0.05 s and a client that asked for progress, at least three progress notifications arrive while held, with increasing progress, no total and no message; a client that asked for none gets none. In memory and over stdio. | Default |
| `test_one_terminal_entry_per_call` | After a scripted run of every ending in section 5's table that a live process can produce, a fold of the ledger alone finds exactly one ending per hold and exactly one terminal entry per held call, and no `call.sent` for any hold that wasn't allowed. | Default |
| `test_model_line_has_no_details` | For deny, timeout and too-many-holds, the client's text is exactly `polarizer: <name> was not allowed`, and contains no rule, reason, path or deny reason. | Default |
| `test_hold_state_from_ledger_matches_live` | After a scripted run, the hold fold over the ledger file alone equals the gateway's. | Default |
| `test_forwarded_arguments_come_from_the_side_file` | The in-memory arguments of a held call are changed after `hold.created` is written; after `allow`, the upstream receives exactly the side file's arguments, and `call.sent` has `hold` and `allowed_by` `"hold"`. | Default |
| `test_allow_with_altered_side_file_is_refused` | A hold's side file altered after the allow is recorded and before serve forwards (`allow` itself refuses an altered file, so the allow comes first): serve records `call.refused` with `hold` and `hold <id> was allowed, but its side file does not match its name`, nothing is forwarded, and the client gets `polarizer: <name> was not allowed`. The same with the side file removed (`is missing`). | Default |
| `test_allowed_by_records_the_rule` | `call.sent` of an unheld call records the rule that let it run (`local-read`, `inside-roots`, `open-world`, and `holds-off` under `--no-holds`). | Default |

### `tests/test_hold_durability.py`

| Test | Claim | Suite |
|---|---|---|
| `test_allow_fsynced_before_forward` | With the deciding writer's fsync held on an event, the upstream sees no call through ten watch intervals; once released, the call is forwarded. | Default |
| `test_serve_fsyncs_adopted_allow` | When the deciding writer's fsync raises after its write, serve calls its own fsync (counted through `FileOps`) before forwarding; if that raises too, nothing is forwarded, the writer stops, and the client gets the "could not record it" line. | Default |
| `test_allow_from_second_process` | `python -m polarizer allow --allow-no-terminal` run as a subprocess while a gateway holds a call forwards it within 2 s (watch every 0.25 s while held). | Default |
| `test_allow_updates_head` | After `allow`, `ledger.head` names the decision's seq; after `holds`, nothing changed (paths, sizes and mtimes). | Default |

### `tests/test_hold_restart.py`

| Test | Claim | Suite |
|---|---|---|
| `test_restart_records_abandoned_holds` | A gateway with two open holds, its session lock released without shutdown work (the process killed, or the lock closed in-process): a new gateway writes one `hold.abandoned` per hold, naming `held_by`, and none on a third start. | Default |
| `test_running_session_is_not_abandoned` | A new gateway on a ledger whose other session still holds its lock writes no `hold.abandoned`, and the other gateway's hold can still be allowed. | Default |
| `test_unknown_session_is_not_abandoned` | With the other session's lock file removed, the new gateway writes nothing, and `holds` shows `state unknown`. | Default |
| `test_decision_on_ended_session_warns` | `allow` on a hold whose session has ended records the decision, exits 0, and writes the warning; the next start writes no `hold.abandoned` for it. | Default |
| `test_esc_during_a_hold` | `polarizer serve` over stdio holds a call; SIGINT, then SIGTERM 100 ms later. serve exits 0, the ledger verifies intact, the hold has `hold.expired` (shutdown) and one `call.refused`, the probe saw no `tools/call`, and a new serve writes no `hold.abandoned`. | POSIX: Windows has no way to send the two signals as Claude Code does (PIN-SPEC.md, section 10) |
| `test_kill_during_a_hold` | SIGKILL while a call is held: the ledger verifies intact with an open hold and no terminal entry; `holds` shows the session ended; a new serve writes `hold.abandoned`, and then there is exactly one terminal entry. | POSIX, for the same reason |
| `test_holds_wait` | `holds --wait` started before any hold prints the listing and exits 0 within 2 s of a hold appearing; with only a dead session's open hold it keeps waiting. | Default |
| `test_holds_wait_bell` | `holds --wait --bell` writes one BEL per new open hold of a running session before the listing, byte for byte as `holds_wait_bell.txt`; without `--bell` no BEL is written; `--bell` without `--wait` is the usage line. | Default |
| `test_stdin_closed_during_a_hold` (stage 7) | End of input while a call is held takes the same shutdown path as the signals: `hold.expired` (shutdown), one `call.refused`, nothing forwarded. | Default, so shutdown during a hold is tested on Windows too |
| `test_killed_process_releases_its_session_lock` (stage 7) | A process killed outright (SIGKILL, or TerminateProcess on Windows) leaves its session lock free within 10 s. | Default |

### `tests/test_hold_cli.py` and `tests/test_golden.py`

| Test | Claim | Suite |
|---|---|---|
| `test_golden.py` (new rows) | Every row of section 8's golden table. | Default |
| `test_holds_escapes_ledger_text` | Control characters, DEL and non-ASCII text in a hand-written hold's tool, rule, reason and session print as escapes. | Default |
| `test_allow_needs_terminal` | `allow` and `deny` without a terminal refuse with the one line, exit 2, and write nothing; with `--allow-no-terminal` they run; on POSIX, run on a pseudo-terminal they run without it. | Default; the pseudo-terminal case POSIX |
| `test_hold_commands_refuse_forbidden_ledger_dir` | `allow` and `deny` refuse a forbidden `ledger_dir`; `holds` doesn't check. | Default |
| `test_concurrent_allows_write_one_decision` | Two `allow` processes for one hold at once: exactly one `hold.decided`, the other refused as already decided. | Default |
| `test_hold_commands_subprocess` | `python -m polarizer holds` prints the golden bytes, including arguments with non-ASCII text shown as escapes. | Default |
| `test_pending_shows_classes` | `pending --config` prints the class lines and the classes section, and `pending --ledger-dir` prints exactly what it did before. | Default |

### `tests/test_args.py`

| Test | Claim | Suite |
|---|---|---|
| `test_hold_side_files_are_not_orphaned` | The side files of a denied hold and an expired hold are matching, not orphaned; an allowed hold's is counted once; a stray file is still orphaned; a deleted held file is missing and an altered one tampered, exit 8. | Default |

### Fixtures

| Test | Claim | Suite |
|---|---|---|
| `test_fixtures.py`, `test_conformance.py` (new valid fixture) | A generated chain holding every new kind and the new optional fields verifies in both verifiers. | Default |

## 14. Threat model, for the README

Draft text, describing only M2a with M1a:

> **What holding does.** You give each tool a class in `polarizer.toml`. Reads run. Writes run when every path you told Polarizer to check is inside your workspace, and isn't one of a few sensitive places such as `.git/hooks`, your shell start-up files, `~/.ssh` or Claude Code's own settings. Destructive and outgoing calls, and calls to tools you haven't classified, are held: the call waits, and nothing reaches the server until you run `polarizer allow` with the hold's id. `polarizer holds` shows each waiting call with its exact arguments, every unusual character escaped. If you deny it or don't answer within five minutes, the call is refused, and the agent sees only that it was not allowed. Every hold, decision and outcome is in the ledger.
>
> **What it does not do.** Polarizer only sees calls routed through it; the agent's own shell, file tools and MCP servers configured directly in Claude Code are outside it. An agent whose call was refused can try another route itself: in the M2a check, the model whose move was refused named Bash `mv` as a route it chose not to take, because it would get around the proxy. A class is your statement about a tool; Polarizer cannot check what the tool really does, and a tool can reach files in ways that never appear in its arguments. Paths are checked only in the top-level arguments you name, and only when the call arrives; a path nested inside an argument (a list of edits, each with its own path) is not checked in this version. A tool's own annotations are shown as a hint and are trusted only if you say so; a server you mark with `trust_annotations` chooses its own class, so its tools' annotations decide which of its calls wait, and Polarizer says at start how many tools that covers. A hold is only as good as the person reading it: if you allow without reading, Polarizer records that you allowed it. An agent that can run commands as you can also run `polarizer allow`; Polarizer refuses unless the command is started from a terminal or told it is a script, which only stops accidents.
>
> **Known gaps in the path checks.** On Windows, the device names `COM1` to `COM3` and `LPT1` to `LPT3` written with a superscript digit are not recognized as device names. If you move Claude Code's configuration with `CLAUDE_CONFIG_DIR`, the built-in patterns don't follow it; add the new place to `write_hold_patterns`. A `~` in a pattern means the home directory in Polarizer's `HOME` when it starts, not your account's home.
>
> **What it relies on.** A held call lives inside the Polarizer process Claude Code started. Pressing Esc in Claude Code stops that process and ends every call it was holding; none of them reaches the server, and the ledger says so. If you allow a call and that process dies before it forwards it, the call does not run either: `polarizer holds` no longer lists it, because you decided it, and if the agent tries the call again you are asked again. A call that waits does not stop the agent: after about two minutes, Claude Code moves the waiting call to the background and the agent keeps working, so it can make other calls, or ask for the same one again (a new hold), while the first still waits for you. The hold doesn't change: nothing runs until you allow it, and it is refused at the timeout. Polarizer has no page or notification for holds yet: run `polarizer holds --wait` in another terminal to see them as they arrive (add `--bell` to have the terminal ring).

## 15. Build order and size

M2a stays **medium**, as milestones.md says: about one to two weeks, in two stages that each end in a stop for review.

### Stage 6: policy and holds

1. **Config:** `[policy]`, `trust_annotations`, `tools`, the errors, the policy hash, `--no-holds`. Done when `test_policy_config.py` passes, except the reference test. Small.
2. **Paths and rules:** resolution, roots, patterns, built-ins, `evaluate`, annotation suggestions. Done when `test_paths.py` and `test_rules.py` pass on Linux. Medium.
3. **Kinds and the hold fold:** the five kinds, the optional fields, `SECURITY_KINDS`, the fold with first-ending-wins, the conditional append, generated fixtures. Done when the fixtures verify in both verifiers and the fold equals the expected state on a generated ledger. Small.
4. **serve:** policy at start (`policy.loaded`, the unclassified line), evaluation after routing, creating and waiting, every ending, the second routing, the cap, the watch at 0.25 s while holds are open, serve's fsync before forwarding. Done when `test_holds.py` passes except `test_progress_during_a_hold`, and `test_hold_durability.py` passes. Medium.
5. **CLI:** `holds` (without `--wait`), `allow`, `deny`, golden files, the terminal check, and the read side of session state: the probe of a session's lock file, the `<state>` in `holds` and the warning on `allow` and `deny`, because `holds_mixed.txt`, `holds_state_unknown.txt` and `allow_ended_session.txt` need them (stage 6, step 0). Done when `test_hold_cli.py` and the new golden rows pass, except the `pending --config` rows and `holds_wait_bell.txt`. Small to medium.
6. **Helpers, existing tests, scripts:** `rig.classify`, `policy=`, the stdio helpers, `polarizer.example.toml`, the three scripts' tomls and their tests. Done when the whole earlier suite passes unchanged in what it asserts, apart from entry counts by kind. Small.

**Stop for review.** Holds work while serve runs. serve creates no session lock yet, so `holds` shows its holds as `state unknown` and `allow` and `deny` warn that they cannot tell whether the session runs; a dead session's holds stay open in the ledger, and the person must poll `holds`. A held call cancelled by shutdown records nothing yet: its hold stays open, as if the process had been killed.

### Stage 7: lifetime, progress and visibility

1. **Session locks and restart:** serve's own lock file, `hold.abandoned` at start (the probe, the session state in `holds` and the warnings are stage 6's). Done when `test_hold_restart.py` passes, except `test_holds_wait` and `test_holds_wait_bell`, with its POSIX skips in place. Small to medium.
2. **Shutdown during holds:** held handlers record their ending in the shutdown path. Done with `test_esc_during_a_hold`. Small.
3. **Progress:** done when `test_progress_during_a_hold` passes. Small.
4. **`holds --wait` and `--bell`:** done when `test_holds_wait`, `test_holds_wait_bell` and `holds_wait_bell.txt` pass. Small.
5. **Classes in `pending` and `approve`:** done when `test_pending_shows_classes` and the remaining golden rows pass. Small.
6. **Reference check of the example config:** `test_example_config_matches_reference_servers`, run locally with `POLARIZER_REFERENCE=1`. Small.
7. **Docs:** MANUAL-CHECK.md's M2a section, QUICKSTART-DRAFT.md's `--no-holds` and the threat model, STAGE6 and STAGE7 notes with claims tables. Small.

**Stop for review,** then the owner's manual check, including the long wait in section 9. The owner ran it on Oct 5, 2026 (section 17, The owner's M2a check).

## 16. Decided

The owner answered the spec round's questions on Oct 4, 2026, and added question 11 (the bell). Each answer is in the sections above; in one line each:

1. **Removing a built-in pattern:** no removal key; `--no-holds` is the only way around the built-in patterns (section 3).
2. **The read hold list:** kept as it is (section 3, deviation 2).
3. **Nested path arguments** (`edits[].path`): in M2b, not now; the README states the limit (sections 3 and 14).
4. **Relative paths:** kept held; no `relative_to` key (section 3).
5. **Recording the allow rule:** `call.sent` gains the optional `allowed_by`, the rule that let a call run or `"hold"` (section 5).
6. **Session lock files:** left in place, one empty file per session; no cleanup (section 7).
7. **The cap of 16 open holds per session:** 16 is fine (section 6); a canary counts toward it (section 12).
8. **M3's done-when:** milestones.md's M3 row now says decisions are fsynced before acting and expiries and cancels are not, as section 5 does.
9. **Claude Code configuration in the write list:** agreed; `CLAUDE.md` stays out of it (section 3).
10. **Workspace roots from the client:** Polarizer does not ask the client for roots (section 2).
11. **A bell for `holds --wait`:** the optional `--bell`, a convenience only (section 8).

The owner also decided, with the same answers: after an allow, serve forwards the arguments read back from the side file and checked against `args_commit`, never its in-memory copy (section 6, Allow); a hold allowed and then orphaned by its session's death is documented in section 7 and the README text (section 14); and `holds_mixed.txt`'s ledger is built from named pieces (section 8, Golden files).

## 17. Deviations and guesses

### The owner's decisions this builds on

Given with the round's brief on Oct 4, 2026; "decision <n>" above refers to these:

1. **A hold lives in serve,** recorded in the ledger, and dies with the process; the CLI is M2a's approval surface and M3's card will use the same kinds.
2. **What is held:** five classes, unclassified tools held, annotations a suggestion only, the default rules, per-tool path arguments resolved before comparison, and no use of the words "protected paths" for a config key.
3. **A decision is bound to the exact call:** a hold id, single use, and serve honors only its own session's holds.
4. **Kinds and ordering:** `hold.created`, `hold.decided`, `hold.expired`, `hold.abandoned`; `call.sent` only after an allow; only entries that can make Polarizer do more are fsynced before it acts.
5. **Time:** Polarizer's own timeout, default 300 s, at most 1200 s, and progress every 10 s.
6. **What the model sees:** nothing while pending, one line on deny or expiry.
7. **Restart:** abandoned holds recorded at start, and what a person can know after Esc.
8. **The commands:** `holds`, `allow`, `deny`, with the pin commands' rules.
9. **Concurrency:** many holds, decisions in any order, the client's cancel and shutdown each ending a hold once.
10. **Canaries:** nothing that would make a later canary distinguishable before the person decides.

### What step 0 found

1. **milestones.md says "Unknown tools are denied"** for M2a; decision 2 says unclassified tools are held. Decided: held, and the second commit changes milestones.md's M2a row to say so.
2. **docs/PLAN.md doesn't exist.** CLAUDE.md lists it "if it exists", and decision 10 cites "PLAN.md, M6". This spec uses milestones.md's M5 + M6 row.
3. **LEDGER-SPEC.md's fsync policy** lists "later milestones' holds and decisions" as security-state entries, while decision 4 fsyncs only entries that can make Polarizer do more. Decided by decision 4 (deviation 5).
4. **milestones.md's M3 row** said timeout and cancel are fsynced before acting. The owner decided to change the row to match this spec (section 16, decision 8).
5. **Decision 5 cites the idle timeout as headless;** verified-facts.md has it from the binary, and the headless observation is that it did not fire (deviation 9).

### The M2a review (Oct 4, 2026)

After stage 6, the owner's review of the rule function and of CI asked for these changes, made in the commit "spec: m2a review" and built in "stage6 fixes":
1. **Destructive and egress calls name a path** that a path rule would hold, in the reason only (section 4, rows 3 and 4).
2. **serve says how many tools take their class from annotations** (section 2, Classes taken from annotations).
3. **At most 8 stuck resolutions** per process; further paths are held at once (section 3).
4. **Known limits** of the path checks are stated (section 3, Known limits, and section 14).
5. **`verify --args` counts `hold.created` as a reference** (section 5), as stage 6's notes proposed under "Found, not changed". The owner confirmed that a tampered side file of a never-forwarded hold exits 8, as any tampered side file does.

### Deviations from the decisions and the existing documents

1. **Built-in patterns can't be removed,** and the config adds to them, like `ledger_forbidden_paths` and Parallax's directories. Decision 2 listed defaults without saying.
2. **Reads of secret stores are held** (the built-in read list), beyond decision 2's "reads run". Reason: a read of `~/.ssh/id_ed25519` puts the key in the model's context, from where the agent's shell (outside Polarizer) or an egress call a tired person allows can carry it out, and M2b's taint doesn't exist yet. Kept (section 16, decision 2).
3. **The write list adds** `.git/config`, Claude Code's configuration and Polarizer's own files (`ledger_dir`, the `--config` file) to decision 2's list. Reason: each lets a write run code or remove the guard: `core.fsmonitor`, a Claude Code hook or MCP server, or a rewritten class.
4. **Relative paths and `~` are held,** not resolved. Decision 2 said to resolve with the real path; that is done for absolute paths, and the rest can't be resolved the way the upstream would.
5. **`hold.created`, `hold.expired` and `hold.abandoned` are not fsynced;** `hold.decided` and `policy.loaded` are, as decision 4 asks. The second commit of this round changes only LEDGER-SPEC.md's kinds table, where each new row says whether it is fsynced. Its fsync policy sentence ("later milestones' holds and decisions") and its files table (`sessions/`) are updated in stage 6, with the code; until then this file wins.
6. **Abandonment needs proof that a session ended.** Decision 7 said "every hold of an earlier session"; with two sessions on one ledger that would abandon a live process's holds. Per-session lock files give the proof without process ids. They add `sessions/` to `ledger_dir`.
7. **`call.refused` and `call.sent` gain an optional `hold`,** and the set of terminal entries for a held call includes `hold.abandoned`. Decision 4 asked for one terminal entry per call; a process killed while holding can't write one, so the next start's `hold.abandoned` stands in, as section 5's table shows.
8. **`hold.decided` carries `args_commit`,** and serve ignores a decision whose commitment differs from its hold's. Decision 3 named only the hold id; the extra field binds the decision to the arguments in the entry itself.
9. **Time facts are cited as verified-facts.md has them:** the idle timeout from the binary, not headless.
10. **M2a has no deny rule of its own.** Decision 2's order (deny, hold, allow) is kept for M2b; the cap of 16 open holds acts like one, after the rule function.
11. **The cap of 16 open holds per session** is this spec's addition.
12. **`polarizer holds --wait`** is this spec's addition, so the person can learn that a call is held without polling.
13. **The watch runs every 0.25 s while a hold is open,** not once a second, so an allow is acted on within about a quarter of a second.
14. **A `policy.loaded` kind** records the policy at every start, rather than a new field on `session.started`, which stays as M0 has it.
15. **`--no-holds`** is the documented way to run with nothing held: a flag recorded in `policy.loaded`, as the round's brief asked, using mcpclerk's idea of a switch that is only a flag (credited by name). m0-plan.md's credits list that idea under M3; the README's credits should name M2a too.
16. **After an allow, serve routes the call again** (pins), but doesn't evaluate the policy again.
17. **`pending` and `approve` show classes only with `--config`,** so `--ledger-dir` output and every existing golden file stay unchanged.
18. **The suggested class from annotations** follows a table chosen here (section 4), with MCP's defaults for absent hints, so a tool with no annotations suggests `destructive`.
19. **Contradiction** means a configured class that holds less than the suggestion; the other direction is not shown.
20. **Test file names, golden file names, rule ids and every exact output line** are this spec's choice.
21. **The Filesystem tool list in the example** was not checked in this round (section 2); stage 7 checks it against the pinned server.
22. **Windows path rules** (stream colons, trailing dots and spaces, device names, `\\?\`) are from reading Windows' naming rules, not run here; `test_windows_names_are_held` runs only on the Windows CI runner.
23. **`--reason` on `deny` refuses an empty reason** rather than recording null, so a typo isn't recorded as a reason.
24. **The writer's conditional append** is a new facility in `writer.py`; no format change.
25. **`allowed_by` on `call.sent`** (owner's decision, section 16, decision 5): an optional field naming the rule that let a call run, or `"hold"`.
26. **Forwarded arguments come from the side file** (owner's decision): after an allow, serve checks the side file against `args_commit` and forwards what it parses from it; a failed check refuses the call with a fixed reason (section 6, Allow). The four `<problem>` texts are this spec's choice.
27. **`holds --wait --bell`** (owner's decision, section 16, decision 11). The owner's wording was to ring for each new hold "then keep waiting as before"; `--wait` ends at the first open hold of a running session, so the bell is written just before that listing, one per such hold found at that read (section 8).

### Stage 7 (Oct 5, 2026)

Decided while building stage 7, recorded in docs/STAGE7-NOTES.md, and folded into the sections above. None changes the ledger format, a status or an exit code.
1. **Progress values always increase** (section 6). MCP asks that each progress value be higher than the last; whole seconds waited do that at the 10 s interval, but not at the 0.05 s interval `test_progress_during_a_hold` injects, where it asks for increasing progress. A value is the whole seconds waited, or the one before plus one if that is higher.
2. **Shutdown after an allow, before forwarding** (sections 5 and 6) has its own `call.refused` reason, `polarizer shut down before the call was forwarded`, as the client's cancel has its own.
3. **`hold.abandoned` is a conditional append** (section 7), so two starts at once, or an allow racing a start, never give a hold two endings or a held call two terminal entries.
4. **`read_file` joins the example config** (section 2), found by the reference check.

### The owner's M2a check (Oct 5, 2026)

The owner ran `scripts/hold-check.sh` interactively with Claude Code 2.1.289 (verified-facts.md, M2a check, interactive). Every step behaved as sections 6 and 9 say. It added two facts, folded into the sections above; neither changes the ledger format, a status, an exit code or any behavior of Polarizer.
1. **Claude Code moves a call to the background after about 123 s,** and the agent keeps working while the call is still held (section 9, with why the 300 s default stays, the three points not verified, and an open question for M3; section 14, What it relies on).
2. **A refused agent can take another route.** The model whose call expired named Bash `mv` as one it could have used. Section 14's "What it does not do" now opens with the routing limit and says so.
