"""In-process drills for the tests: a scripted console, an injected monotonic clock, fixed ids
and salts, a fixed ledger timestamp and the small test scenario set
(tests/helpers/drill_scenarios.json, with its plain words in drill_scenarios-plain.json), so
every byte a drill prints is the same on every run (docs/MEASURE-SPEC.md, section 5).

The console records what the drill writes between two reads as one segment, so a test can take
the intro, one call's screen or one reveal on its own. Typed input is not echoed.
"""

import json
from dataclasses import dataclass, field
from pathlib import Path

from polarizer import cli, drill, scenarios, writer

HERE = Path(__file__).resolve().parent
TEST_SET = scenarios.parse(
    (HERE / "drill_scenarios.json").read_bytes(),
    1,
    (HERE / "drill_scenarios-plain.json").read_bytes(),
)
TS = "2026-10-06T18:02:11.425Z"
# The first seed whose plan matches section 5's example end screen: the plain condition, 8
# planted calls of 20 (drawn from 6 to 10), the README scenario at call 3, a look-alike at call
# 7, a clean call at call 12.
GOLDEN_SEED = "000000000000000000000000000003ea"
SESSION = "8e41c6b2d09a7f35"
# Planted and clean decision times (ms) of the example: medians 18.0 s and 10.9 s, overall
# 12.4 s (median_low of 8, 12 and 20 values).
PLANTED_MS = [9500, 11000, 12000, 18000, 19000, 25000, 30000, 31000]
CLEAN_MS = [7000, 7500, 8000, 8500, 9000, 10900, 12400, 13000, 14000, 20000, 21000, 22000]

EOF_ = object()  # an input that is the end of input
INTERRUPT = object()  # an input that is Ctrl+C


class Clock:
    def __init__(self):
        self.ns = 10**12

    def __call__(self) -> int:
        return self.ns


@dataclass
class Console:
    """Scripted input: each item is a line (str), (line, ms) to advance the clock by ms before
    the line is read, EOF_ or INTERRUPT. Reading past the script is the end of input."""

    inputs: list
    clock: Clock
    segments: list = field(default_factory=lambda: [[]])

    def write(self, text: str) -> None:
        self.segments[-1].append(text)

    def flush(self) -> None:
        pass

    def readline(self) -> str:
        self.segments.append([])
        if not self.inputs:
            return ""
        item = self.inputs.pop(0)
        if item is EOF_:
            return ""
        if item is INTERRUPT:
            raise KeyboardInterrupt
        line, ms = item if isinstance(item, tuple) else (item, 0)
        self.clock.ns += ms * 1_000_000
        return line + "\n"

    def text(self, i: int | None = None) -> str:
        if i is None:
            return "".join("".join(s) for s in self.segments)
        return "".join(self.segments[i])


class Random:
    """token_hex: the session id first, then hold ids 0000000000000001, ...; token_bytes: a
    seed of 16 bytes 0xaa, salts of 32 bytes counting from 1."""

    def __init__(self, session: str = SESSION):
        self.hexes = [session]
        self.count = 0
        self.salts = 0

    def token_hex(self, n: int) -> str:
        if self.hexes:
            return self.hexes.pop(0)
        self.count += 1
        return f"{self.count:0{2 * n}x}"

    def token_bytes(self, n: int) -> bytes:
        if n == 16:
            return b"\xaa" * 16
        self.salts += 1
        return bytes([self.salts % 256]) * n


def install(monkeypatch, the_set=TEST_SET, inputs=(), ts=TS, session=SESSION):
    """Patch the drill's seams; returns the console."""
    clock = Clock()
    console = Console(inputs if isinstance(inputs, list) else list(inputs), clock)
    monkeypatch.setattr(drill, "monotonic_ns", clock)
    monkeypatch.setattr(drill, "random", Random(session))
    monkeypatch.setattr(drill, "make_console", lambda: console)
    monkeypatch.setattr(drill, "load_set", lambda: the_set)
    monkeypatch.setattr(drill, "is_terminal", lambda: True)
    monkeypatch.setattr(writer, "now_ts", (lambda: ts) if isinstance(ts, str) else ts)
    return console


def run(monkeypatch, directory: Path, inputs=(), *args, the_set=TEST_SET, ts=TS, session=SESSION):
    """`polarizer drill --ledger-dir <directory> <args>` in process. Returns (exit, console)."""
    console = install(monkeypatch, the_set, inputs, ts, session)
    code = cli.main(["drill", "--ledger-dir", str(directory), *args])
    return code, console


def entries(directory: Path) -> list[dict]:
    path = Path(directory) / "ledger.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def golden_plan(the_set=TEST_SET, seed: str = GOLDEN_SEED, calls: int = 20):
    return drill.plan(bytes.fromhex(seed), the_set.shapes(), the_set.ids("clean"), calls, set())


def golden_answers(the_set=TEST_SET, prediction=None) -> list:
    """The example's inputs: Enter to start, then each call answered rightly except call 7
    (allowed: missed) and call 12 (denied: a false flag), with the example's times."""
    _, _, order = golden_plan(the_set)
    planted_ms, clean_ms = iter(PLANTED_MS), iter(CLEAN_MS)
    inputs = [""]
    for n, scenario_id in enumerate(order, 1):
        planted = the_set.scenarios[scenario_id]["answer"] == "planted"
        right = "d" if planted else "a"
        wrong = "a" if planted else "d"
        if prediction is not None:
            inputs.append((prediction, 2000))
        inputs.append((wrong if n in (7, 12) else right, next(planted_ms if planted else clean_ms)))
        inputs.append("")
    return inputs
