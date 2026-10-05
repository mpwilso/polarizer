"""Drills: offline practice with held calls, their record, and their report
(docs/MEASURE-SPEC.md, sections 3 to 9).

A drill shows held calls from the scenario set one at a time, each under the task the agent was
given and rendered exactly as `polarizer holds` renders a real hold (holds.render_block). The
person answers allow or deny, and each answer is revealed at once. Everything is recorded in the
drill's own ledger, in the existing format, as drill.* entries; nothing is forwarded, no network
connection is opened and no process is started. The end screen and `drill report` are computed
from the ledger alone, through the same functions.

The plan of a drill (condition, number of planted calls, order) is a function of its seed, the
set, the number of calls and the excluded ids, drawn from SHA-256 so any later Python can draw
it again (section 6, Sampling).
"""

import hashlib
import json
import math
import os
import secrets
import sys
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import polarizer
from polarizer import holds, ledgerdir, lock, measure, scenarios, writer
from polarizer.ledger import LOCKED_LINE, Locked, read_files, verify_bytes
from polarizer.text import printable, safe

CLOSING = [
    "Drills measure attention when you know you are being tested. Use them to see",
    "trends in your own oversight, not to grade or rank anyone.",
]
CONDITIONS = ("plain", "prediction-gate")
CONDITION_WORDS = {"plain": "plain", "prediction-gate": "prediction gate"}
CALLS_RANGE = (10, 40)
DEFAULT_CALLS = 20
OVER_MS = 300_000  # an answer slower than a real hold's default timeout
PREDICTION_LIMIT = 200
STOP_LINE = "polarizer: the drill ledger could not record that; the drill stops"
SET_PROBLEM = "polarizer: the drill scenarios in this installation fail their check: {}"
SERVE_LEDGER = (
    "polarizer: {} holds a serve ledger; drills keep their own "
    "(the default is ~/.local/share/polarizer-drills)"
)
NO_TERMINAL = (
    "polarizer: drill needs a person at a terminal; it does not run from a script or a pipe"
)
REVEALS = {
    "caught": "Planted call. You denied it: caught.",
    "missed": "Planted call. You allowed it: missed. In real use it would have run.",
    "right": "Clean call. You allowed it.",
    "false-flag": (
        "Clean call. You denied it: a false flag. In real use the agent could not have done "
        "its task."
    ),
}
OUTCOMES = {
    (True, "deny"): "caught",
    (True, "allow"): "missed",
    (False, "allow"): "right",
    (False, "deny"): "false-flag",
}
OVER_LINE = "You took over 300 s; a real hold would have timed out before your answer."


def default_dir() -> Path:
    return Path.home() / ".local" / "share" / "polarizer-drills"


# The seams tests replace: the clock, the random source, the terminal check, the console and the
# scenario set.
monotonic_ns = time.monotonic_ns
random = secrets


def is_terminal() -> bool:
    try:
        return os.isatty(sys.stdin.fileno()) and os.isatty(sys.stdout.fileno())
    except (AttributeError, ValueError, OSError):
        return False


class StdConsole:
    """stdout and stdin. readline gives "" at the end of input; Ctrl+C raises
    KeyboardInterrupt."""

    def write(self, text: str) -> None:
        sys.stdout.write(text)

    def flush(self) -> None:
        sys.stdout.flush()

    def readline(self) -> str:
        return sys.stdin.readline()


make_console = StdConsole
load_set = scenarios.newest


def err(line: str) -> None:
    print(line, file=sys.stderr)


# The plan (section 6, Sampling) --------------------------------------------------------------


def stream(seed: bytes):
    """Four 8-byte big-endian integers per SHA-256 block of the seed and a counter."""
    counter = 0
    while True:
        block = hashlib.sha256(b"POLARIZER-DRILL/1\n" + seed + counter.to_bytes(8, "big")).digest()
        for i in range(4):
            yield int.from_bytes(block[8 * i : 8 * i + 8], "big")
        counter += 1


def below(s, n: int) -> int:
    limit = (2**64 // n) * n
    while True:
        x = next(s)
        if x < limit:
            return x % n


def shuffle(s, xs: list) -> list:
    xs = list(xs)
    for i in range(len(xs) - 1, 0, -1):
        j = below(s, i + 1)
        xs[i], xs[j] = xs[j], xs[i]
    return xs


def plan(seed: bytes, planted: list, clean: list, calls: int, excluded) -> tuple[str, int, list]:
    """(condition drawn, number of planted calls, order of scenario ids)."""
    s = stream(seed)
    condition = CONDITIONS[below(s, 2)]
    lo, hi = math.ceil(calls / 5), (2 * calls) // 5
    k = lo + below(s, hi - lo + 1)
    p = sorted(x for x in planted if x not in excluded)
    if len(p) < k:
        p = sorted(planted)
    c = sorted(x for x in clean if x not in excluded)
    if len(c) < calls - k:
        c = sorted(clean)
    p = shuffle(s, p)[:k]
    c = shuffle(s, c)[: calls - k]
    return condition, k, shuffle(s, p + c)


# The fold over a drill ledger ----------------------------------------------------------------


@dataclass
class Record:
    """One drill, from its drill.* entries."""

    session: str
    ts: str
    started: dict
    shown: dict = field(default_factory=dict)  # n -> data
    shown_ts: dict = field(default_factory=dict)  # n -> ts
    predicted: dict = field(default_factory=dict)
    decided: dict = field(default_factory=dict)
    revealed: dict = field(default_factory=dict)
    ended: dict | None = None


@dataclass(frozen=True)
class Answer:
    n: int
    scenario: str
    answer: str  # clean or planted
    shape: str | None
    outcome: str  # caught, missed, right or false-flag
    elapsed_ms: int
    seen_before: int

    @property
    def over(self) -> bool:
        return self.elapsed_ms > OVER_MS


class DrillFold:
    """Drills in drill.started order, and how often each scenario has been shown. Kinds and
    malformed data it doesn't know are ignored; session.started marks a serve ledger."""

    def __init__(self):
        self.records: dict[str, Record] = {}
        self.order: list[str] = []
        self.shown_count: Counter = Counter()
        self.serve = False

    def apply(self, entry: dict) -> None:
        kind, data = entry.get("kind"), entry.get("data")
        if kind == "session.started":
            self.serve = True
            return
        if not isinstance(kind, str) or not kind.startswith("drill.") or not isinstance(data, dict):
            return
        session = data.get("session")
        if kind == "drill.started":
            if isinstance(session, str) and session not in self.records:
                self.records[session] = Record(session, entry.get("ts", ""), data)
                self.order.append(session)
            return
        record = self.records.get(session)
        n = data.get("n")
        if record is None:
            return
        if kind == "drill.ended":
            record.ended = record.ended or data
            return
        if type(n) is not int:
            return
        if kind == "drill.shown":
            record.shown.setdefault(n, data)
            record.shown_ts.setdefault(n, entry.get("ts", ""))
            self.shown_count[data.get("scenario")] += 1
        elif kind == "drill.predicted":
            record.predicted.setdefault(n, data)
        elif kind == "drill.decided":
            record.decided.setdefault(n, data)
        elif kind == "drill.revealed":
            record.revealed.setdefault(n, data)

    def drills(self) -> list[Record]:
        return [self.records[s] for s in self.order]

    def recent_scenarios(self, count: int = 2) -> list[str]:
        """The scenario ids shown in the last `count` drills, sorted."""
        found = set()
        for record in self.drills()[-count:]:
            found |= {d.get("scenario") for d in record.shown.values()}
        return sorted(x for x in found if isinstance(x, str))


def answers(record: Record) -> list[Answer]:
    """The answered calls of a drill: a drill.decided with its drill.revealed, in call order."""
    found = []
    for n in sorted(record.decided):
        decided, revealed = record.decided[n], record.revealed.get(n)
        if revealed is None:
            continue
        elapsed, outcome = decided.get("elapsed_ms"), revealed.get("outcome")
        if type(elapsed) is not int or outcome not in REVEALS:
            continue
        seen = record.shown.get(n, {}).get("seen_before", 0)
        found.append(
            Answer(
                n,
                str(revealed.get("scenario")),
                revealed.get("answer"),
                revealed.get("shape"),
                outcome,
                elapsed,
                seen if type(seen) is int else 0,
            )
        )
    return found


@dataclass
class Tally:
    planted: int = 0
    caught: int = 0
    clean: int = 0
    false_flags: int = 0
    times: list = field(default_factory=list)
    planted_times: list = field(default_factory=list)
    clean_times: list = field(default_factory=list)


def tally(found: list[Answer]) -> Tally:
    t = Tally()
    for a in found:
        t.times.append(a.elapsed_ms)
        if a.answer == "planted":
            t.planted += 1
            t.caught += a.outcome == "caught"
            t.planted_times.append(a.elapsed_ms)
        else:
            t.clean += 1
            t.false_flags += a.outcome == "false-flag"
            t.clean_times.append(a.elapsed_ms)
    return t


# Lines shared by the end screen and the report -----------------------------------------------


def _rate(word: str, k: int, n: int) -> str:
    parts = measure.rate_parts(k, n)
    if parts is None:
        return f"{measure.TOO_FEW}."
    return f"{word} {parts[0]}%, 95% interval {parts[1]}% to {parts[2]}%."


def kind_lines(t: Tally, you: bool) -> list[str]:
    verb = "You denied" if you else "Denied"
    lines = []
    for label, n, k, word in (
        ("planted calls", t.planted, t.caught, "caught"),
        ("clean calls", t.clean, t.false_flags, "false flags"),
    ):
        if n == 0:
            lines.append(f"{label}: none answered.")
        else:
            lines.append(f"{label}: {n}. {verb} {k}: {_rate(word, k, n)}")
    return lines


def _median(times: list) -> str:
    return measure.drill_seconds(measure.median_low(times))


def median_line(t: Tally, by_kind: bool) -> str:
    if not t.times:
        return "median time to decide: no answers"
    line = f"median time to decide: {_median(t.times)}"
    kinds = (("planted", t.planted_times), ("clean", t.clean_times))
    parts = [f"{name} {_median(times)}" for name, times in kinds if times]
    return f"{line} ({', '.join(parts)})" if by_kind else line


def _without(found: list[Answer], indent: str, you: bool) -> list[str]:
    return [indent + line for line in kind_lines(tally([a for a in found if not a.over]), you)]


def end_screen(record: Record) -> list[str]:
    """The end of a drill (section 5), from the ledger's entries for it."""
    found = answers(record)
    how = (record.ended or {}).get("how")
    head = "Drill finished" if how == "finished" else "Drill stopped"
    condition = CONDITION_WORDS.get(record.started.get("condition"), "?")
    lines = [
        "",
        f"{head}: {len(found)} of {record.started.get('calls')} calls answered ({condition}).",
    ]
    if not found:
        return [*lines, "", *CLOSING]
    t = tally(found)
    lines += ["", *kind_lines(t, you=True), median_line(t, by_kind=True)]
    missed = [
        f"call {a.n} ({scenarios.SHAPE_WORDS.get(a.shape, '?')})"
        for a in found
        if a.outcome == "missed"
    ]
    flagged = [f"call {a.n}" for a in found if a.outcome == "false-flag"]
    lines.append(f"missed: {', '.join(missed) or 'none'}")
    lines.append(f"false flags: {', '.join(flagged) or 'none'}")
    over = [a for a in found if a.over]
    if over:
        which = ", ".join(f"call {a.n}" for a in over)
        lines.append(f"over 300 s: {which}. Without {'it' if len(over) == 1 else 'them'}:")
        lines += _without(found, "  ", you=True)
    return [
        *lines,
        "",
        "One drill says little; the intervals show how little. Run polarizer drill report",
        "to see every drill so far.",
        "",
        *CLOSING,
    ]


def _date(record: Record) -> str:
    return record.ts[:10]


def _ended(record: Record) -> str:
    how = (record.ended or {}).get("how")
    return how if how in ("finished", "stopped", "interrupted") else "none"


def drill_line(record: Record) -> str:
    """A drill's line in the report: its counts and median."""
    found = answers(record)
    t = tally(found)
    ended = {"finished": "", "none": ", did not end"}.get(_ended(record), ", stopped")
    condition = CONDITION_WORDS.get(record.started.get("condition"), "?")
    over = sum(a.over for a in found)
    return (
        f"{_date(record)} {condition}, {len(found)} of {record.started.get('calls')}{ended}: "
        f"caught {t.caught} of {t.planted}, false flags {t.false_flags} of {t.clean}, "
        f"median {_median(t.times)}" + (f", {over} over 300 s" if over else "")
    )


def _answered(fold: DrillFold) -> list[tuple[Record, list[Answer]]]:
    return [(r, found) for r in fold.drills() if (found := answers(r))]


def _sets(versions: set) -> str:
    names = [str(v) for v in sorted(versions)]
    if len(names) == 1:
        return f"scenario set {names[0]}"
    return f"scenario sets {', '.join(names[:-1])} and {names[-1]}"


def _by_condition(drills) -> dict:
    grouped = {c: [] for c in CONDITIONS}
    for record, found in drills:
        if record.started.get("condition") in grouped:
            grouped[record.started["condition"]].append((record, found))
    return grouped


def report_lines(fold: DrillFold) -> list[str] | None:
    """`drill report`'s stdout (section 9), or None when no drill has an answer."""
    drills = _answered(fold)
    if not drills:
        return None
    pooled = [a for _, found in drills for a in found]
    finished = sum(_ended(r) == "finished" for r, _ in drills)
    versions = {r.started.get("set") for r, _ in drills}
    lines = [
        f"drills: {finished} finished, {len(drills) - finished} stopped early; "
        f"{len(pooled)} calls answered ({_sets(versions)})",
        f"calls seen in an earlier drill: {sum(a.seen_before > 0 for a in pooled)} of "
        f"{len(pooled)}",
        "",
    ]
    grouped = _by_condition(drills)
    tallies = {}
    for condition, members in grouped.items():
        if not members:
            continue
        found = [a for _, f in members for a in f]
        t = tallies[condition] = tally(found)
        word = "drill" if len(members) == 1 else "drills"
        lines.append(f"{CONDITION_WORDS[condition]}: {len(members)} {word}")
        lines += ["  " + line for line in kind_lines(t, you=False)]
        lines.append("  " + median_line(t, by_kind=False))
        over = sum(a.over for a in found)
        if over:
            lines.append(
                f"  over 300 s: {over} answer{'s' if over > 1 else ''}. "
                f"Without {'it' if over == 1 else 'them'}:"
            )
            lines += _without(found, "    ", you=False)
    lines.append("")
    if len(tallies) == 2:
        gate, plain = tallies["prediction-gate"], tallies["plain"]
        lines.append("prediction gate minus plain")
        for label, k1, n1, k2, n2 in (
            ("caught", gate.caught, gate.planted, plain.caught, plain.planted),
            ("false flags", gate.false_flags, gate.clean, plain.false_flags, plain.clean),
        ):
            parts = measure.difference_parts(k1, n1, k2, n2) if n1 and n2 else None
            if parts is None:
                lines.append(f"  {label}: {measure.TOO_FEW_TO_COMPARE}.")
                continue
            points, low, high, apart = parts
            phrase = measure.DISTINGUISHABLE if apart else measure.NOT_DISTINGUISHABLE
            lines.append(
                f"  {label}: {measure.signed(points)} points, 95% interval {measure.signed(low)} "
                f"to {measure.signed(high)}: {phrase}."
            )
    else:
        missing = "prediction-gate" if "plain" in tallies else "plain"
        lines.append(f"prediction gate minus plain: no {missing} drills yet")
    lines += ["", "by kind of planted call"]
    for shape in scenarios.SHAPES:
        planted = [a for a in pooled if a.answer == "planted" and a.shape == shape]
        caught = sum(a.outcome == "caught" for a in planted)
        lines.append(f"  {scenarios.SHAPE_WORDS[shape]}: {len(planted)} planted, {caught} caught")
    lines += ["", "each drill"]
    lines += ["  " + drill_line(r) for r, _ in drills]
    return [*lines, "", *CLOSING]


# The export (section 9, The export) ----------------------------------------------------------


def _r(k: int, n: int):
    parts = measure.rate_parts(k, n)
    return (
        None
        if parts is None
        else dict(zip(("percent", "low_percent", "high_percent"), parts, strict=True))
    )


def _d(k1: int, n1: int, k2: int, n2: int):
    parts = measure.difference_parts(k1, n1, k2, n2) if n1 and n2 else None
    keys = ("points", "low_points", "high_points", "distinguishable")
    return None if parts is None else dict(zip(keys, parts, strict=True))


def export_doc(fold: DrillFold) -> dict:
    """The anonymized summary: counts, rates, intervals, conditions, set versions, the version
    and dates no finer than the day. No text a person typed, no path, id, seed or time of day."""
    drills = _answered(fold)
    pooled = [a for _, found in drills for a in found]
    conditions, tallies = {}, {}
    for condition, members in _by_condition(drills).items():
        if not members:
            conditions[condition] = None
            continue
        found = [a for _, f in members for a in f]
        t = tallies[condition] = tally(found)
        within = tally([a for a in found if not a.over])
        conditions[condition] = {
            "drills": len(members),
            "planted": t.planted,
            "caught": t.caught,
            "clean": t.clean,
            "false_flags": t.false_flags,
            "catch": _r(t.caught, t.planted),
            "false_flag": _r(t.false_flags, t.clean),
            "median_ms": measure.median_low(t.times) if t.times else None,
            "over_300s": sum(a.over for a in found),
            "catch_within_300s": _r(within.caught, within.planted),
            "false_flag_within_300s": _r(within.false_flags, within.clean),
        }
    difference = {"catch": None, "false_flag": None}
    if len(tallies) == 2:
        gate, plain = tallies["prediction-gate"], tallies["plain"]
        difference = {
            "catch": _d(gate.caught, gate.planted, plain.caught, plain.planted),
            "false_flag": _d(gate.false_flags, gate.clean, plain.false_flags, plain.clean),
        }
    by_shape = {}
    for shape in scenarios.SHAPES:
        planted = [a for a in pooled if a.answer == "planted" and a.shape == shape]
        by_shape[shape] = {
            "planted": len(planted),
            "caught": sum(a.outcome == "caught" for a in planted),
        }
    per_drill = []
    for record, found in drills:
        t = tally(found)
        per_drill.append(
            {
                "date": _date(record),
                "condition": record.started.get("condition"),
                "calls": record.started.get("calls"),
                "answered": len(found),
                "ended": _ended(record),
                "planted": t.planted,
                "caught": t.caught,
                "clean": t.clean,
                "false_flags": t.false_flags,
                "median_ms": measure.median_low(t.times),
                "over_300s": sum(a.over for a in found),
            }
        )
    return {
        "format": "polarizer-drill-summary",
        "format_version": 1,
        "polarizer_version": polarizer.__version__,
        "scenario_sets": [str(v) for v in sorted({r.started.get("set") for r, _ in drills})],
        "first_date": _date(drills[0][0]),
        "last_date": _date(drills[-1][0]),
        "drills": len(drills),
        "answered": len(pooled),
        "repeats": sum(a.seen_before > 0 for a in pooled),
        "conditions": conditions,
        "difference": difference,
        "by_shape": by_shape,
        "per_drill": per_drill,
    }


# One call's screen ---------------------------------------------------------------------------


def arguments_bytes(arguments) -> bytes:
    """The arguments serialized exactly as a side file stores them (LEDGER-SPEC.md, Part 3)."""
    return json.dumps(arguments, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def parse_ts(ts: str) -> datetime:
    return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=UTC)


def call_block(
    scenario: dict,
    hold_id: str,
    args_commit: str,
    session: str,
    shown_ts: str,
    started_ts: str,
    timeout: int,
) -> list[str]:
    """The hold block of a drill call, from the function `polarizer holds` uses: shown as the
    hold is made, so waiting about 0m00s, in a running session."""
    call = scenario["call"]
    hold = holds.Hold(
        hold=hold_id,
        seq=0,
        ts=shown_ts,
        session=session,
        tool=call["tool"],
        args_commit=args_commit,
        cls=call["class"],
        class_from=call["class_from"],
        rule=call["rule"],
        reason=call["reason"],
        timeout_seconds=timeout,
    )
    size = len(arguments_bytes(call["arguments"]))
    return holds.render_block(
        hold, started_ts, lock.RUNNING, parse_ts(shown_ts), call["arguments"], size
    )


def reveal_lines(scenario: dict, outcome: str, over: bool, last: bool) -> list[str]:
    lines = ["", REVEALS[outcome]]
    if over:
        lines.append(OVER_LINE)
    lines += [f"why: {printable(scenario['why'])}", ""]
    return lines


def enter_line(last: bool) -> str:
    return "Press Enter to see the results." if last else "Press Enter for the next call."


def intro_lines(calls: int, directory: Path, condition: str, keep: bool) -> list[str]:
    lines = [
        "Polarizer drill: practice with held calls",
        "",
        f"You will see {calls} held calls, one at a time. Each shows the task the agent was",
        "given, then the call exactly as polarizer holds would show it. Answer allow or",
        "deny. Some calls are planted: they differ from the task in a way that should be",
        "denied. How many changes from drill to drill. After each answer you see whether",
        "the call was clean or planted, and why.",
        "",
        "Nothing here is real. There is no agent and no server, nothing is sent",
        "anywhere, and nothing times out. Your answers and times stay on this computer,",
        f"in {directory}.",
        "",
        *CLOSING,
        "",
    ]
    if condition == "plain":
        lines.append("This drill: plain. Type q at any prompt to stop.")
    elif keep:
        lines += [
            "This drill: prediction first. Before each call, type in a few words what you",
            "expect it to do; then you see the call. What you type is kept on this",
            "computer, and never exported. Type q at any prompt to stop.",
        ]
    else:
        lines += [
            "This drill: prediction first. Before each call, type in a few words what you",
            "expect it to do; then you see the call. Only the length of what you type is",
            "kept. Type q at any prompt to stop.",
        ]
    return lines


# The drill -----------------------------------------------------------------------------------


@dataclass
class Options:
    ledger_dir: Path
    calls: int = DEFAULT_CALLS
    condition: str | None = None
    seed: bytes | None = None
    keep_predictions: bool = False


class _Stop(Exception):
    def __init__(self, how: str):
        super().__init__(how)
        self.how = how


class _NotRecorded(Exception):
    pass


class _Drill:
    def __init__(self, opts: Options, ledger: writer.LedgerWriter, fold: DrillFold, set_, console):
        self.opts, self.ledger, self.fold = opts, ledger, fold
        self.set, self.console = set_, console
        self.answered = 0

    def append(self, kind: str, data: dict) -> None:
        try:
            self.ledger.append(kind, data).result()
        except writer.LedgerError:
            raise _NotRecorded() from None

    def show(self, text: str) -> None:
        self.console.write(text)
        self.console.flush()

    def read(self) -> str:
        """One line, stripped. The end of input, or Ctrl+C, stops the drill as interrupted."""
        try:
            line = self.console.readline()
        except KeyboardInterrupt:
            raise _Stop("interrupted") from None
        if line == "":
            raise _Stop("interrupted")
        return line.strip()

    def run(self) -> int:
        opts = self.opts
        self.session = random.token_hex(8)
        seed = opts.seed if opts.seed is not None else random.token_bytes(16)
        excluded = [] if opts.seed is not None else self.fold.recent_scenarios(2)
        drawn, _, order = plan(
            seed, self.set.ids("planted"), self.set.ids("clean"), opts.calls, set(excluded)
        )
        self.condition = opts.condition or drawn
        how = "finished"
        try:
            self.append(
                "drill.started",
                {
                    "session": self.session,
                    "polarizer_version": polarizer.__version__,
                    "set": self.set.version,
                    "set_sha256": self.set.sha256,
                    "seed": seed.hex(),
                    "seed_from": "flag" if opts.seed is not None else "random",
                    "condition": self.condition,
                    "condition_from": "flag" if opts.condition is not None else "random",
                    "calls": opts.calls,
                    "excluded": excluded,
                    "keep_predictions": opts.keep_predictions,
                },
            )
            self.started_ts = self.fold.records[self.session].ts
            try:
                lines = intro_lines(
                    opts.calls, opts.ledger_dir, self.condition, opts.keep_predictions
                )
                self.show("\n".join(lines) + "\nPress Enter to start.")
                self.wait_enter()
                for n, scenario_id in enumerate(order, 1):
                    self.call(n, scenario_id, len(order))
            except _Stop as e:
                how = e.how
            except KeyboardInterrupt:
                how = "interrupted"
            self.append(
                "drill.ended",
                {
                    "session": self.session,
                    "how": how,
                    "answered": self.answered,
                    "calls": opts.calls,
                },
            )
        except _NotRecorded:
            err(STOP_LINE)
            return 1
        self.show("\n".join(end_screen(self.fold.records[self.session])) + "\n")
        return 0

    def wait_enter(self) -> None:
        if self.read().lower() in ("q", "quit"):
            raise _Stop("stopped")

    def call(self, n: int, scenario_id: str, total: int) -> None:
        scenario = self.set.scenarios[scenario_id]
        hold_id, salt = random.token_hex(8), random.token_bytes(32)
        commit = hashlib.sha256(salt + arguments_bytes(scenario["call"]["arguments"])).hexdigest()
        seen = self.fold.shown_count[scenario_id]
        self.append(
            "drill.shown",
            {
                "session": self.session,
                "n": n,
                "scenario": scenario_id,
                "hold": hold_id,
                "args_commit": commit,
                "seen_before": seen,
            },
        )
        record = self.fold.records[self.session]
        block = call_block(
            scenario,
            hold_id,
            commit,
            self.session,
            record.shown_ts[n],
            self.started_ts,
            self.set.timeout_seconds,
        )
        head = f"\ncall {n} of {total}\ntask: {printable(scenario['task'])}\n"
        body = "\n".join(block) + "\n\nallow or deny? "
        if self.condition == "prediction-gate":
            self.predict(n, head)
            self.show("\n" + body)
        else:
            self.show(head + "\n" + body)
        decision, elapsed = self.decide()
        planted = scenario["answer"] == "planted"
        outcome = OUTCOMES[(planted, decision)]
        self.append(
            "drill.decided",
            {
                "session": self.session,
                "n": n,
                "scenario": scenario_id,
                "decision": decision,
                "elapsed_ms": elapsed,
                "actor": "person",
            },
        )
        self.append(
            "drill.revealed",
            {
                "session": self.session,
                "n": n,
                "scenario": scenario_id,
                "answer": scenario["answer"],
                "shape": scenario.get("shape"),
                "outcome": outcome,
            },
        )
        self.answered += 1
        last = n == total
        lines = reveal_lines(scenario, outcome, elapsed > OVER_MS, last)
        self.show("\n".join(lines) + "\n" + enter_line(last))
        self.wait_enter()

    def predict(self, n: int, head: str) -> None:
        prompt = "what do you expect this call to do? "
        self.show(head + prompt)
        start = monotonic_ns()
        while True:
            line = self.read()
            if line.lower() in ("q", "quit"):
                raise _Stop("stopped")
            if line:
                break
            self.show("type a few words, or q to stop\n" + prompt)
        elapsed = (monotonic_ns() - start) // 1_000_000
        kept = safe(line, PREDICTION_LIMIT) if self.opts.keep_predictions else None
        self.append(
            "drill.predicted",
            {
                "session": self.session,
                "n": n,
                "length": len(line),
                "prediction": kept,
                "elapsed_ms": elapsed,
            },
        )

    def decide(self) -> tuple[str, int]:
        start = monotonic_ns()
        while True:
            line = self.read().lower()
            if line in ("allow", "a"):
                decision = "allow"
                break
            if line in ("deny", "d"):
                decision = "deny"
                break
            if line in ("q", "quit"):
                raise _Stop("stopped")
            self.show("type allow or deny (a or d), or q to stop\nallow or deny? ")
        return decision, (monotonic_ns() - start) // 1_000_000


def run(opts: Options) -> int:
    """`polarizer drill` after its usage and terminal checks: the location, the scenario set,
    the ledger, then the drill. The set is loaded before the ledger is opened, so a set that
    fails its check writes nothing, not even a genesis entry."""
    directory = opts.ledger_dir
    try:
        warning = ledgerdir.check_location(
            directory, ledgerdir.always_forbidden(), ledgerdir.git_tree_by_files
        )
    except ledgerdir.ForbiddenPath as e:
        err(str(e))
        return 2
    if warning:
        err(warning)
    try:
        set_ = load_set()
    except scenarios.SetProblem as e:
        err(SET_PROBLEM.format(e))
        return 2
    fold = DrillFold()

    def listen(entry: dict) -> None:
        fold.apply(entry)
        if entry.get("kind") == "session.started":
            raise writer.LedgerError(SERVE_LEDGER.format(directory), 2)

    try:
        ledger = writer.LedgerWriter.open(directory, on_entry=listen)
    except writer.LedgerError as e:
        err(e.line)
        return e.exit_code
    except OSError as e:
        err(f"polarizer: cannot open {e.filename or directory}: {e.strerror}")
        return 2
    try:
        return _Drill(opts, ledger, fold, set_, make_console()).run()
    finally:
        ledger.close()


def report(directory: Path, export: str | None = None) -> int:
    """`polarizer drill report [--export <path>]`: reads the drill ledger as verify does and
    writes nothing but the export file, created exclusively."""
    try:
        data, head = read_files(directory)
    except Locked:
        print(LOCKED_LINE)
        return 7
    except OSError as e:
        err(f"polarizer: cannot read {e.filename or directory}: {e.strerror}")
        return 2
    fold = DrillFold()
    if data:
        result = verify_bytes(data, head, fold.apply)
        if result.status != "intact":
            for line in result.output_lines():
                print(line)
            return result.exit_code
    lines = report_lines(fold)
    if lines is None:
        if export is not None:
            err("polarizer: no drills to export")
            return 2
        print(f"drills: none yet in {directory}; run polarizer drill")
        return 0
    if export is not None:
        try:
            handle = open(export, "x", encoding="utf-8", newline="\n")
        except FileExistsError:
            err(f"polarizer: {export} already exists; nothing was written")
            return 2
        except OSError as e:
            err(f"polarizer: cannot write {export}: {e.strerror}")
            return 2
        with handle:
            handle.write(json.dumps(export_doc(fold), sort_keys=True, separators=(",", ":")) + "\n")
    for line in lines:
        print(line)
    return 0
