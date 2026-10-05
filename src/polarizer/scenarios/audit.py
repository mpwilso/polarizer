"""The shortcut audit of a scenario set (docs/MEASURE-SPEC.md, section 6, Shortcuts): can a
person, or a trivial rule, tell planted from clean without reading the task and the arguments?

Only what a drill shows before the answer is used: the call screen without the reveal. Every
feature value gets its planted/clean split, and a value with at least TELL_MIN scenarios of
which at least TELL_SHARE share one answer is a tell. Then a leave-one-out experiment: each
scenario is answered by a rule learned from all the others, either the majority answer for one
feature's value or a naive Bayes over every feature, and scored over the scenarios and over
seeded 20-call drills drawn by the drill's own sampler, next to always answering allow.

The guided screen adds one line in plain words under the block, so its audit adds two features
of that line: its length, and whether it names the argument that matters, which for a planted
call is the value that differs from the task (section 6, Plain words). A planted call's line
must describe that value exactly, so the check is that clean calls' lines name their own
argument just as often, and are as long.

Standard library only. tests/test_scenarios.py runs it over the shipped set, and
`python -m polarizer.scenarios audit` and the review sheet print it.
"""

import json
import math
import re
from collections import Counter, defaultdict

from polarizer import drill

TELL_MIN = 6
TELL_SHARE = 0.9
MARGIN = 0.10  # how far the best single-feature rule may beat always-allow, at most
SUBSTRINGS = ("..", ".git", ".ssh", ".env", "http", "@")
ARGS_EDGES = (60, 100, 140)  # bytes of arguments, as the block's args_commit line counts them
TASK_EDGES = (60, 75, 90)  # characters in the task line
PLAIN_EDGES = (70, 100, 125)  # characters in the plain line, near set 1's quartiles (67, 96, 118)
PLAIN_NAMES = ("plain length", "plain names the argument")
# Parts of a value too common to show that a line names it.
GENERIC = {"the", "and", "com", "net", "org", "example", "https", "http", "www", "home",
           "users", "file"}  # fmt: skip
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


def words(text: str) -> set:
    """The parts of a text that can show it names a value: runs of letters and digits,
    two characters or more, lowercased, without the generic ones."""
    return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if len(w) >= 2} - GENERIC


def _text(value) -> str:
    return value if isinstance(value, str) else json.dumps(value)


def focus(items: list) -> dict:
    """{id: the text of the argument that matters}. For a planted call, the value that differs
    from the task (`plant.planted`), or the tool for a different tool. For a clean call, the
    value of the argument that planted calls on its tool change most often (the first by name
    on a tie), or its first argument when none on its tool changes one."""
    changed = defaultdict(Counter)
    for s in items:
        if s["answer"] == "planted" and "argument" in s["plant"]:
            changed[s["call"]["tool"]][s["plant"]["argument"]] += 1
    found = {}
    for s in items:
        call = s["call"]
        if s["answer"] == "planted":
            plant = s["plant"]
            found[s["id"]] = call["tool"] if "planted" not in plant else _text(plant["planted"])
            continue
        counts = changed[call["tool"]]
        named = sorted(counts, key=lambda a: (-counts[a], a))
        name = next((a for a in named if a in call["arguments"]), None)
        if name is None:
            name = next(iter(call["arguments"]), None)
        found[s["id"]] = "" if name is None else _text(call["arguments"][name])
    return found


def plain_features(items: list, plain: dict) -> dict:
    """{id: the guided screen's two features of its plain line}."""
    texts = focus(items)
    found = {}
    for s in items:
        line = plain[s["id"]]
        named = words(texts[s["id"]]) & words(line)
        found[s["id"]] = {
            "plain length": bucket(len(line), PLAIN_EDGES),
            "plain names the argument": "yes" if named else "no",
        }
    return found


def names(plain: dict | None = None) -> tuple:
    return NAMES + (PLAIN_NAMES if plain is not None else ())


def table(items: list, plain: dict | None = None) -> dict:
    """{id: every feature of the screen}: the plain screen's, and with `plain` (id -> line) the
    guided screen's two more."""
    found = {s["id"]: features(s) for s in items}
    if plain is not None:
        for i, extra in plain_features(items, plain).items():
            found[i].update(extra)
    return found


def split(items: list, plain: dict | None = None) -> dict:
    """{feature: {value: [planted, clean]}}."""
    feats = table(items, plain)
    found = {name: defaultdict(lambda: [0, 0]) for name in names(plain)}
    for s in items:
        for name, value in feats[s["id"]].items():
            found[name][value][s["answer"] == "clean"] += 1
    return found


def tells(items: list, plain: dict | None = None) -> list[tuple]:
    """(feature, value, planted, clean) of every value with at least TELL_MIN scenarios of
    which at least TELL_SHARE share one answer."""
    found = []
    for name, values in split(items, plain).items():
        for value, (p, c) in sorted(values.items()):
            if p + c >= TELL_MIN and max(p, c) >= TELL_SHARE * (p + c):
                found.append((name, value, p, c))
    return found


# Leave-one-out: each scenario is answered by a rule learned from all the others. ------------


def majority_loo(items: list, name: str, plain: dict | None = None) -> dict:
    """{id: the majority answer for its value of one feature, learned without it}. Deny only
    where planted is the strict majority; a tie, or a value no other scenario has, is allow."""
    feats = table(items, plain)
    counts = defaultdict(Counter)
    for s in items:
        counts[feats[s["id"]][name]][s["answer"]] += 1
    out = {}
    for s in items:
        c = counts[feats[s["id"]][name]].copy()
        c[s["answer"]] -= 1
        out[s["id"]] = "planted" if c["planted"] > c["clean"] else "clean"
    return out


def bayes_loo(items: list, plain: dict | None = None) -> dict:
    """{id: naive Bayes over every feature, add-one smoothing, learned without it}; deny only
    where planted scores strictly higher."""
    feats = table(items, plain)
    every = names(plain)
    sizes = {name: len({f[name] for f in feats.values()}) for name in every}
    totals = Counter(s["answer"] for s in items)
    counts = Counter((s["answer"], name, feats[s["id"]][name]) for s in items for name in every)
    out = {}
    for s in items:
        own = feats[s["id"]]
        score = {}
        for answer in ("planted", "clean"):
            n = totals[answer] - (s["answer"] == answer)
            total = math.log((n + 1) / (len(items) - 1 + 2))
            for name in every:
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


def experiment(the_set, count: int = SESSIONS, plain: dict | None = None) -> list[tuple]:
    """(rule, accuracy over the scenarios, mean accuracy over `count` drills), always-allow
    first, then each feature's majority rule, then naive Bayes. With `plain`, over the guided
    screen's features."""
    items = list(the_set.scenarios.values())
    answer = {s["id"]: s["answer"] for s in items}
    drawn = [order for _, _, order in plans(the_set, count)]

    def row(name: str, guess: dict) -> tuple:
        over_items = sum(guess[i] == answer[i] for i in answer) / len(answer)
        per_drill = [sum(guess[i] == answer[i] for i in order) / len(order) for order in drawn]
        return name, over_items, sum(per_drill) / len(per_drill)

    rows = [row("always allow", dict.fromkeys(answer, "clean"))]
    rows += [row(name, majority_loo(items, name, plain)) for name in names(plain)]
    rows.append(row("naive Bayes, all features", bayes_loo(items, plain)))
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


def report(the_set, sessions: int = SESSIONS, level: int = 1, guided: bool = False) -> list[str]:
    """The audit in markdown, its top heading at `level`. With `guided`, the audit of the guided
    screen: the tables of the plain line's two features, then the leave-one-out accuracy and the
    tells over all of that screen's features."""
    h = "#" * level
    items = list(the_set.scenarios.values())
    plain = the_set.plain if guided else None
    planted = sum(s["answer"] == "planted" for s in items)
    found = tells(items, plain)
    flagged = {(t[0], t[1]) for t in found}
    if guided:
        lines = [
            f"{h} Shortcut audit, set {the_set.version}, guided screen",
            "",
            "The guided screen is the call screen with one line in plain words under the block. "
            "Its features are the call screen's and two of that line: its length in characters, "
            "and whether it names the argument that matters (for a planted call the value that "
            "differs from the task, for a different tool the tool; for a clean call the "
            "argument planted calls on its tool change most often). Only the two new features' "
            "tables are below; the rest are in the call screen's audit.",
            "",
        ]
    else:
        lines = [
            f"{h} Shortcut audit, set {the_set.version}",
            "",
            f"{len(items)} scenarios: {planted} planted, {len(items) - planted} clean. Features "
            "are what a drill shows before the answer. Argument bytes are the length the block's "
            "args_commit line gives; task length is in characters. A tell is a value with at "
            f"least {TELL_MIN} scenarios of which at least {round(100 * TELL_SHARE)}% share one "
            "answer.",
            "",
        ]
    for name, values in split(items, plain).items():
        if guided and name not in PLAIN_NAMES:
            continue
        lines += [f"{h}# {name}", "", "| value | planted | clean | planted share | tell |",
                  "|---|---|---|---|---|"]  # fmt: skip
        for value, (p, c) in sorted(values.items(), key=lambda kv: (-sum(kv[1]), kv[0])):
            mark = "tell" if (name, value) in flagged else ""
            lines.append(f"| {value} | {p} | {c} | {_pct(p / (p + c))} | {mark} |")
        lines.append("")
    rows = experiment(the_set, sessions, plain)
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
