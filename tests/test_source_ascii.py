"""Every tracked .py file is ASCII only, and the special-character fixture holds the intended
code points at byte level. (CLAUDE.md rule 12: file-writing tools can turn an escape into the
character it names, so source keeps every non-ASCII character as an escape.)"""

import subprocess

import pytest
from conftest import CONFORMANCE, ROOT

EXCEPTIONS: set[str] = set()  # keep empty

BACKSLASH = b"\\"
SHORT_ESCAPES = {0x08: b"b", 0x09: b"t", 0x0A: b"n", 0x0C: b"f", 0x0D: b"r"}
RAW_CODE_POINTS = [0x7F, 0xA0, 0x2028, 0x2029, 0xFEFF, 0xFFFD, 0xE000, 0x1F600, 0xE0049, 0x10FFFF]


def tracked_python_files():
    try:
        done = subprocess.run(
            ["git", "-C", str(ROOT), "ls-files", "-z", "--", "*.py"],
            capture_output=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("not a git work tree, or git is unavailable")
    return [name for name in done.stdout.decode("utf-8").split("\0") if name]


def test_tracked_python_files_are_ascii():
    files = tracked_python_files()
    assert files, "git ls-files found no .py files"
    problems = []
    for name in files:
        if name in EXCEPTIONS:
            continue
        for number, line in enumerate((ROOT / name).read_bytes().split(b"\n"), 1):
            if any(byte > 0x7F for byte in line):
                problems.append(f"{name}:{number}: {line[:100]!r}")
    assert not problems, "non-ASCII bytes in tracked .py files:\n" + "\n".join(problems)


def test_exception_list_stays_empty():
    assert EXCEPTIONS == set()


def test_unicode_fixture_holds_the_intended_code_points():
    """valid/unicode.jsonl: U+0000 to U+001F as the escapes the canonical form requires (short
    forms for 08, 09, 0A, 0C and 0D, lowercase hex for the rest), and the others as raw UTF-8."""
    data = (CONFORMANCE / "valid" / "unicode.jsonl").read_bytes()
    controls = b"".join(
        BACKSLASH + SHORT_ESCAPES[c] if c in SHORT_ESCAPES else BACKSLASH + b"u%04x" % c
        for c in range(0x20)
    )
    assert controls in data
    for code_point in RAW_CODE_POINTS:
        assert chr(code_point).encode("utf-8") in data, f"U+{code_point:04X} missing"
    escapes = {data[i : i + 6] for i in range(len(data)) if data[i : i + 2] == BACKSLASH + b"u"}
    assert escapes == {BACKSLASH + b"u%04x" % c for c in range(0x20) if c not in SHORT_ESCAPES}
