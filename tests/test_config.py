"""polarizer.toml: parse and validate only, with each error's exact one line."""

import json
import sys
import textwrap
from pathlib import Path

import anyio
import pytest

from polarizer import cli
from polarizer.config import ConfigError, load

REPO = Path(__file__).resolve().parent.parent

GOOD = """
ledger_dir = "~/ledgers/polarizer"
ledger_forbidden_paths = ["~/code/parallax", "{extra}"]

[upstream.probe]
command = "/usr/bin/python3"
args = ["probe.py"]
connect_timeout_seconds = 5

[upstream.notes]
command = "notes-mcp"
env = { NOTES_TOKEN = "${NOTES_TOKEN}", NOTES_MODE = "read-only" }
"""


def write(tmp_path, text, name="polarizer.toml"):
    path = tmp_path / name
    path.write_text(textwrap.dedent(text), encoding="utf-8")
    return path


def good(tmp_path):
    return write(tmp_path, GOOD.replace("{extra}", (tmp_path / "srv").as_posix()))


def test_a_good_config(tmp_path, fake_home):
    cfg = load(good(tmp_path), environ={"NOTES_TOKEN": "secret"})
    assert cfg.ledger_dir == fake_home / "ledgers" / "polarizer"
    assert cfg.ledger_forbidden_paths == (
        fake_home / ".local/share/parallax",
        fake_home / ".config/parallax",
        fake_home / "code/parallax",
        tmp_path / "srv",
    )
    probe, notes = cfg.upstreams
    assert (probe.prefix, probe.command, probe.args, probe.connect_timeout_seconds) == (
        "probe", "/usr/bin/python3", ("probe.py",), 5.0
    )  # fmt: skip
    assert notes.env == {"NOTES_TOKEN": "secret", "NOTES_MODE": "read-only"}
    assert notes.connect_timeout_seconds == 10.0
    assert len(cfg.sha256) == 64


def test_defaults(tmp_path, fake_home):
    cfg = load(write(tmp_path, '[upstream.a]\ncommand = "x"\n'))
    assert cfg.ledger_dir == fake_home / ".local/share/polarizer"
    assert cfg.ledger_forbidden_paths == (
        fake_home / ".local/share/parallax",
        fake_home / ".config/parallax",
    )


MANUAL_TOML = REPO / "manual" / "polarizer.manual.toml"
EXAMPLE_TOML = REPO / "polarizer.example.toml"


def manual(tmp_path, fake_home) -> str:
    """manual/polarizer.manual.toml filled in as docs/dev/MANUAL-CHECK.md step 2 does, with its
    POSIX /tmp/polarizer-manual replaced by a directory under tmp_path. Its paths are POSIX
    only; on Windows "/tmp/..." has no drive and is rightly not absolute, so every platform
    gets a real absolute path here (README.md says to edit them by hand)."""
    text = MANUAL_TOML.read_text(encoding="utf-8")
    assert '"/tmp/polarizer-manual"' in text
    text = text.replace("/tmp/polarizer-manual", (tmp_path / "manual").as_posix())
    return text.replace("/home/<you>", fake_home.as_posix())


def test_manual_config_uses_the_manual_ledger(tmp_path, fake_home):
    """manual/polarizer.manual.toml, filled in as docs/dev/MANUAL-CHECK.md step 2 does, parses,
    and its ledger is the manual check's own directory, not the default."""
    cfg = load(write(tmp_path, manual(tmp_path, fake_home)))
    assert cfg.ledger_dir == fake_home / ".local/share/polarizer-manual"
    assert [u.prefix for u in cfg.upstreams] == ["probe", "fs"]


def test_manual_config_policy(tmp_path, fake_home):
    """The manual-check config's policy (docs/HOLD-SPEC.md, section 11): the probe's seven tools local-read,
    the Filesystem tools classified, /tmp/polarizer-manual the root (here a directory under
    tmp_path). So the manual check holds fs__write_file outside the directory and fs__move_file
    always, and nothing of the probe."""
    import os

    from polarizer import policy

    cfg = load(write(tmp_path, manual(tmp_path, fake_home)))
    assert cfg.policy.workspace_roots == (tmp_path / "manual",)
    assert cfg.policy.hold_timeout_seconds == 300
    probe, fs = cfg.upstreams
    assert sorted(probe.tools) == sorted(
        ["wait", "crash", "env", "fail", "rich", "invalid", "change"]
    )
    assert {rule.cls for rule in probe.tools.values()} == {"local-read"}
    assert len(fs.tools) == 14 and fs.tools["move_file"].cls == "destructive"
    root = tmp_path / "manual"
    root.mkdir()
    built = policy.Policy(
        workspace_roots=(os.path.realpath(root),),
        upstreams={
            u.prefix: policy.UpstreamPolicy(u.trust_annotations, u.tools) for u in cfg.upstreams
        },
    )
    inside, outside = str(root / "notes.txt"), str(tmp_path / "elsewhere.txt")
    verdicts = {
        "probe wait": policy.evaluate(built, "probe", "wait", {"seconds": 1}, {}),
        "write inside": policy.evaluate(built, "fs", "write_file", {"path": inside}, {}),
        "write outside": policy.evaluate(built, "fs", "write_file", {"path": outside}, {}),
        "write hook": policy.evaluate(
            built, "fs", "write_file", {"path": str(root / ".git" / "hooks" / "x")}, {}
        ),
        "move": policy.evaluate(
            built, "fs", "move_file", {"source": inside, "destination": inside}, {}
        ),
        "read": policy.evaluate(built, "fs", "read_text_file", {"path": outside}, {}),
    }
    assert {k: (v.action, v.rule) for k, v in verdicts.items()} == {
        "probe wait": ("allow", "local-read"),
        "write inside": ("allow", "inside-roots"),
        "write outside": ("hold", "outside-roots"),
        "write hook": ("hold", "write-pattern"),
        "move": ("hold", "destructive"),
        "read": ("allow", "local-read"),
    }


def test_manual_config_forbids_the_guarded_repos(tmp_path, fake_home):
    """CLAUDE.md rule 6: the manual check's ledger is refused inside the Parallax, Loupe and ISR
    clones and the backup clone. polarizer.toml is written from this file, so these must stay."""
    cfg = load(write(tmp_path, manual(tmp_path, fake_home)))
    code = fake_home / "code"
    for name in ("parallax", "parallax-backup-before-rewrite", "loupe", "isr"):
        assert code / name in cfg.ledger_forbidden_paths, name


def test_example_config_for_users(tmp_path, fake_home):
    """polarizer.example.toml, with /home/you replaced as its header says, parses: the default
    ledger, nothing of this repository (no probe, no forbidden paths beyond Parallax's own two),
    a workspace root, and the pinned Filesystem server with every tool classified as in the
    manual check's config."""
    import tomllib

    text = EXAMPLE_TOML.read_text(encoding="utf-8")
    assert "/home/<you>" not in text and "/tmp/polarizer-manual" not in text
    root = fake_home / "projects" / "demo"
    root.mkdir(parents=True)
    cfg = load(write(tmp_path, text.replace("/home/you", fake_home.as_posix())))
    assert cfg.ledger_dir == fake_home / ".local/share/polarizer"
    assert cfg.ledger_forbidden_paths == (
        fake_home / ".local/share/parallax",
        fake_home / ".config/parallax",
    )
    assert [u.prefix for u in cfg.upstreams] == ["fs"]
    assert cfg.policy.workspace_roots == (root,)
    example_fs = tomllib.loads(text)["upstream"]["fs"]
    manual_fs = tomllib.loads(MANUAL_TOML.read_text(encoding="utf-8"))["upstream"]["fs"]
    assert example_fs["tools"] == manual_fs["tools"]
    assert example_fs["args"][:2] == manual_fs["args"][:2]


def test_verify_and_repair_do_not_need_upstream_secrets(tmp_path, fake_home):
    cfg = load(good(tmp_path), require_env=False, environ={})
    assert cfg.upstreams[1].env["NOTES_TOKEN"] == "${NOTES_TOKEN}"


@pytest.mark.parametrize(
    "text, message",
    [
        ("x = ]\n", "polarizer.toml: line 1: Invalid value"),
        ('ledger_path = "x"\n', 'polarizer.toml: unknown top-level key "ledger_path"'),
        ("ledger_forbidden_paths = []\n", "polarizer.toml: no upstreams configured"),
        ("ledger_dir = 3\n", "polarizer.toml: ledger_dir must be a string"),
        ('ledger_dir = "data/ledger"\n', "polarizer.toml: ledger_dir must be an absolute path, got data/ledger"),
        ('ledger_forbidden_paths = "/x"\n', "polarizer.toml: ledger_forbidden_paths must be a list of absolute paths"),
        ('ledger_forbidden_paths = ["rel"]\n', "polarizer.toml: ledger_forbidden_paths must be a list of absolute paths"),
        ("upstream = 1\n", 'polarizer.toml: "upstream" must be a table of [upstream.<prefix>] tables'),
        ("[upstream]\n", "polarizer.toml: no upstreams configured"),
        ("[upstream]\nnotes = 1\n", "polarizer.toml: [upstream.notes] must be a table"),
        ('[upstream.my__srv]\ncommand = "x"\n', 'polarizer.toml: upstream prefix "my__srv" must be 1 to 32 characters of letters, digits, "-" and "_", with no "__" and no "_" at either end'),
        ('[upstream._a]\ncommand = "x"\n', 'polarizer.toml: upstream prefix "_a"'),
        ('[upstream.a_]\ncommand = "x"\n', 'polarizer.toml: upstream prefix "a_"'),
        (f'[upstream.{"a" * 33}]\ncommand = "x"\n', 'polarizer.toml: upstream prefix "aaa'),
        ('[upstream.notes]\ncommand = "x"\ntimeout = 3\n', 'polarizer.toml: [upstream.notes] unknown key "timeout"'),
        ("[upstream.notes]\nargs = []\n", 'polarizer.toml: [upstream.notes] is missing "command"'),
        ("[upstream.notes]\ncommand = 1\n", 'polarizer.toml: [upstream.notes] "command" must be a string'),
        ('[upstream.notes]\ncommand = "x"\nargs = "y"\n', 'polarizer.toml: [upstream.notes] "args" must be a list of strings'),
        ('[upstream.notes]\ncommand = "x"\nenv = { A = 1 }\n', 'polarizer.toml: [upstream.notes] "env" must be a table of strings'),
        ('[upstream.notes]\ncommand = "x"\nconnect_timeout_seconds = 21\n', 'polarizer.toml: [upstream.notes] "connect_timeout_seconds" must be a number from 1 to 20'),
        ('[upstream.notes]\ncommand = "x"\nconnect_timeout_seconds = true\n', 'polarizer.toml: [upstream.notes] "connect_timeout_seconds" must be a number from 1 to 20'),
        ('[upstream.notes]\ncommand = "x"\nconnect_timeout_seconds = nan\n', 'polarizer.toml: [upstream.notes] "connect_timeout_seconds" must be a number from 1 to 20'),
        ('[upstream.notes]\ncommand = "x"\nenv = { NOTES_TOKEN = "${NOTES_TOKEN}" }\n', 'polarizer.toml: [upstream.notes] env NOTES_TOKEN: "${NOTES_TOKEN}" is not set in Polarizer\'s environment'),
        ('[upstream.notes]\ncommand = "x"\nenv = { NOTES_URL = "https://${HOST}/x" }\n', 'polarizer.toml: [upstream.notes] env NOTES_URL: only a whole "${NAME}" value is expanded'),
    ],
)  # fmt: skip
def test_errors(tmp_path, fake_home, text, message):
    with pytest.raises(ConfigError) as raised:
        load(write(tmp_path, text), environ={})
    assert str(raised.value).startswith(message)
    assert "\n" not in str(raised.value)


def test_toml_error_carries_its_line(tmp_path):
    with pytest.raises(ConfigError) as raised:
        load(write(tmp_path, 'a = 1\n\n[upstream.x]\ncommand = "x"\nbad = = 2\n'))
    assert str(raised.value).startswith("polarizer.toml: line 5: ")


def test_missing_file(tmp_path):
    with pytest.raises(ConfigError) as raised:
        load(tmp_path / "polarizer.toml")
    assert str(raised.value) == f"polarizer.toml: not found at {tmp_path / 'polarizer.toml'}"


def test_the_files_own_name_starts_each_error(tmp_path):
    with pytest.raises(ConfigError, match=r"^other\.toml: no upstreams configured$"):
        load(write(tmp_path, "", name="other.toml"))


def test_verify_reads_ledger_dir_from_config(tmp_path, fake_home, capsys):
    from conftest import install_fixture

    directory = install_fixture("valid/minimal", tmp_path / "led")
    path = write(tmp_path, f'ledger_dir = "{directory.as_posix()}"\n[upstream.a]\ncommand = "x"\n')
    assert cli.main(["verify", "--config", str(path)]) == 0
    assert capsys.readouterr().out.startswith("intact: 1 entries, 0 sessions, 0 calls\n")


def test_verify_config_error_is_one_line_and_exit_2(tmp_path, capsys):
    path = write(tmp_path, "x = 1\n")
    assert cli.main(["verify", "--config", str(path)]) == 2
    out, err = capsys.readouterr()
    assert (out, err) == ("", 'polarizer.toml: unknown top-level key "x"\n')


def test_serve_config_errors_exit_2_before_any_upstream(tmp_path, fake_home, capsys, monkeypatch):
    monkeypatch.delenv("NOTES_TOKEN", raising=False)
    marker = tmp_path / "started"
    # If any case started the upstream, it would create the marker file.
    script = f'open({json.dumps(str(marker))}, "w")'
    upstream = f"[upstream.a]\ncommand = {json.dumps(sys.executable)}\nargs = ['-c', '{script}']\n"
    cases = [
        ("x = 1\n" + upstream, 'polarizer.toml: unknown top-level key "x"'),
        (
            upstream + 'env = { T = "${NOTES_TOKEN}" }\n',
            'polarizer.toml: [upstream.a] env T: "${NOTES_TOKEN}" is not set in Polarizer\'s environment',
        ),
        (
            f'ledger_dir = "{(fake_home / ".config/parallax/led").as_posix()}"\n' + upstream,
            f"polarizer: ledger_dir {fake_home / '.config/parallax/led'} is inside {fake_home / '.config/parallax'}, which Polarizer must not write to",
        ),
    ]
    for text, line in cases:
        path = write(tmp_path, text)
        assert cli.main(["serve", "--config", str(path)]) == 2
        out, err = capsys.readouterr()
        assert (out, err) == ("", line + "\n")
    assert not marker.exists()
    assert not (fake_home / ".config").exists()


def test_serve_refuses_a_v0_ledger(tmp_path, fake_home, capsys):
    from conftest import install_fixture

    directory = install_fixture("valid/v0-parallax", tmp_path / "led")
    path = write(tmp_path, f'ledger_dir = "{directory.as_posix()}"\n[upstream.a]\ncommand = "x"\n')
    assert cli.main(["serve", "--config", str(path)]) == 3
    out, err = capsys.readouterr()
    line = f"polarizer: ledger at {directory} is v0 (Parallax's format); Polarizer only writes v1\n"
    assert (out, err) == ("", line)


def test_upstream_gets_minimal_environment(tmp_path):
    """serve starts each upstream with the SDK's minimal environment plus its own env table:
    a whole "${NAME}" value is copied from Polarizer's environment, and nothing else is."""
    from helpers import rig
    from mcp import Client
    from mcp.client.stdio import DEFAULT_INHERITED_ENV_VARS

    toml = (
        f"[upstream.p]\ncommand = {rig.toml_str(sys.executable)}\nargs = [{rig.toml_str(rig.PROBE)}]\n"
        'env = { PROBE_COPIED = "${SECRET_VAR}", PROBE_LITERAL = "as written" }\n'
    )
    cfg = rig.serve_config(tmp_path, toml, tmp_path / "ledger")
    env = {"SECRET_VAR": "s3cret", "OTHER_VAR": "not passed on"}
    rig.prime(cfg, env)

    async def scenario():
        async with Client(rig.serve_params(cfg, env)) as client:
            result = await client.call_tool("p__env", {})
            return json.loads(result.content[0].text)

    seen = anyio.run(scenario)
    assert seen["PROBE_COPIED"] == "s3cret"
    assert seen["PROBE_LITERAL"] == "as written"
    assert "SECRET_VAR" not in seen and "OTHER_VAR" not in seen
    # LC_CTYPE: Python sets it in its own environment when it coerces the C locale (PEP 538).
    # __CF_*: macOS adds these to every process.
    allowed = set(DEFAULT_INHERITED_ENV_VARS) | {"PROBE_COPIED", "PROBE_LITERAL", "LC_CTYPE"}
    extra = {k for k in seen if k not in allowed and not k.startswith("__CF_")}
    assert extra == set()
