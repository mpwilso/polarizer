"""The logo lockups and the how-it-works diagram are exactly what scripts/brand.py draws, and
stay small, ASCII and labelled for screen readers."""

import importlib.util
import re

from conftest import ROOT

spec = importlib.util.spec_from_file_location("brand", ROOT / "scripts" / "brand.py")
brand = importlib.util.module_from_spec(spec)
spec.loader.exec_module(brand)


def test_files_match_the_script():
    assert brand.main(["--check"]) == 0


def test_files_are_small_ascii_and_labelled():
    for path, text in brand.outputs().items():
        assert text.isascii(), path
        assert len(text.encode()) < 8000, path
        label = re.search(r'aria-label="([^"]+)"', text).group(1)
        assert f"<title>{label}</title>" in text, path


def test_reduced_motion_and_static_mark():
    """Reduced motion stops the animation, and the finished mark is an SVG transform, so a
    renderer without CSS animation still shows the turned line."""
    for theme in ("light", "dark"):
        text = brand.lockup(theme)
        assert "@media (prefers-reduced-motion: reduce){*{animation:none!important}}" in text
        assert f'transform="rotate({brand.TURN} {brand.BAND_X} 50)"' in text
