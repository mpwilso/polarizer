"""tests/conftest.py mask_paths, on every platform: the separator is passed explicitly, so the
Windows case runs on Linux and macOS too and the POSIX case runs on Windows."""

from conftest import mask_paths


def test_windows_paths():
    text = "polarizer: C:\\Users\\you\\.local\\share\\parallax\\x is refused\n"
    masked = mask_paths(text, sep="\\", home="C:\\Users\\you")
    assert masked == "polarizer: <home>/.local/share/parallax/x is refused\n"


def test_posix_paths():
    text = "polarizer: /tmp/pytest-1/dir/taken.json already exists; nothing was written\n"
    masked = mask_paths(text, sep="/", dir="/tmp/pytest-1/dir")
    assert masked == "polarizer: <dir>/taken.json already exists; nothing was written\n"


def test_unrelated_backslash_left_alone():
    """Only the path text after a placeholder is changed: a backslash escape elsewhere in the
    line, and one after a space that ends the path, stay as they are."""
    text = 'C:\\tmp\\d\\taken.json: "a\\nb" \\x1b\n'
    masked = mask_paths(text, sep="\\", dir="C:\\tmp\\d")
    assert masked == '<dir>/taken.json: "a\\nb" \\x1b\n'


def test_longest_path_first_and_default_separator():
    """A path inside another is replaced after it, and with no sep the helper uses os.sep, as
    every golden comparison does."""
    import os

    home = os.sep.join(["", "h", "you"])
    text = f"{home}{os.sep}dir{os.sep}a and {home}{os.sep}b"
    masked = mask_paths(text, home=home, dir=f"{home}{os.sep}dir")
    assert masked == "<dir>/a and <home>/b"
