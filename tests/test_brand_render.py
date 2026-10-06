"""The animated lockups, drawn by a real browser in an <img>, as GitHub shows them, show the
whole mark: after the animation has run in real time, with the animation switched off, and
with its clock stopped at the start. Each is compared with the reduced-motion render, which is
the finished mark.

These need a headless Chromium (tests/helpers/chromium.py says where it looks) and skip without
one. They take about 8 s, most of it the real-time wait.
"""

from pathlib import Path

import pytest
from conftest import ROOT
from helpers import chromium

BRAND = ROOT / "docs" / "brand"
# Each lockup on the page background it is meant for: light on white, dark on GitHub's dark.
LOCKUPS = (("light", "#ffffff"), ("dark", "#0d1117"))
LINES = {"light": "#A9C4F5", "dark": "#34548C"}  # the parallel lines
BAND = {"light": "#1D4ED8", "dark": "#7FB0FF"}  # the turned line
SCALE = 2
WIDTH, HEIGHT = 430, 100
TOLERANCE = 12  # per channel; keeps the dark band's color apart from the dark rim's
OFF = "<style>*{animation:none!important}</style></svg>"
STOPPED = "<style>*{animation-play-state:paused!important}</style></svg>"

BINARY = chromium.find()
pytestmark = pytest.mark.skipif(
    BINARY is None,
    reason="no headless Chromium found in ~/.cache/ms-playwright or at $POLARIZER_CHROMIUM, "
    "or Windows, where tests/helpers/chromium.py can't drive one",
)


def disc(i: int) -> tuple[int, int, int, int]:
    """The i-th lockup's disc, (left, top, right, bottom) in screenshot pixels."""
    return (0, i * HEIGHT * SCALE, 100 * SCALE, (i + 1) * HEIGHT * SCALE)


def page(folder: Path, name: str, change: str | None = None) -> str:
    """A page with both lockups in <img>s, one under the other, each copied first with
    `change` put in place of its closing </svg> when given."""
    imgs = ""
    for theme, background in LOCKUPS:
        svg = BRAND / f"lockup-{theme}.svg"
        if change:
            copy = folder / f"{name}-lockup-{theme}.svg"
            copy.write_text(svg.read_text(encoding="utf-8").replace("</svg>", change))
            svg = copy
        imgs += (
            f'<div style="background:{background}"><img src="{svg.as_uri()}" width="{WIDTH}" '
            f'height="{HEIGHT}" style="display:block"></div>'
        )
    html = folder / f"{name}.html"
    html.write_text(f'<!doctype html><html><body style="margin:0">{imgs}</body></html>')
    return html.as_uri()


def render(url: str, seconds: float, extra: tuple[str, ...] = ()) -> bytes:
    with chromium.Browser(BINARY, extra) as browser:
        start = browser.open(url, WIDTH, HEIGHT * len(LOCKUPS), scale=SCALE)
        png, _ = browser.at(start, seconds)
    return png


@pytest.fixture(scope="module")
def renders(tmp_path_factory) -> dict[str, bytes]:
    folder = tmp_path_factory.mktemp("lockups")
    return {
        "reduced": render(page(folder, "reduced"), 1.0, ("--force-prefers-reduced-motion",)),
        "after 6 s": render(page(folder, "played"), 6.0),
        "animation off": render(page(folder, "off", OFF), 1.0),
        "clock stopped at 0": render(page(folder, "stopped", STOPPED), 1.0),
    }


def counts(png: bytes, i: int, theme: str) -> tuple[int, int]:
    box = disc(i)
    return (
        chromium.count(png, box, (LINES[theme],), TOLERANCE),
        chromium.count(png, box, (BAND[theme],), TOLERANCE),
    )


def test_reduced_motion_render_shows_the_mark(renders):
    """The reference: the reduced-motion render has the lines and the band."""
    for i, (theme, _) in enumerate(LOCKUPS):
        lines, band = counts(renders["reduced"], i, theme)
        assert lines > 2000 and band > 400, (theme, lines, band)


@pytest.mark.parametrize("name", ["after 6 s", "animation off", "clock stopped at 0"])
def test_lockup_in_an_img_shows_the_mark(renders, name):
    for i, (theme, _) in enumerate(LOCKUPS):
        lines, band = counts(renders[name], i, theme)
        want_lines, want_band = counts(renders["reduced"], i, theme)
        assert lines >= 0.9 * want_lines and band >= 0.9 * want_band, (
            name,
            theme,
            (lines, band),
            (want_lines, want_band),
        )
        left, top, right, bottom = disc(i)
        changed = chromium.differing(renders[name], renders["reduced"], disc(i))
        assert changed <= 0.01 * (right - left) * (bottom - top), (name, theme, changed)
