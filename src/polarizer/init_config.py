"""polarizer init (docs/PROXY-SPEC.md, Starting from an existing MCP config).

Reads an MCP config in Claude Code's shape ({"mcpServers": {name: {command, args, env, type}}})
and builds a polarizer.toml with those servers as upstreams, and the MCP config that starts
`polarizer serve` with it. It only reads the source file. It writes to stdout, or with --write
creates two new files in one directory, never over an existing file, and never writes any
Claude Code or MCP config scope. A server it can't express is listed with a reason; a value that
looks like a secret becomes a "${NAME}" placeholder, which serve reads from its environment.
"""

import json
import os
import re
import shutil
import sys
from pathlib import Path

from polarizer.config import PREFIX, WHOLE_VAR
from polarizer.text import safe

SERVER_KEYS = ("type", "command", "args", "env")
IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
SECRET_WORDS = {
    "TOKEN", "SECRET", "PASSWORD", "PASSWD", "PASS", "KEY", "APIKEY", "AUTH", "CREDENTIAL",
    "CREDENTIALS", "PAT", "PRIVATE", "COOKIE", "SESSION",
}  # fmt: skip
SECRET_ENDINGS = ("TOKEN", "SECRET", "PASSWORD", "KEY", "AUTH", "CREDENTIAL", "CREDENTIALS")
TOKEN_STARTS = ("ghp_", "gho_", "ghs_", "github_pat_", "sk-", "xox", "glpat-", "AKIA", "AIza")


class InitError(Exception):
    """One line for stderr; exit 2."""


def looks_secret(key: str, value: str) -> bool:
    """docs/PROXY-SPEC.md, Secrets are not copied. Errs toward yes."""
    words = [w for w in re.split(r"[^A-Za-z0-9]+", key.upper()) if w]
    if any(w in SECRET_WORDS or w.endswith(SECRET_ENDINGS) for w in words):
        return True
    if value.startswith(TOKEN_STARTS):
        return True
    return (
        len(value) >= 24
        and not re.search(r"[\s/:]", value)
        and re.search(r"[A-Za-z]", value) is not None
        and re.search(r"[0-9]", value) is not None
    )


def toml_str(text: str) -> str:
    """A TOML basic string. json.dumps escapes quotes, backslashes and controls the way TOML
    reads them; DEL, which TOML also forbids raw, is escaped by hand."""
    return json.dumps(text, ensure_ascii=False).replace(chr(0x7F), chr(0x5C) + "u007f")


def _server(name: str, entry) -> tuple[dict | None, str | None, list[str]]:
    """(upstream table, None, notes) or (None, the skip reason, [])."""
    if not isinstance(entry, dict):
        return None, "not a JSON object", []
    kind = entry.get("type", "stdio")
    if kind != "stdio":
        return None, f'type "{kind}"; Polarizer proxies only stdio servers', []
    command = entry.get("command")
    if not isinstance(command, str) or not command:
        return None, 'no "command"', []
    if name == "polarizer" or Path(command.replace("\\", "/")).stem == "polarizer":
        return None, "this is Polarizer itself", []
    if not PREFIX.fullmatch(name):
        return None, "the name is not a valid prefix", []
    for key in entry:
        if key not in SERVER_KEYS:
            return None, f'key "{key}" has no polarizer.toml equivalent', []
    args = entry.get("args", [])
    if not isinstance(args, list) or not all(isinstance(a, str) for a in args):
        return None, '"args" must be a list of strings', []
    env = entry.get("env", {})
    if not isinstance(env, dict) or not all(isinstance(v, str) for v in env.values()):
        return None, '"env" must be an object of strings', []
    if any("${" in text for text in [command, *args]):
        return None, '"${...}" in command or args is not expanded by Polarizer', []
    out_env, notes = {}, []
    for key, value in env.items():
        if WHOLE_VAR.fullmatch(value):
            out_env[key] = value
        elif "${" in value:
            return None, f'env {key}: only a whole "${{NAME}}" value is expanded', []
        elif looks_secret(key, value):
            if not IDENTIFIER.fullmatch(key):
                reason = (
                    f'env {key}: looks like a secret, and its name can\'t be a "${{NAME}}" variable'
                )
                return None, reason, []
            out_env[key] = "${" + key + "}"
            notes.append(
                f'{name}: env {key} looked like a secret; polarizer.toml reads it as "${{{key}}}" '
                "from serve's environment"
            )
        else:
            out_env[key] = value
    return {"command": command, "args": args, "env": out_env}, None, notes


def build(path: Path, directory: Path) -> tuple[str, str, list[str]]:
    """(polarizer.toml text, mcp.json text, stderr lines). Raises InitError."""
    try:
        raw = path.read_bytes()
    except OSError as e:
        raise InitError(f"polarizer: cannot read {path}: {e.strerror or e}") from None
    try:
        data = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError) as e:
        raise InitError(f"polarizer: {path} is not valid JSON: {safe(e)}") from None
    servers = data.get("mcpServers") if isinstance(data, dict) else None
    if not isinstance(servers, dict):
        raise InitError(f'polarizer: {path} has no "mcpServers" object')

    tables, lines, comments = [], [], []
    for name, entry in servers.items():
        table, reason, notes = _server(name, entry)
        if table is None:
            line = f"skipped {safe(name)}: {safe(reason)}"
            lines.append(f"polarizer: {line}")
            comments.append(f"# {line}")
            continue
        tables.append((name, table))
        lines += [f"polarizer: {safe(n)}" for n in notes]
        comments += [f"# {safe(n)}" for n in notes]
    if not tables:
        raise InitError(
            "\n".join([*lines, f"polarizer: no server in {path} can go behind Polarizer"])
        )

    toml = [
        f"# Written by polarizer init from {safe(path, 1000)}.",
        "# No tool has a class yet, so every call is held until you give each tool a class",
        "# (docs/HOLD-SPEC.md, section 2, in the Polarizer repository).",
        *comments,
    ]
    for name, table in tables:
        toml += ["", f"[upstream.{name}]", f"command = {toml_str(table['command'])}"]
        if table["args"]:
            toml.append(f"args = [{', '.join(toml_str(a) for a in table['args'])}]")
        if table["env"]:
            pairs = ", ".join(f"{toml_str(k)} = {toml_str(v)}" for k, v in table["env"].items())
            toml.append(f"env = {{ {pairs} }}")
    toml_path = directory / "polarizer.toml"
    found = shutil.which("polarizer")
    command = os.path.abspath(found) if found else "polarizer"
    if not found:
        lines.append('polarizer: set "command" in mcp.json to what `command -v polarizer` prints')
    server = {"command": command, "args": ["serve", "--config", str(toml_path)]}
    mcp = json.dumps({"mcpServers": {"polarizer": server}}, indent=2) + "\n"
    return "\n".join(toml) + "\n", mcp, lines


def next_steps(directory: Path) -> list[str]:
    toml, mcp = directory / "polarizer.toml", directory / "mcp.json"
    return [
        f'polarizer: next: polarizer serve --config "{toml}" < /dev/null, then pending and approve',
        f'polarizer: then: claude --strict-mcp-config --mcp-config "{mcp}"',
    ]


def _create(path: Path, text: str) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def run(source: str, write: str | None) -> int:
    directory = Path(write) if write else Path.home() / ".config" / "polarizer"
    try:
        toml, mcp, lines = build(Path(source), directory)
    except InitError as e:
        print(str(e), file=sys.stderr)
        return 2
    for line in lines:
        print(line, file=sys.stderr)
    files = [(directory / "polarizer.toml", toml), (directory / "mcp.json", mcp)]
    if write is None:
        for path, text in files:
            sys.stdout.write(f"# {path.name}, to save as {path}\n{text}")
    else:
        for path, _ in files:
            if os.path.lexists(path):
                print(f"polarizer: {path} exists; init never overwrites a file", file=sys.stderr)
                return 2
        try:
            directory.mkdir(parents=True, exist_ok=True)
            for path, text in files:
                _create(path, text)
        except OSError as e:
            print(f"polarizer: cannot write {e.filename}: {e.strerror or e}", file=sys.stderr)
            return 2
        print(f"polarizer: wrote {files[0][0]} and {files[1][0]}", file=sys.stderr)
    for line in next_steps(directory):
        print(line, file=sys.stderr)
    return 0
