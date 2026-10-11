"""polarizer init (docs/PROXY-SPEC.md, Starting from an existing MCP config): an MCP config in
Claude Code's shape becomes a polarizer.toml with those servers as upstreams, and the MCP config
that starts serve. It only reads the source file, writes only where --write says and never over
a file, lists every server it can't express, and never copies a secret-looking value."""

import json
import re
import tomllib
from pathlib import Path

import pytest

from polarizer import cli, config

SECRET = "ghp_0123456789abcdefABCDEF0123456789abcd"
LONG_RANDOM = "q8Zr2Lm4Xv7Kp1Ns9Td3Wf6Hb5Jc0Ga"

SERVERS = {
    "fs": {
        "type": "stdio",
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-filesystem@2026.8.31", "/home/you/demo"],
    },
    "notes": {
        "command": "/opt/notes/bin/notes-mcp",
        "args": ["--stdio"],
        "env": {
            "NOTES_MODE": "read-only",
            "NOTES_HOME": "/home/you/notes",
            "GITHUB_TOKEN": SECRET,
            "ApiKey": "plain-looking",
            "SESSION_ID": "x",
            "BUILD": LONG_RANDOM,
            "FROM_SHELL": "${FROM_SHELL}",
        },
    },
}

SKIPPED = {
    "remote": ({"type": "http", "url": "https://example.com/mcp"}, 'type "http"'),
    "events": ({"type": "sse", "url": "https://example.com/sse"}, 'type "sse"'),
    "urlonly": ({"url": "https://example.com/mcp"}, 'no "command"'),
    "polarizer": ({"command": "polarizer", "args": ["serve"]}, "this is Polarizer itself"),
    "again": ({"command": "/home/you/.local/bin/polarizer"}, "this is Polarizer itself"),
    "bad__name": ({"command": "x"}, "the name is not a valid prefix"),
    "withcwd": ({"command": "x", "cwd": "/tmp"}, 'key "cwd" has no polarizer.toml equivalent'),
    "badargs": ({"command": "x", "args": "--stdio"}, '"args" must be a list of strings'),
    "badenv": ({"command": "x", "env": {"A": 1}}, '"env" must be an object of strings'),
    "expands": ({"command": "x", "args": ["${HOME}/srv"]}, "in command or args is not expanded"),
    "partial": (
        {"command": "x", "env": {"URL": "https://${HOST}/api"}},
        'env URL: only a whole "${NAME}" value is expanded',
    ),
    "secretname": (
        {"command": "x", "env": {"MY-TOKEN": SECRET}},
        "env MY-TOKEN: looks like a secret, and its name can't be",
    ),
    "notobject": ("npx thing", "not a JSON object"),
}


def source(tmp_path: Path, servers: dict, **extra) -> Path:
    path = tmp_path / "claude-mcp.json"
    path.write_text(json.dumps({"mcpServers": servers, **extra}, indent=2), encoding="utf-8")
    return path


def run(capsys, *argv):
    code = cli.main(["init", *argv])
    out, err = capsys.readouterr()
    return code, out, err


def split(out: str, home: Path) -> tuple[str, dict]:
    """stdout's two files, each after its "to save as" line."""
    where = home / ".config" / "polarizer"
    toml_head = f"# polarizer.toml, to save as {where / 'polarizer.toml'}\n"
    json_head = f"# mcp.json, to save as {where / 'mcp.json'}\n"
    assert out.startswith(toml_head), out[:200]
    toml_text, _, json_text = out.removeprefix(toml_head).partition(json_head)
    assert json_text, out
    return toml_text, json.loads(json_text)


def test_init_prints_upstreams_and_the_mcp_config(tmp_path, fake_home, capsys):
    src = source(tmp_path, SERVERS, projects={"ignored": True})
    code, out, err = run(capsys, "--from", str(src))
    assert code == 0, err
    toml_text, mcp = split(out, fake_home)
    saved = tmp_path / "polarizer.toml"
    saved.write_text(toml_text, encoding="utf-8")
    cfg = config.load(saved, require_env=False)
    by = {u.prefix: u for u in cfg.upstreams}
    assert list(by) == ["fs", "notes"]
    assert by["fs"].command == "npx" and list(by["fs"].args) == SERVERS["fs"]["args"]
    assert by["notes"].env["NOTES_MODE"] == "read-only"
    assert by["notes"].env["NOTES_HOME"] == "/home/you/notes"
    assert by["notes"].env["FROM_SHELL"] == "${FROM_SHELL}"
    assert cfg.policy.workspace_roots == () and all(not u.tools for u in cfg.upstreams)
    assert "every call is held" in toml_text
    [(name, entry)] = mcp["mcpServers"].items()
    assert name == "polarizer"
    assert Path(entry["command"]).stem == "polarizer"
    config_path = fake_home / ".config" / "polarizer" / "polarizer.toml"
    assert entry["args"] == ["serve", "--config", str(config_path)]
    assert "--strict-mcp-config --mcp-config" in err
    assert not (fake_home / ".config").exists()  # stdout only: nothing written


def test_init_secret_values_become_placeholders(tmp_path, fake_home, capsys):
    src = source(tmp_path, SERVERS)
    code, out, err = run(capsys, "--from", str(src))
    assert code == 0, err
    assert SECRET not in out + err and LONG_RANDOM not in out + err
    toml_text, _ = split(out, fake_home)
    env = tomllib.loads(toml_text)["upstream"]["notes"]["env"]
    for key in ("GITHUB_TOKEN", "ApiKey", "SESSION_ID", "BUILD"):
        assert env[key] == "${" + key + "}", key
        line = f'polarizer: notes: env {key} looked like a secret; polarizer.toml reads it as "${{{key}}}"'
        assert line in err, key
        assert f"env {key} looked like a secret" in toml_text, key
    assert "FROM_SHELL looked like" not in err  # a whole ${NAME} is copied as it is


def test_init_lists_every_server_it_skips(tmp_path, fake_home, capsys):
    servers = {**SERVERS, **{name: entry for name, (entry, _) in SKIPPED.items()}}
    src = source(tmp_path, servers)
    code, out, err = run(capsys, "--from", str(src))
    assert code == 0, err
    toml_text, _ = split(out, fake_home)
    upstreams = tomllib.loads(toml_text)["upstream"]
    for name, (_, reason) in SKIPPED.items():
        assert name not in upstreams, name
        [line] = [x for x in err.splitlines() if x.startswith(f"polarizer: skipped {name}: ")]
        assert reason in line, line
        assert f"# skipped {name}: " in toml_text, name
    assert set(upstreams) | set(SKIPPED) == set(servers)  # no server missing from both
    assert SECRET not in out + err


def test_init_skip_lines_are_made_safe(tmp_path, fake_home, capsys):
    name = "line\nbreak\x1b[2J"
    src = source(tmp_path, {**SERVERS, name: {"command": "x"}})
    code, out, err = run(capsys, "--from", str(src))
    assert code == 0, err
    assert "\x1b" not in out + err
    assert "polarizer: skipped line break\\x1b[2J: the name is not a valid prefix" in err
    toml_text, _ = split(out, fake_home)
    tomllib.loads(toml_text)


def test_init_write_creates_both_files(tmp_path, fake_home, capsys):
    src = source(tmp_path, SERVERS)
    before = src.read_bytes()
    target = tmp_path / "out" / "polarizer"
    code, out, err = run(capsys, "--from", str(src), "--write", str(target))
    assert code == 0, err
    assert out == ""
    assert src.read_bytes() == before
    cfg = config.load(target / "polarizer.toml", require_env=False)
    assert [u.prefix for u in cfg.upstreams] == ["fs", "notes"]
    mcp = json.loads((target / "mcp.json").read_text(encoding="utf-8"))
    args = mcp["mcpServers"]["polarizer"]["args"]
    assert args == ["serve", "--config", str(target / "polarizer.toml")]
    assert str(target / "mcp.json") in err
    assert sorted(p.name for p in target.iterdir()) == ["mcp.json", "polarizer.toml"]


@pytest.mark.parametrize("existing", ["polarizer.toml", "mcp.json"])
def test_init_write_never_overwrites(tmp_path, fake_home, capsys, existing):
    src = source(tmp_path, SERVERS)
    target = tmp_path / "out"
    target.mkdir()
    (target / existing).write_text("mine\n", encoding="utf-8")
    code, out, err = run(capsys, "--from", str(src), "--write", str(target))
    assert code == 2
    assert err.splitlines()[-1] == (
        f"polarizer: {target / existing} exists; init never overwrites a file"
    )
    assert [p.name for p in target.iterdir()] == [existing]
    assert (target / existing).read_text(encoding="utf-8") == "mine\n"


def test_init_errors(tmp_path, fake_home, capsys):
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    shape = tmp_path / "shape.json"
    shape.write_text('{"servers": {}}', encoding="utf-8")
    remote = source(tmp_path, {"remote": SKIPPED["remote"][0]})
    missing = tmp_path / "missing.json"
    cases = [
        ([], "polarizer: init needs --from <path to an MCP config JSON>"),
        (["--from", str(remote), "--write", "rel/dir"],
         "polarizer: --write must be an absolute path, got rel/dir"),
        (["--from", str(missing)], f"polarizer: cannot read {missing}: "),
        (["--from", str(bad)], f"polarizer: {bad} is not valid JSON: "),
        (["--from", str(shape)], f'polarizer: {shape} has no "mcpServers" object'),
        (["--from", str(remote)], f"polarizer: no server in {remote} can go behind Polarizer"),
        (["--from", str(remote), "--config", "/x"], "polarizer: unrecognized arguments: --config /x"),
    ]  # fmt: skip
    for argv, line in cases:
        code, out, err = run(capsys, *argv)
        assert code == 2, argv
        assert out == "", argv
        assert err.splitlines()[-1].startswith(line), (argv, err)
    target = tmp_path / "never"
    code, _, _ = run(capsys, "--from", str(remote), "--write", str(target))
    assert code == 2 and not target.exists()


def test_init_is_in_the_help(capsys):
    with pytest.raises(SystemExit):
        cli.main(["--help"])
    out = capsys.readouterr().out
    assert re.search(r"^ +init +\S", out, re.M), out
