"""README.md's rules, the ones every repo in the family shares (ported from Loupe's
tests/readme.test.ts): under 155 shown lines, with every <details> block collapsed to one line;
no em dash or en dash; no bare angle-bracket placeholder in prose, where a reader would take it
for a tag GitHub swallowed; and none of the words only, first and best in prose, since each
overclaims. Prose is the README without its code blocks, code spans, HTML comments and quoted
lines, and an <img>'s alt text counts as prose while every other tag is dropped. Links and
anchors are tests/test_readme.py::test_readme_links_resolve's, not repeated here. A second test
feeds each rule a small text that should fail it and checks the message.

This file is ASCII (tests/test_source_ascii.py), so the dashes are built with chr()."""

import re

from conftest import ROOT

README = (ROOT / "README.md").read_text(encoding="utf-8")
EM_DASH = chr(0x2014)
EN_DASH = chr(0x2013)
TAGS = {"p", "picture", "source", "img", "b", "details", "summary", "br", "a", "sub", "sup"}
WORDS = re.compile(r"\b(only|first|best)\b", re.I)


def prose(text: str) -> str:
    """The text as GitHub shows it, less code blocks, code spans, HTML comments and quoted
    lines (copied output, not the README's own claims)."""
    text = re.sub(r"^```.*?^```$", "", text, flags=re.S | re.M)
    text = re.sub(r"`[^`\n]*`", "", text)
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    return re.sub(r"^>.*$", "", text, flags=re.M)


def problems(text: str) -> list[str]:
    found = []
    shown = re.sub(r"<details>.*?</details>", "<details></details>", text, flags=re.S)
    lines = shown.count("\n") + (0 if shown.endswith("\n") or not shown else 1)
    if lines >= 155:
        found.append(f"{lines} lines; keep it under 155")
    if EM_DASH in text:
        found.append("an em dash")
    if EN_DASH in text:
        found.append("an en dash")
    visible = prose(text)
    for tag, name in re.findall(r"(</?([^\s>/]*)[^>]*>)", visible):
        if name.lower() not in TAGS:
            found.append(f"a bare angle-bracket placeholder: {tag}")

    def alt(match: re.Match) -> str:
        inner = re.search(r'alt="([^"]*)"', match.group(0))
        return inner.group(1) if inner else ""

    words = re.sub(r"<[^>]*>", alt, visible)
    for word in WORDS.findall(words):
        found.append(f'"{word}" in the prose')
    return found


def test_readme_passes_every_rule():
    assert problems(README) == []


def test_each_rule_catches_what_it_should():
    assert problems("line\n" * 155) == ["155 lines; keep it under 155"]
    assert problems("line\n" * 154) == []
    assert problems("<details>\n" + "line\n" * 155 + "</details>\n") == []
    assert problems(f"A long pause{EM_DASH}then more.\n") == ["an em dash"]
    assert problems(f"From 2 {EN_DASH} 3.\n") == ["an en dash"]
    assert problems("Show the draft under <the reason>.\n") == [
        "a bare angle-bracket placeholder: <the reason>"
    ]
    assert problems("Show the draft under `<the reason>`.\n\n```\n<folder>\n```\n") == []
    assert problems('<p align="center"><b>Bold</b><br></p>\n<!-- <note> -->\n') == []
    assert problems(
        'The first tool, and the best.\n<img src="brand/mark.svg" alt="Our only mark">\n'
    ) == [
        '"first" in the prose',
        '"best" in the prose',
        '"only" in the prose',
    ]
    assert (
        problems("```markdown\nFirst question: only the next box?\n```\n> The best line.\n") == []
    )
    assert problems("Firstly, the bestiary is lonely.\n") == []
