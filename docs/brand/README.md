# Polarizer brand

Every SVG here, and the diagram in [../img/](../img/), is drawn by `scripts/brand.py` from the palette at its top. To change one, edit the script and run `uv run python scripts/brand.py`. `tests/test_brand.py` fails if a file and the script disagree.

## The mark

A polarizing filter: a round face of parallel lines, and one line turned out of line with the others. Light that lines up with a filter passes; light at an angle to it is stopped. The parallel lines are calls that line up with what you approved, and pass. The turned line is the call that doesn't, and is held for you.

### The animation

The lockups play once when the image loads, in 1.6 s, and hold. They open on the finished mark; the lines and the held call's line fade out in 0.15 s; the parallel lines draw in from the top, one after another from the left, the held call's line in its place among them, still in line; then that line swings out of line to its final angle, easing in and out; then nothing moves. It is CSS keyframes in the file's own `<style>`, with no script, so GitHub runs it in an `<img>`.

The first frame and the last are the finished mark, and so is every element's own style, so the whole mark shows whenever the animation doesn't play: for viewers who ask for reduced motion (`prefers-reduced-motion: reduce`), in renderers without CSS animation, and in a renderer that paints the image once and never moves its clock on. That last case is why the mark opens whole. Until Oct 6, 2026 the lines started at `scaleY(0)`, and GitHub, in Chrome 154 on Windows, showed an empty circle, which is that old first frame. Hiding happens only in keyframes between the first and the last. The finished frame is the static mark this replaced, element for element (`tests/test_brand.py`), and `tests/test_brand_render.py` checks in a headless Chromium that an `<img>` of each lockup shows it after the animation, with the animation off and with its clock stopped at the start.

### Files

| File | Use |
|---|---|
| [lockup-light.svg](lockup-light.svg) | The mark and the word, on light pages. Animated. |
| [lockup-dark.svg](lockup-dark.svg) | The same, on dark pages. Animated. |
| [mark-small.svg](mark-small.svg) | The mark alone at 32 px and under, such as a favicon, an avatar or a list icon; nothing in this repository shows it yet (README.md uses the lockups). Still, cropped close to the filter, with three thicker lines in place of eight (eight would be a pixel each and run together) and a thicker band and rim. Its own face and rim read on light and dark pages, so there is one file. |

## The lockup

The mark and the word POLARIZER. Use [lockup-light.svg](lockup-light.svg) on light pages and [lockup-dark.svg](lockup-dark.svg) on dark ones. An image can't see the page's theme, so a web page picks the file:

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="lockup-dark.svg">
  <img src="lockup-light.svg" alt="Polarizer: a round filter of parallel lines, with one line turned out of line" height="72">
</picture>

## Palette

One hue, a filter blue. The light and dark values are in `PALETTE` in `scripts/brand.py`.

| Name | Light | Dark | Use |
|---|---|---|---|
| ring | `#1E3A8A` | `#6EA0F5` | The filter's rim. |
| band | `#1D4ED8` | `#7FB0FF` | The turned line, and the diagram's box edges. |
| lines | `#A9C4F5` | `#34548C` | The parallel lines. |
| tint | `#EAF1FD` | `#0E1A33` | The filter's face. |
| ink | `#1c1c1a` | `#EDEDEA` | The word and the diagram's text. |

## Type

The word is set in the reader's own sans-serif system font, weight 800, so the files carry no font. Its spacing is fixed with `textLength`, so it fits whatever font draws it.
