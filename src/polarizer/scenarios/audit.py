"""The shortcut audit of a scenario set (docs/MEASURE-SPEC.md, section 6, Shortcuts): can a
person, or a trivial rule, tell planted from clean without reading the task and the arguments?

Only what a drill shows before the answer is used: the call screen without the reveal. Every
feature value gets its planted/clean split, and a value with at least TELL_MIN scenarios of
which at least TELL_SHARE share one answer is a tell. Then a leave-one-out experiment: each
scenario is answered by a rule learned from all the others, either the majority answer for one
feature's value or a naive Bayes over every feature, and scored over the scenarios and over
seeded 20-call drills drawn by the drill's own sampler, next to always answering allow.

Standard library only. tests/test_scenarios.py runs it over the shipped set, and
`python -m polarizer.scenarios audit` and the review sheet print it.
"""

import math
from collections import Counter, defaultdict

from polarizer import drill

TELL_MIN = 6
TELL_SHARE = 0.9
MARGIN = 0.10  # how far the best single-feature rule may beat always-allow, at most
SUBSTRINGS = ("..", ".git", ".ssh", ".env", "http", "@")
ARGS_EDGES = (60, 100, 140)  # bytes of arguments, as the block's args_commit line counts them
TASK_EDGES = (60, 75, 90)  # characters in the task line
CALLS = 20
SESSIONS = 1000
PER_CONDITION = 10000


def bucket(n: int, edges: tuple) -> str:
    """'<60', '60-99', '100-139' or '140+' for the edges (60, 100, 140)."""
    if n < edges[0]:
        return f"<{edges[0]}"
    for lo, hi in zip(edges, edges[1:], strict=False):
        if n < hi:
            return f"{lo}-{hi - 1}"
    return f"{edges[-1]}+"


def features(s: dict) -> dict:
    """What the call screen shows before the answer, as categorical features."""
    call = s["call"]
    text = drill.arguments_bytes(call["arguments"])
    found = {
        "tool": call["tool"],
        "class": str(call["class"]),
        "class_from": str(call["class_from"]),
        "rule": call["rule"],
        "arguments": str(len(call["arguments"])),
        "argument bytes": bucket(len(text), ARGS_EDGES),
        "task length": bucket(len(s["task"]), TASK_EDGES),
    }
    for part in SUBSTRINGS:
        found[f'"{part}" in arguments'] = "yes" if part.encode() in text else "no"
    return found


NAMES = (
    "tool", "class", "class_from", "rule", "arguments", "argument bytes", "task length",
    *(f'"{part}" in arguments' for part in SUBSTRINGS),
)  # fmt: skip


def split(items: list) -> dict:
    """{feature: {value: [planted, clean]}}."""
    table = {name: defaultdict(lambda: [0, 0]) for name in NAMES}
    for s in items:
        for name, value in features(s).items():
            table[name][value][s["answer"] == "clean"] += 1
    return table


def tells(items: list) -> list[tuple]:
    """(feature, value, planted, clean) of every value with at least TELL_MIN scenarios of
    which at least TELL_SHARE share one answer."""
    found = []
    for name, values in split(items).items():
        for value, (p, c) in sorted(values.items()):
            if p + c >= TELL_MIN and max(p, c) >= TELL_SHARE * (p + c):
                found.append((name, value, p, c))
    return found


# Leave-one-out: each scenario is answered by a rule learned from all the others. ------------


def majority_loo(items: list, name: str) -> dict:
    """{id: the majority answer for its value of one feature, learned without it}. Deny only
    where planted is the strict majority; a tie, or a value no other scenario has, is allow."""
    counts = defaultdict(Counter)
    for s in items:
        counts[features(s)[name]][s["answer"]] += 1
    out = {}
    for s in items:
        c = counts[features(s)[name]].copy()
        c[s["answer"]] -= 1
        out[s["id"]] = "planted" if c["planted"] > c["clean"] else "clean"
    return out


def bayes_loo(items: list) -> dict:
    """{id: naive Bayes over every feature, add-one smoothing, learned without it}; deny only
    where planted scores strictly higher."""
    feats = {s["id"]: features(s) for s in items}
    sizes = {name: len({f[name] for f in feats.values()}) for name in NAMES}
    totals = Counter(s["answer"] for s in items)
    counts = Counter((s["answer"], name, feats[s["id"]][name]) for s in items for name in NAMES)
    out = {}
    for s in items:
        own = feats[s["id"]]
        score = {}
        for answer in ("planted", "clean"):
            n = totals[answer] - (s["answer"] == answer)
            total = math.log((n + 1) / (len(items) - 1 + 2))
            for name in NAMES:
                hits = counts[answer, name, own[name]] - (s["answer"] == answer)
                total += math.log((hits + 1) / (n + sizes[name]))
            score[answer] = total
        out[s["id"]] = "planted" if score["planted"] > score["clean"] else "clean"
    return out


def plans(the_set, count: int, calls: int = CALLS) -> list[tuple]:
    """(condition, planted count, order) of `count` drills drawn by the drill's sampler, with
    the seeds 0, 1, 2, ... as 16-byte big-endian counters and nothing excluded."""
    shapes, clean = the_set.shapes(), the_set.ids("clean")
    return [drill.plan(i.to_bytes(16, "big"), shapes, clean, calls, set()) for i in range(count)]


def experiment(the_set, count: int = SESSIONS) -> list[tuple]:
    """(rule, accuracy over the scenarios, mean accuracy over `count` drills), always-allow
    first, then each feature's majority rule, then naive Bayes."""
    items = list(the_set.scenarios.values())
    answer = {s["id"]: s["answer"] for s in items}
    drawn = [order for _, _, order in plans(the_set, count)]

    def row(name: str, guess: dict) -> tuple:
        over_items = sum(guess[i] == answer[i] for i in answer) / len(answer)
        per_drill = [sum(guess[i] == answer[i] for i in order) / len(order) for order in drawn]
        return name, over_items, sum(per_drill) / len(per_drill)

    rows = [row("always allow", dict.fromkeys(answer, "clean"))]
    rows += [row(name, majority_loo(items, name)) for name in NAMES]
    rows.append(row("naive Bayes, all features", bayes_loo(items)))
    return rows


def best_single(rows: list) -> tuple:
    """The single-feature row with the best accuracy over the scenarios."""
    return max(rows[1:-1], key=lambda r: (r[1], r[2]))


def counts(the_set, per_condition: int = PER_CONDITION, calls: int = CALLS) -> dict:
    """The planted counts of drills drawn with the seeds of `plans`, until each condition has
    `per_condition`: {"counts": {condition: Counter of counts}, "reportable": {condition:
    drills with at least 5 planted and at least 10 clean calls}, "missing": {condition: drills
    missing a shape}, "seeds": seeds used}."""
    shapes, clean = the_set.shapes(), the_set.ids("clean")
    found = {c: Counter() for c in drill.CONDITIONS}
    reportable = Counter()
    missing = Counter()
    seed = 0
    while min(sum(c.values()) for c in found.values()) < per_condition:
        condition, k, order = drill.plan(seed.to_bytes(16, "big"), shapes, clean, calls, set())
        seed += 1
        if sum(found[condition].values()) >= per_condition:
            continue
        found[condition][k] += 1
        reportable[condition] += k >= 5 and calls - k >= 10
        missing[condition] += len({shapes[i] for i in order if i in shapes}) < len(
            set(shapes.values())
        )
    return {"counts": found, "reportable": reportable, "missing": missing, "seeds": seed}


# Printing --------------------------------------------------------------------------------


def _pct(x: float) -> str:
    return f"{100 * x:.1f}%"


def report(the_set, sessions: int = SESSIONS, level: int = 1) -> list[str]:
    """The audit in markdown, its top heading at `level`."""
    h = "#" * level
    items = list(the_set.scenarios.values())
    planted = sum(s["answer"] == "planted" for s in items)
    found = tells(items)
    flagged = {(t[0], t[1]) for t in found}
    lines = [
        f"{h} Shortcut audit, set {the_set.version}",
        "",
        f"{len(items)} scenarios: {planted} planted, {len(items) - planted} clean. Features are "
        "what a drill shows before the answer. Argument bytes are the length the block's "
        "args_commit line gives; task length is in characters. A tell is a value with at least "
        f"{TELL_MIN} scenarios of which at least {round(100 * TELL_SHARE)}% share one answer.",
        "",
    ]
    for name, values in split(items).items():
        lines += [f"{h}# {name}", "", "| value | planted | clean | planted share | tell |",
                  "|---|---|---|---|---|"]  # fmt: skip
        for value, (p, c) in sorted(values.items(), key=lambda kv: (-sum(kv[1]), kv[0])):
            mark = "tell" if (name, value) in flagged else ""
            lines.append(f"| {value} | {p} | {c} | {_pct(p / (p + c))} | {mark} |")
        lines.append("")
    rows = experiment(the_set, sessions)
    best = best_single(rows)
    lines += [
        f"{h}# Leave-one-out accuracy",
        "",
        f"Each scenario is answered by a rule learned from the other {len(items) - 1}. "
        f"Scenarios: the share answered right. Drills: the mean share answered right over "
        f"{sessions} seeded {CALLS}-call drills (seeds 0 to {sessions - 1}, nothing excluded).",
        "",
        "| rule | scenarios | drills |",
        "|---|---|---|",
        *(f"| {r[0]} | {_pct(r[1])} | {_pct(r[2])} |" for r in rows),
        "",
        f"Best single-feature rule: {best[0]}, {_pct(best[1])} over the scenarios and "
        f"{_pct(best[2])} over the drills, against always allow's {_pct(rows[0][1])} and "
        f"{_pct(rows[0][2])}.",
        "",
        "Tells: "
        + ("; ".join(f"{t[0]} = {t[1]} ({t[2]} planted, {t[3]} clean)" for t in found) or "none")
        + ".",
        "",
    ]
    return lines


def count_lines(the_set, per_condition: int = PER_CONDITION, level: int = 1) -> list[str]:
    """The planted count's distribution over seeded 20-call drills, in markdown."""
    h = "#" * level
    got = counts(the_set, per_condition)
    lo, hi = drill.planted_range(CALLS, len(the_set.shapes()), len(set(the_set.shapes().values())))
    head = " | ".join(str(k) for k in range(lo, hi + 1))
    lines = [
        f"{h} Planted count, set {the_set.version}",
        "",
        f"{per_condition} drills of {CALLS} calls per condition, from the first {got['seeds']} "
        "seeds (0, 1, 2, ... as 16-byte counters, nothing excluded).",
        "",
        f"| condition | {head} | 5+ planted and 10+ clean | a shape missing |",
        "|---" * (hi - lo + 4) + "|",
    ]
    for condition, found in got["counts"].items():
        cells = " | ".join(str(found[k]) for k in range(lo, hi + 1))
        n = sum(found.values())
        lines.append(
            f"| {condition} | {cells} | {_pct(got['reportable'][condition] / n)} | "
            f"{got['missing'][condition]} |"
        )
    return lines + [""]
