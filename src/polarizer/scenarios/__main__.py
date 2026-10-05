"""Development helpers for the newest scenario set (docs/MEASURE-SPEC.md, section 6, Adding a
scenario):

    python -m polarizer.scenarios show <id>           the screen of one scenario
    python -m polarizer.scenarios show <id> --answer  the screen, then the answer and its why
    python -m polarizer.scenarios sheet --out <path>  a review sheet of every scenario, in markdown
    python -m polarizer.scenarios audit               the shortcut audit and the planted count

`show` is for a reviewer to answer one scenario before reading its answer. `sheet` writes every
scenario (id, answer, shape, task, the block a drill shows and the reveal), then a summary of the
set, a list of things for the reviewer to look at, the shortcut audit and the planted count's
distribution (polarizer.scenarios.audit). `audit` prints those last two alone.
"""

import difflib
import sys
from collections import Counter
from pathlib import Path

from polarizer import drill, scenarios
from polarizer.scenarios import audit

TS = "2026-10-06T18:02:11.425Z"
NEAR = 0.85  # task lines at least this similar (difflib's ratio) are listed as near-duplicates
SHORT_WHY = 8  # reveals under this many words are listed
FEW_PER_SHAPE = 8  # shapes with fewer planted scenarios than this are listed
RARE_TOOL = 3  # tools held in fewer scenarios than this are listed


def block(the_set: scenarios.ScenarioSet, scenario: dict) -> list[str]:
    """The hold block a drill shows, with zero ids, a zero args_commit and a fixed time."""
    return drill.call_block(scenario, "0" * 16, "0" * 64, "1" * 16, TS, TS, the_set.timeout_seconds)


def kind(scenario: dict) -> str:
    shape = scenario.get("shape")
    return f"planted ({scenarios.SHAPE_WORDS[shape]})" if shape else "clean"


def show(the_set: scenarios.ScenarioSet, scenario_id: str, answer: bool) -> int:
    scenario = the_set.scenarios.get(scenario_id)
    if scenario is None:
        print(f"no scenario {scenario_id} in set {the_set.version}", file=sys.stderr)
        return 2
    print(f"call 1 of 1\ntask: {scenario['task']}\n")
    print("\n".join(block(the_set, scenario)))
    if answer:
        print(f"\nanswer: {kind(scenario)}\nwhy: {scenario['why']}")
    return 0


def _fence(lines: list[str]) -> str:
    """A code fence longer than any run of backticks in the lines."""
    runs = [len(r) for line in lines for r in _backtick_runs(line)]
    return "`" * max([3, *(n + 1 for n in runs)])


def _backtick_runs(line: str) -> list[str]:
    runs, current = [], ""
    for c in line:
        if c == "`":
            current += c
        elif current:
            runs.append(current)
            current = ""
    return runs + ([current] if current else [])


def _fenced(lines: list[str]) -> list[str]:
    fence = _fence(lines)
    return [fence + "text", *lines, fence]


def _table(head: tuple, rows: list) -> list[str]:
    lines = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    return lines + ["| " + " | ".join(str(c) for c in row) + " |" for row in rows]


def _counts(title: str, counter: Counter) -> list[str]:
    rows = sorted(counter.items(), key=lambda kv: (-kv[1], str(kv[0])))
    return [f"### {title}", "", *_table((title.split()[-1], "scenarios"), rows), ""]


def look_at(the_set: scenarios.ScenarioSet) -> list[str]:
    """The things a reviewer should look at, one line each; empty when there are none."""
    found = []
    items = list(the_set.scenarios.values())
    tasks = [(s["id"], s["task"]) for s in items]
    for i, (a, task_a) in enumerate(tasks):
        for b, task_b in tasks[i + 1 :]:
            if task_a == task_b:
                found.append(f"{a} and {b}: the same task line")
                continue
            matcher = difflib.SequenceMatcher(None, task_a.lower(), task_b.lower())
            if matcher.quick_ratio() >= NEAR and matcher.ratio() >= NEAR:
                found.append(f"{a} and {b}: task lines {matcher.ratio():.2f} alike")
    for s in items:
        words = len(s["why"].split())
        if words < SHORT_WHY:
            found.append(f"{s['id']}: the reveal has {words} words")
    shapes = Counter(s["shape"] for s in items if s["answer"] == "planted")
    for shape in scenarios.SHAPES:
        if shapes[shape] < FEW_PER_SHAPE:
            found.append(f"shape {shape}: {shapes[shape]} scenarios")
    tools = Counter(s["call"]["tool"] for s in items)
    for s in items:
        if tools[s["call"]["tool"]] < RARE_TOOL:
            found.append(
                f"{s['id']}: tool {s['call']['tool']} is held in "
                f"{tools[s['call']['tool']]} scenario{'s' if tools[s['call']['tool']] > 1 else ''}"
            )
    return found


def sheet(the_set: scenarios.ScenarioSet) -> str:
    """The review sheet, in markdown: every scenario in file order, then the summary."""
    items = list(the_set.scenarios.values())
    planted = [s for s in items if s["answer"] == "planted"]
    lines = [
        f"# Drill scenario set {the_set.version}: review sheet",
        "",
        f"{len(items)} scenarios ({len(planted)} planted, {len(items) - len(planted)} clean), "
        f"set file sha256 `{the_set.sha256}`. Each block is what a drill shows, with zero ids "
        "and a fixed time. The summary is at the end.",
        "",
    ]
    for s in items:
        shape = s.get("shape")
        lines += [
            f"## {s['id']}",
            "",
            f"{s['answer']}, shape {shape}" if shape else "clean",
            "",
            *_fenced([f"task: {s['task']}", "", *block(the_set, s)]),
            "",
            "Reveal:",
            "",
            *_fenced([f"why: {s['why']}"]),
            "",
        ]
    calls = [s["call"] for s in items]
    lines += ["## Summary", ""]
    shapes = Counter(s["shape"] for s in planted)
    lines += _counts("Per shape", Counter({x: shapes[x] for x in scenarios.SHAPES}))
    lines += _counts("Per tool", Counter(c["tool"] for c in calls))
    lines += _counts("Per class", Counter(c["class"] or "(none)" for c in calls))
    lines += _counts("Per rule", Counter(c["rule"] for c in calls))
    lines += _counts("Per answer", Counter(s["answer"] for s in items))
    rows = []
    for shape in scenarios.SHAPES:
        tools = {s["call"]["tool"] for s in planted if s["shape"] == shape}
        clean = sum(s["answer"] == "clean" and s["call"]["tool"] in tools for s in items)
        rows.append((shape, shapes[shape], clean, ", ".join(sorted(tools))))
    lines += [
        "### Planted versus clean per shape",
        "",
        "Clean scenarios have no shape, so the clean count is of clean scenarios held on the "
        "tools that shape's planted scenarios use: the clean calls a planted one must be told "
        "apart from.",
        "",
        *_table(("shape", "planted", "clean on the same tools", "tools"), rows),
        "",
        "### Look at these",
        "",
        f"Task lines that are the same or at least {NEAR:.2f} alike (difflib's ratio), reveals "
        f"under {SHORT_WHY} words, shapes with fewer than {FEW_PER_SHAPE} scenarios, and "
        f"scenarios held on a tool that fewer than {RARE_TOOL} scenarios use.",
        "",
    ]
    found = look_at(the_set)
    lines += [f"- {line}" for line in found] if found else ["Nothing."]
    lines += ["", *nearest(the_set), ""]
    lines += audit.report(the_set, level=3) + audit.count_lines(the_set, level=3)
    return "\n".join(lines[:-1]) + "\n"


def nearest(the_set: scenarios.ScenarioSet) -> list[str]:
    """The closest the set comes to the first two checks: its most alike pair of task lines and
    its shortest reveals."""
    items = list(the_set.scenarios.values())
    best = (0.0, "", "")
    for i, a in enumerate(items):
        for b in items[i + 1 :]:
            matcher = difflib.SequenceMatcher(None, a["task"].lower(), b["task"].lower())
            if matcher.quick_ratio() > best[0] and matcher.ratio() > best[0]:
                best = (matcher.ratio(), a["id"], b["id"])
    words = {s["id"]: len(s["why"].split()) for s in items}
    fewest = min(words.values())
    shortest = ", ".join(i for i, n in words.items() if n == fewest)
    lines = [f"Closest to these: the reveals of {shortest} have {fewest} words"]
    if best[1]:
        lines[0] += f"; the most alike task lines are {best[1]} and {best[2]}, {best[0]:.2f}"
    return [lines[0] + "."]


USAGE = __doc__.split("\n\n")[1]


def main(argv: list[str]) -> int:
    if len(argv) in (2, 3) and argv[0] == "show" and argv[2:] in ([], ["--answer"]):
        return show(scenarios.newest(), argv[1], bool(argv[2:]))
    if len(argv) == 3 and argv[:2] == ["sheet", "--out"]:
        the_set = scenarios.newest()
        path = Path(argv[2])
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="ascii", newline="\n") as handle:
            handle.write(sheet(the_set))
        print(f"wrote {path}: {len(the_set.scenarios)} scenarios of set {the_set.version}")
        return 0
    if argv == ["audit"]:
        the_set = scenarios.newest()
        print("\n".join(audit.report(the_set) + audit.count_lines(the_set)[:-1]))
        return 0
    print(USAGE, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
