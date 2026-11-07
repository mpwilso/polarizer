"""`polarizer stats` (docs/MEASURE-SPEC.md, section 10, and the test table in section 12): the
fixture of section 10 against its exact expected output, the rounding of time to decision, the
approval rate over decided holds only, the suggestion threshold and its exclusions, the trend
windows, that other kinds change nothing, that it is read-only, and every `stats` row of
section 9's golden table but the stage 10 one."""

import json
import time
from pathlib import Path

import pytest
from conftest import install_fixture
from helpers import statsledger as sl
from test_drill import check_golden, listing

from polarizer import cli, stats
from polarizer.ledger import LOCK, read_files, verify_bytes
from polarizer.lock import LedgerLock

SPEC_MIXED = """stats: 9 holds; 3 sessions with holds, 1 without

session a1a1a1a1a1a1a1a1 started 2026-10-06T09:59:59.000Z
  holds 5: allowed 3, denied 1, expired 1, abandoned 0, open 0
  approval: allowed 3 of 4 decided, too few to say a rate (5 or more needed)
  time to decision: median about 20 s, from 4 decisions

session b2b2b2b2b2b2b2b2 started 2026-10-06T10:59:59.000Z
  holds 3: allowed 2, denied 0, expired 0, abandoned 1, open 0
  approval: allowed 2 of 2 decided, too few to say a rate (5 or more needed)
  time to decision: median about 0 s, from 2 decisions

session c3c3c3c3c3c3c3c3 started 2026-10-06T11:59:59.000Z
  holds 1: allowed 0, denied 0, expired 0, abandoned 0, open 1
  approval: no decisions
  time to decision: no decisions

all sessions
  holds 9: allowed 5, denied 1, expired 1, abandoned 1, open 1
  approval: allowed 5 of 6 decided, 83% (95% interval 43% to 97%)
  time to decision: median about 10 s, from 6 decisions
  Times are wall-clock differences in whole seconds, so each can be off by about
  a second, and they include the time before a hold was noticed.

by tool and rule
  fs__write_file outside-roots: allowed 4 of 4 decided; 1 open
  fs__move_file destructive: allowed 1 of 2 decided
  fs__edit_file write-pattern: none decided; 1 abandoned
  web__post_comment egress: none decided; 1 expired

suggestions: none (a tool and rule needs 20 decided holds, all allowed; 20 is provisional)

trend: needs 80 decided holds to compare the last 40 with the 40 before (provisional numbers); this ledger has 6
"""

SPEC_WARNING = """trend, with provisional thresholds (40 and 40 decisions, half the time, 6 s)
  last 40 decisions: median about 4 s, allowed 37 of 40
  the 40 before: median about 19 s, allowed 36 of 40
  Decisions got faster while the approval rate did not fall. That can mean less
  reading, or calls that became easier to judge; a drill can tell the two apart.
"""

BUILD_PATTERN = 'argument "path": /home/me/proj/build/out.txt matches build/**'


def run(directory: Path, capsys) -> tuple[int, str, str]:
    code = cli.main(["stats", "--ledger-dir", str(directory)])
    out, err = capsys.readouterr()
    return code, out, err


def section(out: str, head: str) -> str:
    """The lines of one section of the output, from its head to the next blank line."""
    lines = out.split("\n")
    start = next(i for i, line in enumerate(lines) if line.startswith(head))
    end = next((i for i in range(start, len(lines)) if lines[i] == ""), len(lines))
    return "\n".join(lines[start:end]) + "\n"


# The fixture ------------------------------------------------------------------------------


def test_stats_fixture(tmp_path, capsys):
    """The fixture of section 10 prints stats_mixed.txt exactly, and that is the spec's text."""
    directory = sl.mixed(tmp_path / "ledger")
    code, out, err = run(directory, capsys)
    assert (code, err) == (0, "")
    assert out == SPEC_MIXED
    check_golden("stats_mixed.txt", out)


def test_time_to_decision_rounding():
    at = "2026-10-06T10:00:00.000Z"
    assert stats.seconds_between(at, "2026-10-06T10:00:10.400Z") == 10
    assert stats.seconds_between(at, "2026-10-06T10:00:19.600Z") == 20
    assert stats.seconds_between(at, "2026-10-06T10:00:40.500Z") == 41
    assert stats.seconds_between("2026-10-06T10:00:00.900Z", "2026-10-06T10:00:00.500Z") == 0
    assert stats.seconds_between(at, "not a time") is None
    assert stats.round_ms(10400) == 10 and stats.round_ms(40500) == 41


def test_approval_rate_counts_decisions_only(tmp_path, capsys):
    """Expired, abandoned and open holds are in the counts and out of the rate."""
    specs = [sl.started(sl.A1, "09:59:59.000"), sl.started(sl.B2, "10:59:59.000")]
    decisions = ["allow"] * 5 + ["deny"] * 2
    specs += sl.run_of(7, "fs__write_file", "outside-roots", sl.OUTSIDE, decisions, 10)
    specs += [
        sl.created(sl.A1, "00000000000000a1", "web__post_comment", "egress", sl.EGRESS,
                   "10:20:00.000", cls="egress"),
        sl.expired(sl.A1, "00000000000000a1", "10:25:00.000"),
        sl.created(sl.A1, "00000000000000a2", "fs__write_file", "outside-roots", sl.OUTSIDE,
                   "10:21:00.000"),
        sl.abandoned(sl.B2, "00000000000000a2", sl.A1, "11:00:00.000"),
        sl.created(sl.B2, "00000000000000a3", "fs__write_file", "outside-roots", sl.OUTSIDE,
                   "11:01:00.000"),
    ]  # fmt: skip
    directory = sl.build(tmp_path / "ledger", specs)
    code, out, _ = run(directory, capsys)
    assert code == 0
    assert section(out, "all sessions") == (
        "all sessions\n"
        "  holds 10: allowed 5, denied 2, expired 1, abandoned 1, open 1\n"
        "  approval: allowed 5 of 7 decided, 71% (95% interval 35% to 92%)\n"
        "  time to decision: median about 10 s, from 7 decisions\n"
        "  Times are wall-clock differences in whole seconds, so each can be off by about\n"
        "  a second, and they include the time before a hold was noticed.\n"
    )
    assert "fs__write_file outside-roots: allowed 5 of 7 decided; 1 abandoned, 1 open\n" in out
    assert "web__post_comment egress: none decided; 1 expired\n" in out


# Suggestions -----------------------------------------------------------------------------


def suggestions_of(directory: Path, capsys) -> str:
    code, out, _ = run(directory, capsys)
    assert code == 0
    return section(out, "suggestions")


NONE = (
    "suggestions: none (a tool and rule needs 20 decided holds, all allowed; 20 is provisional)\n"
)


def test_suggestion_threshold(tmp_path, capsys):
    """20 of 20 decided and allowed gives the rule's line; 19 of 19, or 20 of 21, gives none; a
    built-in pattern, polarizer-files and the path-* rules never give one, at any count."""
    d = tmp_path / "a"
    assert suggestions_of(sl.allowed_run(d, 20), capsys) == (
        "suggestions, from a provisional threshold of 20 decided holds, all allowed:\n"
        "  fs__write_file held by outside-roots was allowed 20 of 20 times; consider whether "
        "the paths it writes belong inside a workspace root.\n"
    )
    assert suggestions_of(sl.allowed_run(tmp_path / "b", 19), capsys) == NONE
    assert suggestions_of(sl.allowed_run(tmp_path / "c", 21, denied=1), capsys) == NONE
    for i, (rule, reason) in enumerate(
        [
            ("write-pattern", sl.HOOKS),
            ("read-pattern", 'argument "path": /home/me/.ssh/id_ed25519 matches ~/.ssh/**'),
            ("polarizer-files", 'argument "path": /home/me/.local/share/polarizer/ledger.jsonl '
                                "is inside Polarizer's ledger directory"),
            ("path-missing", 'argument "path" is missing'),
            ("path-not-string", 'argument "path" is not a string or a list of strings'),
            ("path-unresolvable", 'argument "path": the path is not absolute'),
            ("write-pattern", "no pattern named here"),
        ]
    ):  # fmt: skip
        d = sl.allowed_run(tmp_path / f"x{i}", 60, rule=rule, reason=reason)
        assert suggestions_of(d, capsys) == NONE, rule


def test_each_rule_has_its_line():
    assert stats.suggestion("probe__wait", "unclassified", 20) == (
        "probe__wait has no class and was allowed 20 of 20 times; consider giving it a class in "
        "polarizer.toml."
    )
    assert stats.suggestion("fs__write_file", "write-unchecked", 20) == (
        "fs__write_file has no path_args and was allowed 20 of 20 times; consider naming its "
        "path arguments."
    )
    assert stats.suggestion("fs__move_file", "destructive", 21) == (
        "fs__move_file held by destructive was allowed 21 of 21 times; consider whether it needs "
        "a hold on every call."
    )
    assert stats.suggestion("web__post_comment", "egress", 20) == (
        "web__post_comment held by egress was allowed 20 of 20 times; consider whether it needs "
        "a hold on every call."
    )
    assert stats.suggestion("fs__read_file", "read-pattern", 20, "secrets/**") == (
        "fs__read_file held by read-pattern (secrets/**) was allowed 20 of 20 times; consider "
        "whether that pattern still needs a hold."
    )
    assert stats.suggestion("fs__write_file", "polarizer-files", 50) is None
    assert stats.suggestion("fs__write_file", "write-pattern", 50) is None
    assert stats.suggestion("fs__write_file", "made-up", 50) is None


def suggestion_ledger(directory: Path) -> Path:
    """stats_suggestion.txt: 20 allowed holds of fs__write_file by a pattern from the config,
    build/**, and 20 of fs__edit_file by the built-in .git/hooks/**, which gets no line."""
    specs = [sl.started(sl.A1, "09:59:59.000")]
    specs += sl.run_of(20, "fs__write_file", "write-pattern", BUILD_PATTERN, "allow", 10)
    specs += sl.run_of(20, "fs__edit_file", "write-pattern", sl.HOOKS, "allow", 10,
                       start_seq=21)  # fmt: skip
    return sl.build(directory, specs)


# The trend -------------------------------------------------------------------------------


def test_trend_needs_80(tmp_path, capsys):
    code, out, _ = run(sl.allowed_run(tmp_path / "ledger", 79), capsys)
    assert code == 0
    assert out.endswith(
        "\ntrend: needs 80 decided holds to compare the last 40 with the 40 before "
        "(provisional numbers); this ledger has 79\n"
    )


def test_trend_warning(tmp_path, capsys):
    """40 decisions at 19 s with 36 allowed, then 40 at 4 s with 37 allowed: the warning."""
    directory = sl.two_windows(tmp_path / "ledger", 19, 36, 4, 37)
    code, out, _ = run(directory, capsys)
    assert code == 0
    assert section(out, "trend") == SPEC_WARNING
    assert "holds 80: allowed 73, denied 7, expired 0, abandoned 0, open 0" in out


def test_trend_no_warning(tmp_path, capsys):
    """The same times with 38 then 20 allowed (the rate fell): the three number lines and no
    warning; with the earlier median at 5 s: no warning."""
    code, out, _ = run(sl.two_windows(tmp_path / "fell", 19, 38, 4, 20), capsys)
    assert code == 0
    assert section(out, "trend") == (
        "trend, with provisional thresholds (40 and 40 decisions, half the time, 6 s)\n"
        "  last 40 decisions: median about 4 s, allowed 20 of 40\n"
        "  the 40 before: median about 19 s, allowed 38 of 40\n"
    )
    code, out, _ = run(sl.two_windows(tmp_path / "floor", 5, 36, 2, 37), capsys)
    assert code == 0
    assert section(out, "trend") == (
        "trend, with provisional thresholds (40 and 40 decisions, half the time, 6 s)\n"
        "  last 40 decisions: median about 2 s, allowed 37 of 40\n"
        "  the 40 before: median about 5 s, allowed 36 of 40\n"
    )


def test_trend_windows_are_the_last_by_decision_seq(tmp_path, capsys):
    """With 90 decided holds, the first 10 are outside both windows."""
    directory = sl.two_windows(tmp_path / "ledger", 19, 36, 4, 37, extra=10)
    code, out, _ = run(directory, capsys)
    assert code == 0
    assert section(out, "trend") == SPEC_WARNING


# What it ignores, and what it never does --------------------------------------------------


def test_stats_ignores_other_kinds(tmp_path, capsys):
    """Drill kinds and unknown kinds in a ledger change nothing in the output."""
    specs = sl.mixed_specs()
    extra = [
        (sl.ts("09:59:59.500"), "drill.started", {"session": "d" * 16, "calls": 20}),
        (sl.ts("10:00:05.000"), "drill.decided", {"session": "d" * 16, "n": 1,
                                                  "decision": "deny", "elapsed_ms": 12400,
                                                  "actor": "person"}),
        (sl.ts("10:30:00.000"), "hold.revealed", {"session": sl.A1, "hold": "0000000000000001",
                                                  "label": "real"}),
        (sl.ts("12:31:00.000"), "something.else", {"hold": "0000000000000009", "x": [1, 2]}),
    ]  # fmt: skip
    specs = [extra[0], *specs[:4], extra[1], *specs[4:12], extra[2], *specs[12:], extra[3]]
    code, out, err = run(sl.build(tmp_path / "ledger", specs), capsys)
    assert (code, err) == (0, "")
    assert out == SPEC_MIXED


def test_stats_prefers_a_monotonic_elapsed_ms(tmp_path, capsys):
    """A hold.decided with elapsed_ms (M3's card) gives that time, not the ts difference, and
    the note under all sessions says how many decisions had one."""
    specs = [
        sl.started(sl.A1, "09:59:59.000"),
        sl.created(sl.A1, "0000000000000001", "fs__write_file", "outside-roots", sl.OUTSIDE,
                   "10:00:00.000"),
        sl.decided("0000000000000001", "allow", "10:00:30.000", elapsed_ms=3600),
        sl.created(sl.A1, "0000000000000002", "fs__write_file", "outside-roots", sl.OUTSIDE,
                   "10:01:00.000"),
        sl.decided("0000000000000002", "allow", "10:01:30.000"),
    ]  # fmt: skip
    code, out, _ = run(sl.build(tmp_path / "ledger", specs), capsys)
    assert code == 0
    assert section(out, "all sessions") == (
        "all sessions\n"
        "  holds 2: allowed 2, denied 0, expired 0, abandoned 0, open 0\n"
        "  approval: allowed 2 of 2 decided, too few to say a rate (5 or more needed)\n"
        "  time to decision: median about 4 s, from 2 decisions\n"
        "  Times are wall-clock differences in whole seconds, so each can be off by about\n"
        "  a second, and they include the time before a hold was noticed.\n"
        "  1 of 2 decisions were timed on one clock, from the card that showed the hold.\n"
    )


def test_decisions_without_a_time(tmp_path, capsys):
    """A session whose decisions all lack a parseable time (section 17, Stage 9 build, 2) says
    so under its approval line, instead of `no decisions`, which would contradict it."""
    bad = "not a time"
    specs = [
        sl.started(sl.A1, "09:59:59.000"),
        sl.created(sl.A1, "0000000000000001", "fs__write_file", "outside-roots", sl.OUTSIDE,
                   "10:00:00.000"),
        (bad, "hold.decided", sl.decided("0000000000000001", "allow", "10:00:10.000")[2]),
        sl.created(sl.A1, "0000000000000002", "fs__write_file", "outside-roots", sl.OUTSIDE,
                   "10:01:00.000"),
        (bad, "hold.decided", sl.decided("0000000000000002", "deny", "10:01:10.000")[2]),
        sl.started(sl.B2, "10:59:59.000"),
        sl.created(sl.B2, "0000000000000003", "fs__write_file", "outside-roots", sl.OUTSIDE,
                   "11:00:00.000"),
        (bad, "hold.decided", sl.decided("0000000000000003", "allow", "11:00:10.000")[2]),
    ]  # fmt: skip
    code, out, err = run(sl.build(tmp_path / "ledger", specs), capsys)
    assert (code, err) == (0, "")
    assert section(out, f"session {sl.A1}") == (
        f"session {sl.A1} started 2026-10-06T09:59:59.000Z\n"
        "  holds 2: allowed 1, denied 1, expired 0, abandoned 0, open 0\n"
        "  approval: allowed 1 of 2 decided, too few to say a rate (5 or more needed)\n"
        "  time to decision: none of the 2 decisions has a time\n"
    )
    assert section(out, f"session {sl.B2}") == (
        f"session {sl.B2} started 2026-10-06T10:59:59.000Z\n"
        "  holds 1: allowed 1, denied 0, expired 0, abandoned 0, open 0\n"
        "  approval: allowed 1 of 1 decided, too few to say a rate (5 or more needed)\n"
        "  time to decision: the 1 decision has no time\n"
    )
    assert section(out, "all sessions") == (
        "all sessions\n"
        "  holds 3: allowed 2, denied 1, expired 0, abandoned 0, open 0\n"
        "  approval: allowed 2 of 3 decided, too few to say a rate (5 or more needed)\n"
        "  time to decision: none of the 3 decisions has a time\n"
    )
    assert "no decisions" not in out


def test_session_blocks_are_linear_in_sessions(tmp_path):
    """5,000 sessions with a hold each: the session blocks take well under a second. They were
    quadratic in sessions: 87.9 s for stats on a ledger of 20,000, and 0.74 s for this step at
    5,000 on the machine the fix was made on, where the new code takes milliseconds. Only that
    step is timed, so the test stays fast, and 0.5 s leaves slow runners a wide margin."""
    n = 5000
    data, head = read_files(sl.many_sessions(tmp_path / "ledger", n))
    fold = stats.StatsFold()
    assert verify_bytes(data, head, fold.apply).status == "intact"
    counted = stats.count(fold)
    start = time.perf_counter()
    blocks = stats._session_blocks(fold, counted)
    elapsed = time.perf_counter() - start
    assert len(blocks) == n
    assert blocks[0][0] == f"session {0:016x} started 2026-10-06T09:59:59.000Z"
    assert blocks[-1][0] == f"session {n - 1:016x} started 2026-10-06T09:59:59.000Z"
    assert elapsed < 0.5, f"{elapsed:.2f} s for {n} sessions"


def test_stats_is_read_only(tmp_path, capsys):
    """Paths, sizes and mtimes are unchanged; no lock file is created."""
    directory = sl.mixed(tmp_path / "ledger")
    before = listing(tmp_path)
    code, _, _ = run(directory, capsys)
    assert code == 0
    assert listing(tmp_path) == before
    assert not (directory / LOCK).exists()


def test_stats_usage(capsys):
    for argv in (["stats"], ["stats", "--config", "/a.toml", "--ledger-dir", "/b"]):
        assert cli.main(argv) == 2
        out, err = capsys.readouterr()
        assert out == ""
        assert err == (
            "polarizer: stats needs exactly one of --config <absolute path> "
            "or --ledger-dir <absolute path>\n"
        )


def test_stats_with_config(tmp_path, capsys, monkeypatch):
    """--config names the ledger directory; the config is read with require_env=False and
    nothing is started."""
    directory = sl.mixed(tmp_path / "ledger")
    config = tmp_path / "polarizer.toml"
    config.write_text(
        "ledger_dir = " + json.dumps(str(directory)) + "\n\n"
        '[upstream.fs]\ncommand = "fs-mcp"\nenv = { TOKEN = "${MISSING}" }\n',
        encoding="utf-8",
    )
    monkeypatch.delenv("MISSING", raising=False)
    code = cli.main(["stats", "--config", str(config)])
    out, err = capsys.readouterr()
    assert (code, err) == (0, "")
    assert out == SPEC_MIXED


# Golden files (section 9) ----------------------------------------------------------------


def fixture(name):
    return lambda d: install_fixture(name, d)


GOLDEN = {
    "stats_mixed": (sl.mixed, 0),
    "stats_nothing": (sl.nothing, 0),
    "stats_suggestion": (suggestion_ledger, 0),
    "stats_trend_warning": (lambda d: sl.two_windows(d, 19, 36, 4, 37), 0),
    "stats_trend_no_warning": (lambda d: sl.two_windows(d, 19, 38, 4, 20), 0),
    "stats_tampered": (fixture("broken/edit_value"), 1),
    "stats_invalid": (fixture("broken/insert_float"), 3),
    "stats_torn_tail": (fixture("broken/tear_last_line"), 5),
    "stats_truncated": (fixture("broken/truncated"), 6),
    "stats_no_ledger": (lambda d: d, 2),
}


@pytest.mark.parametrize("case", sorted(GOLDEN))
def test_stats_golden(case, tmp_path, capsys):
    """Every stats row of section 9's golden table, except the stage 10 row."""
    setup, want = GOLDEN[case]
    directory = setup(tmp_path / "ledger")
    code, out, err = run(directory, capsys)
    assert err == ""
    assert code == want
    check_golden(f"{case}.txt", out, directory=directory)


def test_stats_locked(tmp_path, capsys):
    directory = sl.mixed(tmp_path / "ledger")
    lock = LedgerLock.create(directory / LOCK)
    assert lock.acquire(0)
    try:
        code, out, _ = run(directory, capsys)
    finally:
        lock.close()
    assert code == 7
    check_golden("stats_locked.txt", out)


def test_golden_statuses_match_holds():
    """A broken or missing ledger prints the same line from stats as from holds."""
    golden = Path(__file__).parent / "golden"
    for case in ("tampered", "invalid", "torn_tail", "truncated", "no_ledger", "locked"):
        want = (golden / f"holds_{case}.txt").read_text(encoding="utf-8")
        assert (golden / f"stats_{case}.txt").read_text(encoding="utf-8") == want, case


def test_nothing_is_one_line(tmp_path, capsys):
    code, out, err = run(sl.nothing(tmp_path / "ledger"), capsys)
    assert (code, out, err) == (0, "stats: no holds in this ledger\n", "")
