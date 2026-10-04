"""The policy in polarizer.toml (docs/HOLD-SPEC.md, section 2): its keys and error lines, the
policy hash, policy.loaded, --no-holds, and workspace roots that serve alone checks."""

import json
import os
import subprocess
import sys
from pathlib import Path

import anyio
import pytest
from helpers import rig
from helpers.fakes import FakeUpstream

from polarizer import cli, config, policy
from polarizer.text import safe
from polarizer.writer import FileOps

# The policy hash of FIXED, computed once with a throwaway script in the session scratch
# directory, outside the repo, from a hand-written form and the standard library's json
# (sort_keys, compact separators, ensure_ascii=False, which equals RFC 8785 on the ledger's
# subset), never from polarizer.policy (docs/STAGE6-NOTES.md).
FIXED_SHA = "bcd888f46f6866c375af7fc238eda1573eb0628ef3663b57e45bfab29e397006"
FIXED = """
[policy]
hold_timeout_seconds = 120
write_hold_patterns = ["deploy/**", "~/.config/systemd/**"]
read_hold_patterns = ["~/.kube/**"]

[upstream.fs]
command = "x"

[upstream.fs.tools]
read_text_file = { class = "local-read", path_args = ["path"] }
write_file = { class = "local-write", path_args = ["path"] }
move_file = { class = "destructive", path_args = ["source", "destination"] }

[upstream.web]
command = "y"
trust_annotations = true
"""


def write(tmp_path: Path, text: str, name: str = "polarizer.toml") -> Path:
    path = tmp_path / name
    path.write_text(f'ledger_dir = "{(tmp_path / "ledger").as_posix()}"\n{text}', encoding="utf-8")
    return path


def probe_table(log: Path) -> str:
    return (
        f"[upstream.fs]\ncommand = {rig.toml_str(sys.executable)}\n"
        f"args = [{rig.toml_str(rig.PROBE)}]\nenv = {{ PROBE_LOG = {rig.toml_str(log)} }}\n"
    )


def _errors(tmp_path, upstream: str):
    """(toml after ledger_dir, the error line after the file's name) for every error line."""
    gone = tmp_path / "gone"
    many = json.dumps([f"/r{i}" for i in range(33)])
    tools = upstream + "[upstream.fs.tools]\n"
    cases = [
        ("policy = 1\n" + upstream, '"policy" must be a table'),
        ("[policy]\nroots = []\n" + upstream, '[policy] unknown key "roots"'),
        (
            '[policy]\nworkspace_roots = ["rel/dir"]\n' + upstream,
            "[policy] workspace_roots must be a list of absolute paths",
        ),
        (
            "[policy]\nworkspace_roots = [1]\n" + upstream,
            "[policy] workspace_roots must be a list of absolute paths",
        ),
        (
            f"[policy]\nworkspace_roots = {many}\n" + upstream,
            "[policy] workspace_roots has more than 32 entries",
        ),
        (
            f"[policy]\nworkspace_roots = [{rig.toml_str(gone.as_posix())}]\n" + upstream,
            f"[policy] workspace root {gone} does not exist",
        ),
    ]
    for bad in ("0", "1201", "1.5", "true", '"5"'):
        cases.append(
            (
                f"[policy]\nhold_timeout_seconds = {bad}\n" + upstream,
                "[policy] hold_timeout_seconds must be an integer from 1 to 1200",
            )
        )
    for key in ("write_hold_patterns", "read_hold_patterns"):
        for bad in ('"x"', json.dumps(["p"] * 65), "[1]"):
            cases.append(
                (
                    f"[policy]\n{key} = {bad}\n" + upstream,
                    f"[policy] {key} must be a list of at most 64 patterns",
                )
            )
    for pattern, reason in [
        ("a/**b", '"**" must be a whole part'),
        ("a//b", "an empty part"),
        ("a/", "an empty part"),
        ("a/../b", '"." or ".." as a part'),
        ("./a", '"." or ".." as a part'),
        ("~x/y", '"~" only as the first part, followed by "/"'),
        ("a/~/b", '"~" only as the first part, followed by "/"'),
        ("~", '"~" only as the first part, followed by "/"'),
        ("a\\b", 'a backslash; use "/"'),
        ("a" * 1025, "longer than 1024 characters"),
        ("", "empty"),
    ]:
        cases.append(
            (
                f"[policy]\nwrite_hold_patterns = [{json.dumps(pattern)}]\n" + upstream,
                f'[policy] pattern "{safe(pattern)}" is not valid: {reason}',
            )
        )
    cases += [
        (
            upstream + "trust_annotations = 1\n",
            '[upstream.fs] "trust_annotations" must be true or false',
        ),
        (
            upstream + "tools = 1\n",
            '[upstream.fs] "tools" must be a table of [upstream.fs.tools] entries',
        ),
        (
            tools + '"write file" = { class = "local-write" }\n',
            '[upstream.fs.tools] "write file" is not a tool name Polarizer can expose',
        ),
        (
            tools + 'write_file = "local-write"\n',
            '[upstream.fs.tools] "write_file" must be a table',
        ),
        (
            tools + 'write_file = { class = "local-write", paths = [] }\n',
            '[upstream.fs.tools] "write_file" unknown key "paths"',
        ),
        (
            tools + 'write_file = { path_args = ["path"] }\n',
            '[upstream.fs.tools] "write_file" is missing "class"',
        ),
        (
            tools + 'write_file = { class = "write" }\n',
            '[upstream.fs.tools] "write_file" class must be one of '
            "local-read, local-write, destructive, open-world, egress",
        ),
        (
            tools + 'write_file = { class = "local-write", path_args = "path" }\n',
            '[upstream.fs.tools] "write_file" path_args must be a list of at most 16 argument names',
        ),
        (
            tools
            + f'write_file = {{ class = "local-write", path_args = {json.dumps(["a"] * 17)} }}\n',
            '[upstream.fs.tools] "write_file" path_args must be a list of at most 16 argument names',
        ),
        (
            tools + 'write_file = { class = "local-write", path_args = [""] }\n',
            '[upstream.fs.tools] "write_file" path_args must be a list of at most 16 argument names',
        ),
        ("[policy]\nholds = false\n" + upstream, '[policy] unknown key "holds"'),
        ("holds = false\n" + upstream, 'unknown top-level key "holds"'),
    ]
    return cases


def test_policy_errors(tmp_path, capsys, fake_home):
    """Each error line in section 2, exactly, exit 2, and serve starts no upstream."""
    log = tmp_path / "probe.log"
    for i, (text, line) in enumerate(_errors(tmp_path, probe_table(log))):
        path = write(tmp_path, text, f"polarizer{i}.toml")
        code = cli.main(["serve", "--config", str(path)])
        out, err = capsys.readouterr()
        assert (code, out, err) == (2, "", f"polarizer{i}.toml: {line}\n"), text
    assert not log.exists()


def test_error_text_from_the_file_is_safe(tmp_path, capsys):
    """A key, a tool name and a pattern holding ESC and a newline print escaped."""
    esc = chr(0x5C) + "u001b"  # TOML's escape for ESC: a raw ESC isn't allowed in TOML
    for text, line in [
        (
            f'[policy]\n"x{esc}[2J" = 1\n[upstream.fs]\ncommand = "x"\n',
            '[policy] unknown key "x\\x1b[2J"',
        ),
        (
            f'[policy]\nwrite_hold_patterns = ["a/**b{esc}"]\n[upstream.fs]\ncommand = "x"\n',
            '[policy] pattern "a/**b\\x1b" is not valid: "**" must be a whole part',
        ),
        (
            '[upstream.fs]\ncommand = "x"\n[upstream.fs.tools]\n"a\\nb" = { class = "egress" }\n',
            '[upstream.fs.tools] "a b" is not a tool name Polarizer can expose',
        ),
    ]:
        path = write(tmp_path, text)
        with pytest.raises(config.ConfigError) as caught:
            config.load(path)
        assert str(caught.value) == f"polarizer.toml: {line}"


def test_no_policy_table_holds_everything(tmp_path, capsys):
    """No [policy] and no tools: the config loads, every tool is unclassified, the one stderr
    line after startup's listing counts them, and a call is held as unclassified."""
    cfg = config.load(write(tmp_path, '[upstream.f]\ncommand = "x"\n'))
    assert cfg.policy == config.PolicyConfig()
    built = policy.build(cfg)
    assert built.classified == 0 and built.workspace_roots == ()
    fake = FakeUpstream()

    async def main():
        specs = [rig.spec("f", fake.server)]
        async with rig.proxied(tmp_path / "ledger", specs, policy=built, hold_timeout=0.2) as (
            client,
            _,
        ):
            capsys.readouterr()
            result = await client.call_tool("f__echo", {"x": 1})
            assert result.content[0].text == "polarizer: f__echo was not allowed"

    capsys.readouterr()
    anyio.run(main)
    created = rig.kinds(tmp_path / "ledger", "hold.created")
    assert [c["data"]["rule"] for c in created] == ["unclassified"]
    assert created[0]["data"]["reason"] == "f__echo has no class in polarizer.toml"
    assert fake.calls == []


def test_unclassified_line_once_after_listing(tmp_path, capsys):
    fake = FakeUpstream()
    specs = [rig.spec("f", fake.server)]
    partial = policy.Policy(
        upstreams={"f": policy.UpstreamPolicy(False, {"echo": config.ToolRule("local-read")})}
    )

    async def main():
        async with rig.gateway(tmp_path / "ledger", specs, approve=False, policy=partial):
            pass

    anyio.run(main)
    err = capsys.readouterr().err
    n = len(fake.names)
    line = (
        f"polarizer: {n - 1} of {n} listed tools have no class in polarizer.toml; "
        "every call to them is held\n"
    )
    assert err.count(line) == 1
    # Nothing is said when every tool has a class, or with --no-holds.
    for quiet in (rig.classified(specs), policy.Policy(holds=False)):

        async def again(p=quiet):
            async with rig.gateway(tmp_path / "ledger", specs, approve=False, policy=p):
                pass

        anyio.run(again)
        assert "listed tools have no class" not in capsys.readouterr().err


class _Events(FileOps):
    """Records each line written (by kind) and each fsync, in order."""

    def __init__(self, events):
        self.events = events

    def write(self, fd, data):
        if data.startswith(b'{"data"'):
            self.events.append(("write", json.loads(data)["kind"]))
        super().write(fd, data)

    def fsync(self, fd):
        self.events.append(("fsync",))
        super().fsync(fd)


def test_no_holds_is_a_flag_only(tmp_path, monkeypatch, fake_home):
    """--no-holds records policy.loaded with holds "off", fsynced before any upstream connects,
    and every call runs; the config has no key for it (test_policy_errors)."""
    from polarizer import upstream as upstream_mod

    events = []
    real_run = upstream_mod.Upstream.run

    async def run(self, *args, **kwargs):
        events.append(("connect", self.prefix))
        return await real_run(self, *args, **kwargs)

    monkeypatch.setattr(upstream_mod.Upstream, "run", run)
    fake = FakeUpstream()
    off = policy.Policy(holds=False)

    async def main():
        specs = [rig.spec("f", fake.server)]
        async with rig.proxied(tmp_path / "ledger", specs, policy=off, ops=_Events(events)) as (
            client,
            _,
        ):
            result = await client.call_tool("f__echo", {"x": 1})
            assert result.is_error is False

    anyio.run(main)
    loaded = events.index(("write", "policy.loaded"))
    fsynced = events.index(("fsync",), loaded)
    assert fsynced < events.index(("connect", "f"))
    entries = rig.entries(tmp_path / "ledger")
    kinds = [e["kind"] for e in entries]
    assert kinds[kinds.index("session.started") + 1] == "policy.loaded"
    assert entries[kinds.index("policy.loaded")]["data"]["holds"] == "off"
    sent = rig.kinds(tmp_path / "ledger", "call.sent")
    assert [s["data"]["allowed_by"] for s in sent] == ["holds-off"]
    head = json.loads((tmp_path / "ledger" / "ledger.head").read_text())
    assert head["seq"] >= kinds.index("policy.loaded")


def test_no_holds_from_the_command_line(tmp_path):
    """`polarizer serve --no-holds` writes the warning and records holds "off"; any other
    command given it is a usage error."""
    cfg = rig.serve_config(tmp_path, probe_table(tmp_path / "probe.log"), tmp_path / "ledger")
    done = subprocess.run(
        [sys.executable, "-m", "polarizer", "serve", "--config", str(cfg), "--no-holds"],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        timeout=120,
    )
    assert done.returncode == 0, done.stderr
    assert b"polarizer: warning: started with --no-holds; no call is held\n" in done.stderr
    loaded = rig.kinds(tmp_path / "ledger", "policy.loaded")
    assert [e["data"]["holds"] for e in loaded] == ["off"]


def test_policy_loaded_fields(tmp_path, fake_home):
    """policy.loaded follows session.started, with the fixed config's policy hash, the count
    of configured entries and the timeout; and, for a config with roots, the resolved roots."""
    built = policy.build(config.load(write(tmp_path, FIXED)))
    assert built.sha256 == FIXED_SHA
    assert built.classified == 3 and built.hold_timeout_seconds == 120

    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    try:
        os.symlink(real, link)
        root = link
    except OSError:
        root = real  # Windows without the symlink privilege: the root as it is
    text = FIXED.replace(
        "[policy]\n", f"[policy]\nworkspace_roots = [{rig.toml_str(root.as_posix())}]\n"
    )
    with_roots = policy.build(config.load(write(tmp_path, text, "roots.toml")))
    assert with_roots.workspace_roots == (os.path.realpath(real),)

    async def main():
        async with rig.gateway(tmp_path / "ledger", [], approve=False, policy=with_roots):
            pass

    anyio.run(main)
    entries = rig.entries(tmp_path / "ledger")
    kinds = [e["kind"] for e in entries]
    started = kinds.index("session.started")
    assert kinds[started + 1] == "policy.loaded"
    data = entries[started + 1]["data"]
    assert data == {
        "session": entries[started]["data"]["session"],
        "holds": "on",
        "policy_sha256": with_roots.sha256,
        "classified": 3,
        "workspace_roots": [os.path.realpath(real)],
        "hold_timeout_seconds": 120,
    }
    assert with_roots.sha256 != FIXED_SHA


def test_roots_must_exist_for_serve_only(tmp_path, capsys, fake_home):
    """A missing root stops serve with its line; holds, pending and verify with --config read
    the same file without checking."""
    gone = tmp_path / "gone"
    text = f"[policy]\nworkspace_roots = [{rig.toml_str(gone.as_posix())}]\n" + probe_table(
        tmp_path / "probe.log"
    )
    path = write(tmp_path, text)
    assert cli.main(["serve", "--config", str(path)]) == 2
    assert (
        capsys.readouterr().err
        == f"polarizer.toml: [policy] workspace root {gone} does not exist\n"
    )
    assert not (tmp_path / "probe.log").exists()
    for command in ("holds", "pending", "verify"):
        assert cli.main([command, "--config", str(path)]) == 2
        out, err = capsys.readouterr()
        assert (out, err) == (f"no ledger at {tmp_path / 'ledger'}\n", "")
