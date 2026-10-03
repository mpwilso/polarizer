"""polarizer.toml: parse and validate only, with each error's exact one line."""

import textwrap

import pytest

from polarizer import cli
from polarizer.config import ConfigError, load

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
