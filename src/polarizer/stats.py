"""`polarizer stats`: what a serve ledger's holds came to (docs/MEASURE-SPEC.md, section 10).

Read-only, like `holds`: the ledger is read as `verify` reads it, folded with the hold fold
(holds.HoldState, so each hold's ending is the one `holds`, `allow` and `deny` see), and
nothing is written. It never changes policy; its suggestions are lines of text. Every number is
printed with its count, a rate never without its interval (measure.py), and the thresholds that
are guesses say so on the line that uses them.

Time to decision crosses processes (`serve` holds, `allow` decides), so it is the wall-clock
difference of `hold.created` and `hold.decided`, in whole seconds, 0 when the clock stepped
back, and labelled "about". A `hold.decided` that carries a monotonic `elapsed_ms` (M3's card,
which shows the hold and takes the decision in one process) is preferred when present.

M2b (docs/TAINT-SPEC.md, section 9): when any session is tainted, a taint section counts the
tainted sessions and the holds taint caused (rule tainted), which never get a suggestion.
"""

import sys
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from polarizer import holds, measure, paths
from polarizer.ledger import LEDGER, LOCKED_LINE, Locked, read_files, verify_bytes
from polarizer.text import printable

SUGGEST_MIN = 20  # decided holds, all allowed, before a suggestion line; provisional
WINDOW = 40  # decided holds in each of the trend's two windows; provisional
TREND_FLOOR_SECONDS = 6  # the earlier window's median must be at least this; provisional
STATES = ("allowed", "denied", "expired", "abandoned", "open")
DECIDED = ("allowed", "denied")
TS_FORMAT = "%Y-%m-%dT%H:%M:%S.%fZ"
PATTERN_RULES = ("write-pattern", "read-pattern")
SUGGESTED_RULES = ("unclassified", "outside-roots", "write-unchecked", "destructive", "egress")
NO_SUGGESTION = (
    f"suggestions: none (a tool and rule needs {SUGGEST_MIN} decided holds, all allowed; "
    f"{SUGGEST_MIN} is provisional)"
)
SUGGESTIONS_HEAD = (
    f"suggestions, from a provisional threshold of {SUGGEST_MIN} decided holds, all allowed:"
)
WALL_CLOCK_NOTE = (
    "  Times are wall-clock differences in whole seconds, so each can be off by about",
    "  a second, and they include the time before a hold was noticed.",
)
TREND_HEAD = (
    f"trend, with provisional thresholds ({WINDOW} and {WINDOW} decisions, half the time, "
    f"{TREND_FLOOR_SECONDS} s)"
)
TREND_WARNING = (
    "  Decisions got faster while the approval rate did not fall. That can mean less",
    "  reading, or calls that became easier to judge; a drill can tell the two apart.",
)


@dataclass(frozen=True)
class Decision:
    """What stats keeps of a hold.decided entry, by its seq."""

    ts: str | None
    elapsed_ms: int | None


class StatsFold:
    """The hold fold, plus what it drops and stats needs: each session's order of arrival and
    each hold.decided's ts and optional elapsed_ms. Kinds it doesn't know are ignored."""

    def __init__(self):
        self.holds = holds.HoldState()
        self.sessions: dict[str, None] = {}  # in session.started order, as a dict's keys
        self.decisions: dict[int, Decision] = {}  # seq of hold.decided -> Decision
        self.planting: dict[str, int] = {}  # session -> planted_calls_per_session, when above 0

    def apply(self, entry: dict) -> None:
        self.holds.apply(entry)
        kind, data = entry.get("kind"), entry.get("data")
        if not isinstance(data, dict):
            return
        if kind == "session.started":
            session = data.get("session")
            if isinstance(session, str):
                self.sessions.setdefault(session)
        elif kind == "policy.loaded":
            session, n = data.get("session"), data.get("planted_calls_per_session")
            if isinstance(session, str) and type(n) is int and n > 0:
                self.planting.setdefault(session, n)
        elif kind == "hold.decided" and type(entry.get("seq")) is int:
            ms = data.get("elapsed_ms")
            ts = entry.get("ts")
            self.decisions[entry["seq"]] = Decision(
                ts if isinstance(ts, str) else None,
                ms if type(ms) is int and ms >= 0 else None,
            )


@dataclass(frozen=True)
class Counted:
    """One hold as stats counts it."""

    hold: holds.Hold
    state: str  # one of STATES
    decided_seq: int | None  # the hold.decided's seq, for allowed and denied
    seconds: int | None  # time to decision, when it has one
    monotonic: bool  # the time came from elapsed_ms


def parse_ts(ts: str | None) -> datetime | None:
    try:
        return datetime.strptime(ts, TS_FORMAT)
    except (TypeError, ValueError):
        return None


def seconds_between(created: str | None, decided: str | None) -> int | None:
    """The wall-clock difference in whole seconds, a half rounding up, 0 when negative; None
    when either ts doesn't parse."""
    a, b = parse_ts(created), parse_ts(decided)
    if a is None or b is None:
        return None
    ms = (b - a) // timedelta(milliseconds=1)
    return max(round_ms(ms), 0)


def round_ms(ms: int) -> int:
    """Milliseconds to whole seconds, a half rounding up."""
    return (ms + 500) // 1000


@dataclass
class Planted:
    """The holds of planting sessions that are out of every other count (section 11)."""

    planted: list  # Counted, revealed as planted, with a reveal that checks
    not_revealed: int = 0
    did_not_check: int = 0


def split(fold: StatsFold) -> tuple[list[Counted], Planted]:
    """(the holds every other count uses, the planted section's). A hold of a session with
    planting on counts only once revealed as real, with a reveal that checks; revealed as
    planted, it goes to the planted section; unrevealed or not checking, it is counted there as
    such and left out of every rate."""
    real, section = [], Planted([])
    for c in count(fold):
        if c.hold.session not in fold.planting:
            real.append(c)
            continue
        reveal = fold.holds.revealed(c.hold.hold)
        if reveal is None:
            section.not_revealed += 1
        elif not holds.reveal_checks(c.hold, reveal):
            section.did_not_check += 1
        elif reveal.label == "planted":
            section.planted.append(c)
        else:
            real.append(c)
    return real, section


def count(fold: StatsFold) -> list[Counted]:
    """Every hold, in seq order, with its state and time."""
    found = []
    for hold in sorted(fold.holds.snapshot().values(), key=lambda h: h.seq):
        ending = hold.ending
        if ending is None:
            found.append(Counted(hold, "open", None, None, False))
            continue
        if ending.kind == "hold.expired":
            found.append(Counted(hold, "expired", None, None, False))
            continue
        if ending.kind == "hold.abandoned":
            found.append(Counted(hold, "abandoned", None, None, False))
            continue
        state = "allowed" if ending.decision == "allow" else "denied"
        decision = fold.decisions.get(ending.seq)
        seconds, monotonic = None, False
        if decision is not None:
            if decision.elapsed_ms is not None:
                seconds, monotonic = round_ms(decision.elapsed_ms), True
            else:
                seconds = seconds_between(hold.ts, decision.ts)
        found.append(Counted(hold, state, ending.seq, seconds, monotonic))
    return found


# Lines ------------------------------------------------------------------------------------


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def _counts_line(counted: list[Counted]) -> str:
    by_state = {state: sum(1 for c in counted if c.state == state) for state in STATES}
    rest = ", ".join(f"{state} {by_state[state]}" for state in STATES)
    return f"  holds {len(counted)}: {rest}"


def _rate(k: int, n: int) -> str:
    parts = measure.rate_parts(k, n)
    if parts is None:
        return measure.TOO_FEW
    return f"{parts[0]}% (95% interval {parts[1]}% to {parts[2]}%)"


def _approval_line(counted: list[Counted]) -> str:
    decided = [c for c in counted if c.state in DECIDED]
    if not decided:
        return "  approval: no decisions"
    allowed = sum(1 for c in decided if c.state == "allowed")
    rate = _rate(allowed, len(decided))
    return f"  approval: allowed {allowed} of {len(decided)} decided, {rate}"


def _times(counted: list[Counted]) -> list[int]:
    return [c.seconds for c in counted if c.state in DECIDED and c.seconds is not None]


def _time_line(counted: list[Counted]) -> str:
    times = _times(counted)
    if not times:
        decided = sum(1 for c in counted if c.state in DECIDED)
        if not decided:
            return "  time to decision: no decisions"
        if decided == 1:
            return "  time to decision: the 1 decision has no time"
        return f"  time to decision: none of the {decided} decisions has a time"
    median = measure.stats_seconds(measure.median_low(times))
    return f"  time to decision: median about {median}, from {_plural(len(times), 'decision')}"


def _block(counted: list[Counted]) -> list[str]:
    return [_counts_line(counted), _approval_line(counted), _time_line(counted)]


def _session_blocks(fold: StatsFold, counted: list[Counted]) -> list[list[str]]:
    """One block per session with holds, in session.started order; a session with holds and no
    session.started (a hand-edited ledger) comes after them, in order of its first hold. The
    holds are grouped once, so the cost grows with the ledger, not with sessions squared."""
    by_session: dict[str, list[Counted]] = {}  # in order of each session's first hold
    for c in counted:
        by_session.setdefault(c.hold.session, []).append(c)
    order = {s: None for s in fold.sessions if s in by_session}
    order.update((s, None) for s in by_session if s not in order)
    blocks = []
    for session in order:
        started = fold.holds.session_started(session)
        started_text = printable(started) if started is not None else "unknown"
        head = f"session {printable(session)} started {started_text}"
        blocks.append([head, *_block(by_session[session])])
    return blocks


def _all_block(counted: list[Counted]) -> list[str]:
    lines_ = ["all sessions", *_block(counted)]
    timed = [c for c in counted if c.state in DECIDED and c.seconds is not None]
    monotonic = sum(1 for c in timed if c.monotonic)
    if len(timed) > monotonic:
        lines_.extend(WALL_CLOCK_NOTE)
    if monotonic:
        lines_.append(
            f"  {monotonic} of {_plural(len(timed), 'decision')} were timed on one clock, "
            "from the card that showed the hold."
        )
    return lines_


def _pairs(counted: list[Counted]) -> list[tuple[tuple[str, str], list[Counted]]]:
    """(tool, rule) -> its holds, most holds first, then by tool, then by rule."""
    groups: dict[tuple[str, str], list[Counted]] = {}
    for c in counted:
        groups.setdefault((c.hold.tool, c.hold.rule), []).append(c)
    return sorted(groups.items(), key=lambda item: (-len(item[1]), item[0][0], item[0][1]))


def _pair_line(pair: tuple[str, str], group: list[Counted]) -> str:
    tool, rule = pair
    decided = [c for c in group if c.state in DECIDED]
    allowed = sum(1 for c in decided if c.state == "allowed")
    head = f"  {printable(tool)} {printable(rule)}: "
    head += f"allowed {allowed} of {len(decided)} decided" if decided else "none decided"
    others = []
    for state in ("expired", "abandoned", "open"):
        n = sum(1 for c in group if c.state == state)
        if n:
            others.append(f"{n} {state}")
    return head + ("; " + ", ".join(others) if others else "")


def pattern_of(reason: str) -> str | None:
    """The pattern a write-pattern or read-pattern reason names, after its last `matches `."""
    marker = " matches "
    at = reason.rfind(marker)
    if at < 0:
        return None
    return reason[at + len(marker) :]


def is_builtin(rule: str, pattern: str) -> bool:
    built_in = paths.BUILTIN_WRITE if rule == "write-pattern" else paths.BUILTIN_READ
    return pattern in built_in


def suggestion(tool: str, rule: str, k: int, pattern: str | None = None) -> str | None:
    """The line for a tool and rule allowed k of k times, or None for a rule that never gets
    one: polarizer-files, the path-* rules, a pattern rule with no pattern, and any other."""
    tool = printable(tool)
    times = f"was allowed {k} of {k} times"
    if rule == "unclassified":
        return f"{tool} has no class and {times}; consider giving it a class in polarizer.toml."
    if rule == "outside-roots":
        return (
            f"{tool} held by outside-roots {times}; consider whether the paths it writes "
            "belong inside a workspace root."
        )
    if rule == "write-unchecked":
        return f"{tool} has no path_args and {times}; consider naming its path arguments."
    if rule in ("destructive", "egress"):
        return f"{tool} held by {rule} {times}; consider whether it needs a hold on every call."
    if rule in PATTERN_RULES and pattern is not None:
        return (
            f"{tool} held by {rule} ({printable(pattern)}) {times}; consider whether that "
            "pattern still needs a hold."
        )
    return None


def suggestions(counted: list[Counted]) -> list[str]:
    """One line per tool and rule with at least SUGGEST_MIN decided holds, every one allowed.
    A pattern rule is counted per pattern the reasons name, since the line asks about that
    pattern. Built-in patterns, polarizer-files and the path-* rules fail closed by design and
    never get one."""
    found = []
    for (tool, rule), group in _pairs(counted):
        decided = [c for c in group if c.state in DECIDED]
        if rule in PATTERN_RULES:
            by_pattern: dict[str, list[Counted]] = {}
            for c in decided:
                pattern = pattern_of(c.hold.reason)
                if pattern is not None:
                    by_pattern.setdefault(pattern, []).append(c)
            for pattern, held in by_pattern.items():
                if is_builtin(rule, pattern):
                    continue
                if len(held) >= SUGGEST_MIN and all(c.state == "allowed" for c in held):
                    found.append(suggestion(tool, rule, len(held), pattern))
        elif rule in SUGGESTED_RULES:
            if len(decided) >= SUGGEST_MIN and all(c.state == "allowed" for c in decided):
                found.append(suggestion(tool, rule, len(decided)))
    return [line for line in found if line is not None]


def _window_line(label: str, window: list[Counted]) -> str:
    allowed = sum(1 for c in window if c.state == "allowed")
    times = [c.seconds for c in window if c.seconds is not None]
    median = measure.stats_seconds(measure.median_low(times)) if times else "none"
    return f"  {label}: median about {median}, allowed {allowed} of {len(window)}"


def trend(counted: list[Counted]) -> list[str]:
    """The warning sign: decisions getting faster while the approval rate stays flat, over the
    last WINDOW decided holds against the WINDOW before them, by the seq of their hold.decided.
    It fires when the earlier median is at least TREND_FLOOR_SECONDS, the recent median is at
    most half of it, and Newcombe's interval for recent minus earlier has high >= 0."""
    decided = sorted((c for c in counted if c.state in DECIDED), key=lambda c: c.decided_seq)
    if len(decided) < 2 * WINDOW:
        return [
            f"trend: needs {2 * WINDOW} decided holds to compare the last {WINDOW} with the "
            f"{WINDOW} before (provisional numbers); this ledger has {len(decided)}"
        ]
    recent, earlier = decided[-WINDOW:], decided[-2 * WINDOW : -WINDOW]
    out = [
        TREND_HEAD,
        _window_line(f"last {WINDOW} decisions", recent),
        _window_line(f"the {WINDOW} before", earlier),
    ]
    recent_times = [c.seconds for c in recent if c.seconds is not None]
    earlier_times = [c.seconds for c in earlier if c.seconds is not None]
    if not recent_times or not earlier_times:
        return out
    recent_median = measure.median_low(recent_times)
    earlier_median = measure.median_low(earlier_times)
    recent_allowed = sum(1 for c in recent if c.state == "allowed")
    earlier_allowed = sum(1 for c in earlier if c.state == "allowed")
    _, high = measure.newcombe(recent_allowed, len(recent), earlier_allowed, len(earlier))
    if earlier_median >= TREND_FLOOR_SECONDS and 2 * recent_median <= earlier_median and high >= 0:
        out.extend(TREND_WARNING)
    return out


def planted_lines(fold: StatsFold, section: Planted) -> list[str]:
    """The planted calls section (section 11, In stats), when any session had planting on."""
    limits = sorted(set(fold.planting.values()))
    most = str(limits[0]) if len(limits) == 1 else f"{limits[0]} to {limits[-1]}"
    states = {state: sum(1 for c in section.planted if c.state == state) for state in STATES}
    caught, missed = states["denied"], states["allowed"]
    second = (
        f"  planted {len(section.planted)}: denied {caught} (caught), allowed {missed} (missed), "
        f"expired {states['expired']}, not revealed {section.not_revealed}"
    )
    if section.did_not_check:
        second += f", did not check {section.did_not_check}"
    decided = caught + missed
    if decided:
        catch = f"  catch rate: caught {caught} of {decided}, {_rate(caught, decided)}"
    else:
        catch = "  catch rate: none decided"
    return [
        "planted calls",
        f"  on in {len(fold.planting)} of {len(fold.sessions)} sessions: at most {most} per "
        "session, about 1 in 20 eligible calls, never with 12 or more holds open",
        second,
        catch,
    ]


def taint_lines(fold: StatsFold, counted: list[Counted]) -> list[str]:
    """The taint section (TAINT-SPEC.md, section 9), or nothing when no session is tainted."""
    tainted = fold.holds.tainted_sessions() & set(fold.sessions)
    if not tainted:
        return []
    by_taint = [c for c in counted if c.hold.rule == "tainted"]
    rest = _counts_line(by_taint).replace("  holds ", "  held by taint ", 1)
    return [
        "taint",
        f"  tainted: {len(tainted)} of {len(fold.sessions)} sessions, every egress call held "
        "from then on",
        rest,
    ]


def lines(fold: StatsFold) -> list[str]:
    """`stats`' stdout for an intact ledger."""
    counted, section = split(fold)
    if not counted and not fold.planting:
        return ["stats: no holds in this ledger"]
    with_holds = {c.hold.session for c in counted}
    without = sum(1 for s in fold.sessions if s not in with_holds)
    out = [
        f"stats: {_plural(len(counted), 'hold')}; "
        f"{_plural(len(with_holds), 'session')} with holds, {without} without"
    ]
    for block in _session_blocks(fold, counted):
        out.append("")
        out.extend(block)
    out.append("")
    out.extend(_all_block(counted))
    if fold.planting:
        out.append("")
        out.extend(planted_lines(fold, section))
    taint = taint_lines(fold, counted)
    if taint:
        out.append("")
        out.extend(taint)
    out.append("")
    out.append("by tool and rule")
    out.extend(_pair_line(pair, group) for pair, group in _pairs(counted))
    out.append("")
    suggested = suggestions(counted)
    if suggested:
        out.append(SUGGESTIONS_HEAD)
        out.extend(f"  {line}" for line in suggested)
    else:
        out.append(NO_SUGGESTION)
    out.append("")
    out.extend(trend(counted))
    return out


def run(ledger_dir: Path) -> int:
    """`polarizer stats`: read-only, with `holds`' reading, status handling and exit codes."""
    try:
        data, head = read_files(ledger_dir)
    except Locked:
        print(LOCKED_LINE)
        return 7
    except OSError as e:
        where = e.filename or ledger_dir / LEDGER
        print(f"polarizer: cannot read {where}: {e.strerror}", file=sys.stderr)
        return 2
    if not data:
        print(f"no ledger at {ledger_dir}")
        return 2
    fold = StatsFold()
    result = verify_bytes(data, head, fold.apply)
    if result.status != "intact":
        for line in result.output_lines():
            print(line)
        return result.exit_code
    for line in lines(fold):
        print(line)
    return 0
