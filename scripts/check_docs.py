"""The docs integrity check: run this instead of retyping commands.

    uv run python scripts/check_docs.py

Checks CLAUDE.md, the Markdown files at the repo root (README.md and the others) and every
Markdown file under docs/, and prints a report per file: word count, line count and the
heading list. A finding makes it exit 1:
- a repeated paragraph, or a repeated line of 40 or more characters outside code blocks and
  tables (a copy-paste or merge slip);
- a prose line that ends mid-sentence just before a blank line or the end of the file;
- a table whose rows don't all have the same number of columns;
- any character outside printable ASCII (plus tab and newline), except the micro sign;
- a missing final newline, or a duplicate heading;
- a relative link, image or srcset whose file doesn't exist, or whose #anchor names no heading
  in that file (GitHub's anchor rules), and an image with no alt text.
Standard library only. scripts/test.sh runs it, so CI does too.
"""

import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ALLOWED = {chr(0xB5)}  # the micro sign, used for microseconds
ENDINGS = tuple(".:;!?)`*>]\"'|")


def slug(heading: str) -> str:
    """GitHub's anchor for a heading."""
    text = re.sub(r"[^\w\- ]", "", heading.replace("`", "").strip().lower())
    return text.replace(" ", "-")


def anchors(path: Path) -> set[str]:
    text = re.sub(r"```.*?```", "", path.read_text(encoding="utf-8"), flags=re.S)
    return {slug(h) for h in re.findall(r"^#{1,6} (.+)$", text, re.M)}


def link_findings(path: Path, text: str) -> list[str]:
    """Relative links, images and srcsets must name a file that exists and, with #anchor, a
    heading in it; every image needs alt text. Code blocks and code spans are skipped."""
    prose = re.sub(r"```.*?```", "", text, flags=re.S)
    prose = re.sub(r"`[^`\n]*`", "", prose)
    targets = re.findall(r"\]\(([^)\s]+)\)", prose)
    targets += re.findall(r'\b(?:src|srcset|href)="([^"]+)"', prose)
    found = []
    for target in targets:
        if re.match(r"[a-z]+:", target):
            continue  # https:, mailto: and the like
        name, _, anchor = target.partition("#")
        where = (path.parent / name) if name else path
        if not where.exists():
            found.append(f"link to a missing file: {target}")
        elif anchor and where.suffix == ".md" and anchor not in anchors(where):
            found.append(f"link to a missing heading: {target}")
    for tag in re.findall(r"<img\b[^>]*>", prose):
        if not re.search(r'\balt="[^"]+"', tag):
            found.append(f"image with no alt text: {tag[:60]}")
    for alt in re.findall(r"!\[([^\]]*)\]\(", prose):
        if not alt.strip():
            found.append("image with no alt text")
    return found


def files() -> list[Path]:
    root = [p for p in sorted(ROOT.glob("*.md")) if p.name != "CLAUDE.md"]
    docs = sorted((ROOT / "docs").rglob("*.md"), key=lambda p: p.relative_to(ROOT).parts)
    return [ROOT / "CLAUDE.md", *root, *docs]


def table_cells(line: str) -> int:
    """Columns in a table row: pipes outside inline code and not escaped."""
    count, in_code, prev = 0, False, ""
    for ch in line.strip():
        if ch == "`":
            in_code = not in_code
        elif ch == "|" and not in_code and prev != "\\":
            count += 1
        prev = ch
    return count - 1


def check(path: Path) -> tuple[list[str], list[str]]:
    text = path.read_text(encoding="utf-8")
    lines = text.split("\n")
    report, findings = [], []
    in_code, prose, tables, current = [], [], [], []
    fenced = False
    headings = []
    for number, line in enumerate(lines, 1):
        if line.startswith("```"):
            fenced = not fenced
            in_code.append(number)
            continue
        if fenced or line.startswith("    "):
            in_code.append(number)
            continue
        if line.startswith("|"):
            current.append((number, table_cells(line)))
            continue
        if current:
            tables.append(current)
            current = []
        if re.match(r"#{1,6} ", line):
            headings.append(line)
        prose.append((number, line))
    if current:
        tables.append(current)

    words = len(text.split())
    report.append(f"{path.relative_to(ROOT)}: {words} words, {len(lines)} lines")
    report.append("  headings: " + " | ".join(headings))

    if not text.endswith("\n"):
        findings.append("no final newline")
    for heading, n in Counter(headings).items():
        if n > 1:
            findings.append(f"heading repeated {n} times: {heading}")
    paragraphs = [p.strip() for p in text.split("\n\n") if len(p.strip()) > 40]
    for paragraph, n in Counter(paragraphs).items():
        if n > 1:
            findings.append(f"paragraph repeated {n} times: {paragraph[:60]!r}")
    long_lines = Counter(line.strip() for _, line in prose if len(line.strip()) >= 40)
    for line, n in long_lines.items():
        if n > 1:
            findings.append(f"line repeated {n} times: {line[:60]!r}")
    for number, line in prose:
        stripped = line.rstrip()
        if not stripped or stripped.startswith("#"):
            continue
        next_blank = number == len(lines) or not lines[number].strip()
        if next_blank and not stripped.endswith(ENDINGS):
            findings.append(f"line {number} may end mid-sentence: ...{stripped[-50:]!r}")
    for table in tables:
        counts = {cells for _, cells in table}
        if len(counts) > 1:
            first = table[0][0]
            findings.append(f"table at line {first}: rows have {sorted(counts)} columns")
    for number, line in enumerate(lines, 1):
        for ch in line:
            if ch in ALLOWED or ch == "\t" or " " <= ch <= "~":
                continue
            findings.append(f"line {number}: character U+{ord(ch):04X} is not allowed")
    findings += link_findings(path, text)
    return report, findings


def main() -> int:
    total = 0
    for path in files():
        report, findings = check(path)
        print("\n".join(report))
        for finding in findings:
            print(f"  FINDING: {finding}")
        total += len(findings)
    print(f"check_docs: {len(files())} files, {total} findings")
    return 1 if total else 0


if __name__ == "__main__":
    sys.exit(main())
