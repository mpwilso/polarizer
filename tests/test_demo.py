"""docs/img/demo.svg is exactly what scripts/demo.py draws from a fresh run of the real commands
against the test stub, and stays small, ASCII and labelled; the README shows it under the
tagline with alt text."""

import importlib.util
import re
import sys

import pytest
from conftest import ROOT

spec = importlib.util.spec_from_file_location("demo", ROOT / "scripts" / "demo.py")
demo = importlib.util.module_from_spec(spec)
spec.loader.exec_module(demo)


@pytest.mark.skipif(sys.platform == "win32", reason="deny runs on a pseudo-terminal")
def test_demo_matches_a_fresh_run():
    assert demo.main(["--check"]) == 0


def test_demo_is_small_ascii_and_labelled():
    text = demo.OUT.read_text(encoding="utf-8")
    assert text.isascii()
    assert len(text.encode()) < 8000
    assert f'aria-label="{demo.ALT}"' in text and f"<title>{demo.ALT}</title>" in text
    assert "fs__write_file local-write" in text
    assert "held by write-pattern" in text and ".git/hooks/**" in text
    assert "polarizer: fs__write_file was not allowed" in text


def test_readme_shows_the_demo_under_the_tagline():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    tagline = readme.index("<b>Human in the loop only works if the human is still looking.</b>")
    [tag] = re.findall(r'<img src="docs/img/demo\.svg"[^>]*>', readme)
    assert readme.index(tag) > tagline
    assert readme.index(tag) < readme.index("\nStatus: ")
    assert re.search(r'\balt="[^"]{40,}"', tag)
