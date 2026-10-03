"""Where the ledger may live: protected paths are refused by real path components, Parallax's
two directories always; any other git working tree only warns. Uses a temporary home."""

import os
import subprocess
import sys

import pytest
from conftest import install_fixture

from polarizer import cli
from polarizer.ledgerdir import ProtectedPath, always_protected, check_location, is_inside


def test_parallax_directories_are_always_protected(fake_home):
    assert always_protected() == [
        fake_home / ".local" / "share" / "parallax",
        fake_home / ".config" / "parallax",
    ]


@pytest.mark.parametrize(
    "inside",
    [".local/share/parallax", ".local/share/parallax/sub/ledger", ".config/parallax/x"],
)
def test_refused_inside_parallax_directories(fake_home, inside):
    with pytest.raises(ProtectedPath) as raised:
        check_location(fake_home / inside, always_protected())
    assert str(raised.value).startswith(f"polarizer: ledger_dir {fake_home / inside} is inside ")
    assert str(raised.value).endswith(", which Polarizer must not write to")


def test_components_not_string_prefixes(fake_home):
    assert check_location(fake_home / ".local/share/parallax2", always_protected()) is None
    assert check_location(fake_home / ".config/parallax-old/x", always_protected()) is None


def test_dot_dot_and_symlinks_are_resolved(fake_home, tmp_path):
    sneaky = fake_home / ".local/share/polarizer/../parallax/ledger"
    assert is_inside(sneaky, fake_home / ".local/share/parallax")
    if sys.platform == "win32":
        pytest.skip("symlinks need privileges on Windows")
    target = fake_home / ".config" / "parallax"
    target.mkdir(parents=True)
    link = tmp_path / "innocent"
    link.symlink_to(target)
    with pytest.raises(ProtectedPath):
        check_location(link / "ledger", always_protected())


def test_config_paths_add_to_the_defaults(fake_home, tmp_path):
    repo = tmp_path / "code" / "parallax"
    with pytest.raises(ProtectedPath):
        check_location(repo / "data", [*always_protected(), repo])
    with pytest.raises(ProtectedPath):
        check_location(fake_home / ".config/parallax", [*always_protected(), repo])


def git(*args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def test_warning_inside_another_git_working_tree(fake_home, tmp_path):
    repo = tmp_path / "somerepo"
    repo.mkdir()
    git("init", "-q", cwd=repo)
    warning = check_location(repo / "not" / "yet" / "made", always_protected())
    top = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"], cwd=repo, capture_output=True, text=True
    ).stdout.strip()
    assert warning == (
        f"polarizer: warning: ledger_dir {repo / 'not' / 'yet' / 'made'} "
        f"is inside the git working tree {top}"
    )
    assert check_location(tmp_path / "plain", always_protected()) is None


def test_repair_refuses_a_protected_ledger_dir(fake_home, capsys):
    directory = install_fixture("broken/tear_last_line", fake_home / ".local/share/parallax")
    before = sorted(os.listdir(directory))
    assert cli.main(["repair", "--ledger-dir", str(directory)]) == 2
    out, err = capsys.readouterr()
    assert out == ""
    assert err == (
        f"polarizer: ledger_dir {directory} is inside {directory}, "
        "which Polarizer must not write to\n"
    )
    assert sorted(os.listdir(directory)) == before


def test_repair_warns_inside_a_git_tree_and_continues(fake_home, tmp_path, capsys):
    repo = tmp_path / "repo"
    repo.mkdir()
    git("init", "-q", cwd=repo)
    directory = install_fixture("valid/session", repo / "ledger")
    assert cli.main(["repair", "--ledger-dir", str(directory)]) == 0
    out, err = capsys.readouterr()
    assert out == "nothing to repair: ledger is intact\n"
    assert err.startswith("polarizer: warning: ledger_dir ")
