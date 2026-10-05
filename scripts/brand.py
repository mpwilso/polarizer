"""Draws Polarizer's logo lockups and the README's how-it-works diagram, light and dark.

    uv run python scripts/brand.py          write the files
    uv run python scripts/brand.py --check  exit 1 if a file differs from what this would write

Standard library only. tests/test_brand.py runs the check, so the files and this script can't
drift apart. docs/brand/README.md says what the mark means.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# One hue for Polarizer, a filter blue, as each sibling project has its own.
PALETTE = {
    "light": {
        "ink": "#1c1c1a",  # the wordmark and diagram text, as in the family's lockups
        "tint": "#EAF1FD",  # the filter's face
        "lines": "#A9C4F5",  # the parallel lines: calls that line up
        "band": "#1D4ED8",  # the turned band: a call that doesn't
        "ring": "#1E3A8A",  # the filter's rim
        "box": "#1E3A8A",  # diagram: Polarizer's parts
        "on_box": "#F1F5FF",
        "frame": "#F5F8FE",
        "edge": "#1D4ED8",
        "you": "#1c1c1a",
        "on_you": "#F1F5FF",
    },
    "dark": {
        "ink": "#EDEDEA",
        "tint": "#0E1A33",
        "lines": "#34548C",
        "band": "#7FB0FF",
        "ring": "#6EA0F5",
        "box": "#1E3A8A",
        "on_box": "#EDF3FF",
        "frame": "#0E1A33",
        "edge": "#7FB0FF",
        "you": "#EDEDEA",
        "on_you": "#10151C",
    },
}

SANS = 'ui-sans-serif,system-ui,"Segoe UI",Helvetica,Arial,sans-serif'
MONO = "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace"
TURN = 28  # degrees the band is turned from the lines
BAND_X = 58  # the line the band replaces; off center, so the mark never reads as a slash
LOCKUP_ALT = "Polarizer: a round filter of parallel lines, with one line turned out of line"


def lockup(theme: str) -> str:
    c = PALETTE[theme]
    lines = "".join(
        f'<path d="M{x} 6 V94"/>' for x in (18, 26, 34, 42, 50, 66, 74, 82)
    )  # BAND_X is the band's place: it starts in line with the others and turns
    style = (
        # The inner group's SVG transform is the finished mark, for renderers without CSS
        # animation. The outer group starts it back in line with the others and turns it into
        # place once; with no animation (or reduced motion) the outer group does nothing.
        f".turn{{transform-box:view-box;transform-origin:{BAND_X}px 50px;"
        "animation:turn 1.6s ease-out}"
        f"@keyframes turn{{from{{transform:rotate(-{TURN}deg)}}to{{transform:rotate(0deg)}}}}"
        "@media (prefers-reduced-motion: reduce){*{animation:none!important}}"
        f".word{{font-family:{SANS};font-weight:800;letter-spacing:3px}}"
    )
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 430 100" width="430" height="100" '
        f'role="img" aria-label="{LOCKUP_ALT}"><title>{LOCKUP_ALT}</title><style>{style}</style>'
        '<defs><clipPath id="face"><circle cx="50" cy="50" r="42"/></clipPath></defs>'
        f'<circle cx="50" cy="50" r="44" fill="{c["tint"]}"/>'
        f'<g clip-path="url(#face)" fill="none" stroke="{c["lines"]}" stroke-width="3">'
        f"{lines}</g>"
        f'<g class="turn"><g transform="rotate({TURN} {BAND_X} 50)" fill="none" '
        'stroke-linecap="round">'
        f'<path d="M{BAND_X} 30 V70" stroke="{c["tint"]}" stroke-width="13"/>'
        f'<path d="M{BAND_X} 30 V70" stroke="{c["band"]}" stroke-width="7"/></g></g>'
        f'<circle cx="50" cy="50" r="44" fill="none" stroke="{c["ring"]}" stroke-width="5"/>'
        f'<text class="word" x="112" y="67" font-size="46" textLength="300" '
        f'lengthAdjust="spacing" fill="{c["ink"]}">POLARIZER</text></svg>\n'
    )


# The diagram ---------------------------------------------------------------------------------

DIAGRAM_ALT = (
    "How Polarizer works: the agent, Claude Code, sends every tool call to polarizer serve. "
    "There, pins list only the tool definitions you approved, a rule function looks at the "
    "tool's class and the call's resolved paths, and a risky call is held until you allow or "
    "deny it or it times out. Allowed calls go on to the MCP servers. Every call, hold and "
    "decision goes into a hash-chained ledger. You run pending, approve, holds, allow, deny "
    "and verify in a terminal; they read the ledger and write your decisions to it. The "
    "agent's own shell and file tools are not routed through Polarizer."
)
CHAR = 7.3  # width of one 12px monospace character, rounded up
PAD = 12


def box(c, x, y, w, h, lines, kind) -> str:
    fill, text, stroke, dash = {
        "outside": ("none", c["ink"], c["edge"], ""),
        "part": (c["box"], c["on_box"], c["edge"], ""),
        "you": (c["you"], c["on_you"], c["edge"], ""),
        "ledger": (c["frame"], c["ink"], c["edge"], ""),
        "note": ("none", c["ink"], c["edge"], ' stroke-dasharray="4 3"'),
    }[kind]
    for line in lines:
        assert len(line) * CHAR + 2 * PAD <= w, f"{line!r} does not fit in {w}px"
    top = y + (h - 18 * len(lines)) / 2 + 13
    spans = "".join(
        f'<tspan x="{x + PAD}" y="{top + 18 * i:.1f}"'
        + (' font-weight="700"' if i == 0 else "")
        + f">{line}</tspan>"
        for i, line in enumerate(lines)
    )
    return (
        f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="4" fill="{fill}" '
        f'stroke="{stroke}" stroke-width="1.5"{dash}/>'
        f'<text font-family="{MONO}" font-size="12" fill="{text}">{spans}</text>'
    )


def arrow(c, x1, y1, x2, y2) -> str:
    """A line with a head at each end: calls go one way, results and decisions the other."""
    ink = c["ink"]
    if y1 == y2:
        heads = (
            f'<polygon points="{x1},{y1} {x1 + 6},{y1 - 4} {x1 + 6},{y1 + 4}" fill="{ink}"/>'
            f'<polygon points="{x2},{y2} {x2 - 6},{y2 - 4} {x2 - 6},{y2 + 4}" fill="{ink}"/>'
        )
        line = f'<line x1="{x1 + 6}" y1="{y1}" x2="{x2 - 6}" y2="{y2}"'
    else:
        heads = (
            f'<polygon points="{x1},{y1} {x1 - 4},{y1 + 6} {x1 + 4},{y1 + 6}" fill="{ink}"/>'
            f'<polygon points="{x2},{y2} {x2 - 4},{y2 - 6} {x2 + 4},{y2 - 6}" fill="{ink}"/>'
        )
        line = f'<line x1="{x1}" y1="{y1 + 6}" x2="{x2}" y2="{y2 - 6}"'
    return line + f' stroke="{ink}" stroke-width="1.5"/>' + heads


def diagram(theme: str) -> str:
    c = PALETTE[theme]
    parts = [
        box(c, 16, 70, 164, 56, ["Agent", "Claude Code"], "outside"),
        f'<rect x="212" y="20" width="256" height="204" rx="6" fill="{c["frame"]}" '
        f'stroke="{c["edge"]}" stroke-width="1.5"/>',
        f'<text x="226" y="42" font-family="{MONO}" font-size="12" font-weight="700" '
        f'fill="{c["ink"]}">polarizer serve</text>',
        box(c, 226, 54, 228, 48, ["Pins: only approved", "definitions are listed"], "part"),
        box(c, 226, 110, 228, 48, ["Rule: the tool's class", "and the resolved paths"], "part"),
        box(c, 226, 166, 228, 48, ["Holds: wait for allow", "or deny, or time out"], "part"),
        box(c, 500, 70, 164, 56, ["MCP servers", "upstream tools"], "outside"),
        arrow(c, 180, 98, 212, 98),
        arrow(c, 468, 98, 500, 98),
        box(c, 212, 268, 256, 56, ["Ledger: every call, hold", "and decision, chained"], "ledger"),
        arrow(c, 340, 224, 340, 268),
        box(
            c,
            16,
            252,
            164,
            88,
            ["You, in a terminal:", "pending, approve", "holds, allow, deny", "verify"],
            "you",
        ),
        arrow(c, 180, 296, 212, 296),
        box(
            c,
            500,
            252,
            164,
            88,
            ["Not routed through", "Polarizer: the", "agent's shell and", "its own file tools"],
            "note",
        ),
    ]
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 680 356" width="680" height="356" '
        f'role="img" aria-label="{DIAGRAM_ALT}"><title>{DIAGRAM_ALT}</title>'
        + "".join(parts)
        + "</svg>\n"
    )


def outputs() -> dict[Path, str]:
    files = {}
    for theme in ("light", "dark"):
        files[ROOT / "docs" / "brand" / f"lockup-{theme}.svg"] = lockup(theme)
        files[ROOT / "docs" / "img" / f"how-it-works-{theme}.svg"] = diagram(theme)
    return files


def main(argv: list[str]) -> int:
    stale = []
    for path, text in outputs().items():
        if "--check" in argv:
            if not path.exists() or path.read_text(encoding="utf-8") != text:
                stale.append(path.relative_to(ROOT).as_posix())
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8", newline="\n")
    for name in stale:
        print(f"brand: {name} differs from scripts/brand.py; run it")
    return 1 if stale else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
