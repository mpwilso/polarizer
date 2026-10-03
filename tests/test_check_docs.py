"""scripts/check_docs.py finds each kind of problem, and passes clean text."""

import importlib.util

import pytest
from conftest import ROOT

spec = importlib.util.spec_from_file_location("check_docs", ROOT / "scripts" / "check_docs.py")
check_docs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check_docs)

CLEAN = (
    "# Title\n\nA sentence.\n\n| a | b |\n|---|---|\n| `x|y` | 2 |\n\n```\n# not a heading\n```\n"
)


def findings(tmp_path, monkeypatch, text):
    monkeypatch.setattr(check_docs, "ROOT", tmp_path)
    path = tmp_path / "doc.md"
    path.write_bytes(text.encode("utf-8"))
    return check_docs.check(path)[1]


def test_clean_text_passes(tmp_path, monkeypatch):
    assert findings(tmp_path, monkeypatch, CLEAN) == []


def test_micro_sign_is_allowed(tmp_path, monkeypatch):
    assert findings(tmp_path, monkeypatch, "It took 3 " + chr(0xB5) + "s.\n") == []


@pytest.mark.parametrize(
    "text, expected",
    [
        ("No final newline.", "no final newline"),
        ("# A\n\n# A\n", "heading repeated"),
        (
            "A paragraph long enough to count as one, here.\n\nA paragraph long enough to count as one, here.\n",
            "paragraph repeated",
        ),
        (
            "- a line that is long enough to be checked for repeats.\n- a line that is long enough to be checked for repeats.\n",
            "line repeated",
        ),
        ("This line stops in the middle of\n\nNext.\n", "may end mid-sentence"),
        ("| a | b |\n|---|---|\n| 1 | 2 | 3 |\n", "rows have"),
        ("Zero width" + chr(0x200B) + "space.\n", "U+200B"),
        ("Line separator" + chr(0x2028) + "here.\n", "U+2028"),
        ("Accent caf" + chr(0xE9) + ".\n", "U+00E9"),
    ],
)
def test_each_finding(tmp_path, monkeypatch, text, expected):
    assert any(expected in f for f in findings(tmp_path, monkeypatch, text))


def test_the_real_docs_pass():
    assert check_docs.main() == 0
