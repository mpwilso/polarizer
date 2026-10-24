"""The logo lockups, the small mark and the how-it-works diagram are exactly what
scripts/brand.py draws, and stay small, ASCII and labelled for screen readers. The lockups
loop, with CSS only, and the frame every cycle starts and ends on is the static mark they
replaced."""

import importlib.util
import re
import xml.etree.ElementTree as ET

from conftest import ROOT

spec = importlib.util.spec_from_file_location("brand", ROOT / "scripts" / "brand.py")
brand = importlib.util.module_from_spec(spec)
spec.loader.exec_module(brand)

BRAND = ROOT / "docs" / "brand"
ANIMATED = [BRAND / "lockup-light.svg", BRAND / "lockup-dark.svg"]
REDUCED = "@media (prefers-reduced-motion: reduce){*{animation:none!important}}"

# The lockups' geometry at 7afd4e1, before the lines were animated, taken from the committed
# files with geometry() below (the same in both themes): every element but <style> and <title>,
# with its attributes less class, fill, stroke, role and aria-label.
OLD_GEOMETRY = [
    ("svg", (("height", "100"), ("viewBox", "0 0 430 100"), ("width", "430"))),
    ("defs", ()),
    ("clipPath", (("id", "face"),)),
    ("circle", (("cx", "50"), ("cy", "50"), ("r", "42"))),
    ("circle", (("cx", "50"), ("cy", "50"), ("r", "44"))),
    ("g", (("clip-path", "url(#face)"), ("stroke-width", "3"))),
    *[("path", (("d", f"M{x} 6 V94"),)) for x in (18, 26, 34, 42, 50, 66, 74, 82)],
    ("g", ()),
    ("g", (("stroke-linecap", "round"), ("transform", "rotate(28 58 50)"))),
    ("path", (("d", "M58 30 V70"), ("stroke-width", "13"))),
    ("path", (("d", "M58 30 V70"), ("stroke-width", "7"))),
    ("circle", (("cx", "50"), ("cy", "50"), ("r", "44"), ("stroke-width", "5"))),
    (
        "text",
        (("font-size", "46"), ("lengthAdjust", "spacing"), ("textLength", "300"), ("x", "112"),
         ("y", "67")),
        "POLARIZER",
    ),
]  # fmt: skip


def geometry(text: str) -> list[tuple]:
    out = []
    for el in ET.fromstring(text).iter():
        tag = el.tag.split("}")[1]
        if tag in ("style", "title"):
            continue
        skip = ("class", "fill", "stroke", "role", "aria-label")
        attrs = tuple(sorted((k, v) for k, v in el.attrib.items() if k not in skip))
        out.append((tag, attrs) + ((el.text,) if tag == "text" else ()))
    return out


def seconds(value: str) -> float:
    return float(value[:-2]) / 1000 if value.endswith("ms") else float(value[:-1])


def test_files_match_the_script():
    assert brand.main(["--check"]) == 0
    names = {path.name for path in brand.outputs()}
    assert {"lockup-light.svg", "lockup-dark.svg", "mark-small.svg"} <= names


def test_files_are_small_ascii_and_labelled():
    for path, text in brand.outputs().items():
        assert text.isascii(), path
        assert len(text.encode()) < 8000, path
        label = re.search(r'aria-label="([^"]+)"', text).group(1)
        assert f"<title>{label}</title>" in text, path


def test_no_script_anywhere():
    """GitHub shows these in an <img>, where scripts never run; none is written."""
    for path, text in brand.outputs().items():
        assert "<script" not in text.lower() and "javascript:" not in text.lower(), path
        assert not re.search(r"\son[a-z]+=", text), path


def test_animated_files_respect_reduced_motion():
    for path in ANIMATED:
        text = path.read_text(encoding="utf-8")
        assert "@keyframes" in text, path
        assert REDUCED in text, path


def test_animation_loops_with_a_long_hold():
    """Every animation repeats for good, with no delay, and every one has the same cycle: the
    moving part, under 2.5 s, then the finished mark held still for at least 2 s. The hold is
    what keeps a looping header from being busy, and it is where the loop joins."""
    for path in ANIMATED:
        style = re.search(r"<style>(.*?)</style>", path.read_text(encoding="utf-8")).group(1)
        declarations = re.findall(r"animation:([^;}]+)", style)
        declarations = [v for v in declarations if not v.startswith("none")]  # reduced motion
        assert declarations, path
        for value in declarations:
            assert "infinite" in value.split(), (path, value)
            times = re.findall(r"\d*\.?\d+m?s\b", value)
            assert len(times) == 1, (path, value)  # a duration, no delay
            assert seconds(times[0]) == brand.CYCLE_SECONDS, (path, value)
        assert "animation-delay" not in style, path
        assert brand.TOTAL_SECONDS < 2.5, brand.TOTAL_SECONDS
        assert brand.CYCLE_SECONDS - brand.TOTAL_SECONDS >= 2, brand.CYCLE_SECONDS
        # Nothing moves after the moving part: every keyframe offset is at or before its end.
        _, found = keyframes(style)
        last = brand.TOTAL_SECONDS / brand.CYCLE_SECONDS * 100
        for name, frames in found.items():
            for selector, _ in frames:
                for stop in selector.split(","):
                    assert float(stop.strip().rstrip("%")) <= last + 0.01, (path, name, stop)


def test_final_frame_is_the_old_static_mark():
    """In the hold (or with the animation never run), each lockup is the mark as it was: the same
    elements with the same geometry. Animation lives only in <style> and class attributes."""
    for path in ANIMATED:
        assert geometry(path.read_text(encoding="utf-8")) == OLD_GEOMETRY, path


def test_reduced_motion_and_static_mark():
    """Reduced motion stops the animation, and the finished mark is an SVG transform, so a
    renderer without CSS animation still shows the turned line."""
    for theme in ("light", "dark"):
        text = brand.lockup(theme)
        assert REDUCED in text
        assert f'transform="rotate({brand.TURN} {brand.BAND_X} 50)"' in text


# What hides an element: no opacity, no visibility, no display, a dash offset, or a scale of 0
# on either axis.
HIDING = re.compile(
    r"opacity\s*:\s*0*\.?0*\s*(?:!important\s*)?(?:;|}|$)"
    r"|visibility\s*:\s*hidden|display\s*:\s*none|stroke-dashoffset"
    r"|scale[XY]?\(\s*0*\.?0*\s*[,)]|scale\([^)]*,\s*0*\.?0*\s*\)"
)
HIDING_ATTRIBUTE = re.compile(
    r'\s(?:opacity|fill-opacity|stroke-opacity)="0*\.?0*"|\svisibility="hidden"'
    r'|\sdisplay="none"|\sstroke-dashoffset=|\stransform="[^"]*scale\(\s*0'
)


def test_hiding_patterns():
    for hidden in ("opacity:0", "opacity: 0.0;", "visibility:hidden", "display:none",
                   "stroke-dashoffset:40", "transform:scaleY(0)", "transform:scale(0)",
                   "transform:scale(1, 0)", "transform:scaleX(0.0)"):  # fmt: skip
        assert HIDING.search(hidden), hidden
    for shown in ("opacity:1", "opacity:.5", "transform:none", "transform:scaleY(1)",
                  "transform:scale(0.5)", "transform:rotate(-28deg)", "animation:none"):  # fmt: skip
        assert not HIDING.search(shown), shown
    for hidden in (' opacity="0"', ' visibility="hidden"', ' transform="scale(0)"'):
        assert HIDING_ATTRIBUTE.search(hidden), hidden
    assert not HIDING_ATTRIBUTE.search(' transform="rotate(28 58 50)" opacity="1"')


def keyframes(style: str) -> tuple[str, dict[str, list[tuple[str, str]]]]:
    """(style less its @keyframes blocks, {name: [(selector, declarations), ...]})."""
    rest, found, pos = "", {}, 0
    for m in re.finditer(r"@keyframes\s+([\w-]+)\s*{", style):
        if m.start() < pos:
            continue
        depth, end = 1, m.end()
        while depth:
            depth += {"{": 1, "}": -1}.get(style[end], 0)
            end += 1
        body = style[m.end() : end - 1]
        found[m.group(1)] = re.findall(r"([^{}]+){([^{}]*)}", body)
        rest += style[pos : m.start()]
        pos = end
    return rest + style[pos:], found


def test_nothing_is_hidden_outside_keyframes():
    """No base rule and no attribute hides anything: the mark with no animation at all is the
    whole mark. Hiding is only ever a keyframe's."""
    for path in ANIMATED:
        text = path.read_text(encoding="utf-8")
        style = re.search(r"<style>(.*?)</style>", text).group(1)
        base, _ = keyframes(style)
        assert not HIDING.search(base), (path, HIDING.search(base).group(0))
        assert not HIDING_ATTRIBUTE.search(text), (path, HIDING_ATTRIBUTE.search(text).group(0))
        assert not re.search(r"\b(?:forwards|both)\b", style), path


def test_first_and_last_frames_hide_nothing():
    """Every animation's first keyframe and last keyframe show the whole mark. A renderer that
    paints the image once and never moves its clock on shows the first frame for good (GitHub,
    Chrome 154 on Windows, Oct 2026: an empty circle when the lines started at scaleY(0)), and a
    finished animation shows the last. Hiding may sit only between them."""
    for path in ANIMATED:
        style = re.search(r"<style>(.*?)</style>", path.read_text(encoding="utf-8")).group(1)
        _, found = keyframes(style)
        assert found, path
        for name, frames in found.items():
            for selector, declarations in frames:
                stops = {s.strip() for s in selector.split(",")}
                if stops & {"from", "0%", "to", "100%"}:
                    assert not HIDING.search(declarations), (path, name, selector, declarations)


def test_small_mark_is_still_close_cropped_and_thick():
    """mark-small.svg, for 32 px and under: no animation, a viewBox cropped to the filter, and
    every stroke at least 1.5 px wide at 32 px."""
    text = (BRAND / "mark-small.svg").read_text(encoding="utf-8")
    assert "<style" not in text and "animation" not in text and "@keyframes" not in text
    root = ET.fromstring(text)
    assert root.get("width") == root.get("height") == "32"
    x, y, w, h = (float(v) for v in root.get("viewBox").split())
    assert w == h <= 100 and x > 0 and y > 0
    widths = [float(v) for v in re.findall(r'stroke-width="([\d.]+)"', text)]
    assert widths and min(widths) * 32 / w >= 1.5, widths
    assert f'transform="rotate({brand.TURN} {brand.BAND_X} 50)"' in text
