"""The docs integrity check: run this instead of retyping commands.

    uv run python scripts/check_docs.py

Checks docs/*.md and CLAUDE.md and prints a report per file: word count, line count and the
heading list. A finding makes it exit 1:
- a repeated paragraph, or a repeated line of 40 or more characters outside code blocks and
  tables (a copy-paste or merge slip);
- a prose line that ends mid-sentence just before a blank line or the end of the file;
- a table whose rows don't all have the same number of columns;
- any character outside printable ASCII (plus tab and newline), except the micro sign;
- a missing final newline, or a duplicate heading.
Standard library only. scripts/test.sh runs it, so CI does too.
"""

import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ALLOWED = {chr(0xB5)}  # the micro sign, used for microseconds
ENDINGS = tuple(".:;!?)`*>]\"'|")


def files() -> list[Path]:
    return [ROOT / "CLAUDE.md", *sorted((ROOT / "docs").glob("*.md"))]


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
