"""`polarizer drill` (docs/MEASURE-SPEC.md, sections 4 to 9 and 12): the sampler, the screens
and conditions, answers, time, stopping, the drill ledger's kinds, the location and terminal
checks, what a drill touches, and the golden files.

Drills run in process with the seams of tests/helpers/drillkit.py, on the small test set, except
where a test says it uses the shipped set or a pseudo-terminal."""

import hashlib
import json
import os
import subprocess
import sys
import textwrap
import time
from collections import Counter
from pathlib import Path

import pytest
from conftest import ROOT, build_chain, install_fixture
from helpers import drillkit as dk
from helpers import holdledger as hl
from helpers.ptyread import finish, read_some

import polarizer
from polarizer import canon, cli, drill, scenarios, writer

GOLDEN = Path(__file__).parent / "golden"
SPEC_CLOSING = drill.CLOSING
KINDS = {
    "drill.started": {"session", "polarizer_version", "set", "set_sha256", "seed", "seed_from",
                      "condition", "condition_from", "calls", "excluded", "keep_predictions"},
    "drill.shown": {"session", "n", "scenario", "hold", "args_commit", "seen_before"},
    "drill.predicted": {"session", "n", "length", "prediction", "elapsed_ms"},
    "drill.decided": {"session", "n", "scenario", "decision", "elapsed_ms", "actor"},
    "drill.revealed": {"session", "n", "scenario", "answer", "shape", "outcome"},
    "drill.ended": {"session", "how", "answered", "calls"},
}  # fmt: skip


def check_golden(name: str, text: str, directory: Path | None = None) -> None:
    if directory is not None:
        text = text.replace(str(directory), "<dir>")
    path = GOLDEN / name
    if os.environ.get("POLARIZER_UPDATE_GOLDEN"):
        path.write_text(text, encoding="utf-8", newline="\n")
    assert text == path.read_text(encoding="utf-8"), name


def kinds(directory) -> list[str]:
    return [e["kind"] for e in dk.entries(directory)]


def golden_drill(monkeypatch, directory, *args, prediction=None):
    inputs = dk.golden_answers(prediction=prediction)
    return dk.run(monkeypatch, directory, inputs, "--seed", dk.GOLDEN_SEED, *args)


# --- the plan (section 6, Sampling) ---------------------------------------------------------


def test_sampler_vector():
    """Section 6, Sampling: the stream's first values and four plans, computed first by an
    independent script from the spec's text. The vector set has 3, 2, 2, 2 and 1 planted
    scenarios of the five shapes; plan b falls back to a shape's whole pool and to every clean
    id; plan c, of 10 calls, has 5 planted, one of each shape; plan d draws the fewest, 6 of
    20."""
    s = drill.stream(bytes(range(16)))
    assert (next(s), next(s)) == (8468598625902157147, 15816047190215027138)
    shapes = ["changed-argument"] * 3 + ["different-tool"] * 2 + ["extra-effect"] * 2
    shapes += ["misleading-summary"] * 2 + ["look-alike"]
    planted = {f"p{i:02d}": shape for i, shape in enumerate(shapes, 1)}
    clean = [f"c{i:02d}" for i in range(1, 13)]

    def order(text):
        return text.split(", ")

    seed = bytes(range(16))
    assert drill.plan(seed, planted, clean, 20, set()) == ("prediction-gate", 9, order(
        "c05, c08, c06, p04, c02, c09, p06, c01, c12, p09, p08, p07, p10, c10, p05, p01, p03, "
        "c03, c07, c04"))  # fmt: skip
    assert drill.plan(seed, planted, clean, 20, {"p01", "p04", "p10", "c01"}) == (
        "prediction-gate", 9, order(
        "c03, c09, c05, c11, c10, c06, c12, c04, p04, p10, p07, p05, p02, p08, p03, c07, c02, "
        "p09, c08, p06"))  # fmt: skip
    assert drill.plan(b"\xff" * 16, planted, clean, 10, set()) == (
        "prediction-gate", 5, order("p06, p08, c10, p03, c06, c07, p04, c09, p10, c01"))  # fmt: skip
    assert drill.plan(b"\xff" * 16, planted, clean, 20, set()) == ("prediction-gate", 6, order(
        "c04, c12, p05, c09, c06, p06, c05, c10, p04, c07, p08, c03, c01, c02, c08, c11, p03, "
        "p10"))  # fmt: skip


def test_planted_range():
    """30 to 50 percent of the calls, at least 5 and at least one per shape when the length
    allows, never more than the set has (section 4, Defaults; computed first by the same
    independent script)."""
    ranges = {n: drill.planted_range(n, 60, 5) for n in (10, 11, 12, 16, 17, 20, 25, 30, 40)}
    assert ranges == {10: (5, 5), 11: (5, 5), 12: (5, 6), 16: (5, 8), 17: (6, 8), 20: (6, 10),
                      25: (8, 12), 30: (9, 15), 40: (12, 20)}  # fmt: skip
    assert drill.planted_range(20, 7, 5) == (6, 7) and drill.planted_range(20, 3, 3) == (3, 3)
    assert drill.planted_range(10, 60, 2) == (5, 5)


def test_planted_count_and_shapes():
    """With the shipped set, every 20-call plan has 6 to 10 planted calls and the rest clean,
    with all five shapes and shape counts at most one apart, and each count from 6 to 10 is
    drawn: 2,000 drills in a row, each leaving out what the two before it showed, as drills on
    one ledger do."""
    the_set = scenarios.newest()
    planted, clean = the_set.shapes(), the_set.ids("clean")
    last = [set(), set()]
    counts = Counter()
    for i in range(2000):
        seed = hashlib.sha256(i.to_bytes(4, "big")).digest()[:16]
        _, k, order = drill.plan(seed, planted, clean, 20, last[0] | last[1])
        shapes = Counter(planted[x] for x in order if x in planted)
        assert 6 <= k <= 10 and sum(shapes.values()) == k and len(order) == 20
        assert len(shapes) == 5 and max(shapes.values()) - min(shapes.values()) <= 1
        assert not (last[0] | last[1]) & set(order)
        last = [last[1], set(order)]
        counts[k] += 1
    assert set(counts) == {6, 7, 8, 9, 10}


def test_plan_is_reproducible_from_the_ledger(monkeypatch, tmp_path):
    """The plan drawn again from drill.started equals the drill.shown sequence and the
    condition, with the shipped set, a random seed and a drill excluded before it."""
    directory = tmp_path / "drills"
    the_set = scenarios.newest()
    for i in range(2):
        inputs = ["", *(["a", ""] * 20)]
        code, _ = dk.run(monkeypatch, directory, inputs, the_set=the_set, session=f"{i:016x}")
        assert code == 0
    started = [e["data"] for e in dk.entries(directory) if e["kind"] == "drill.started"]
    for data in started:
        assert data["set"] == the_set.version and data["set_sha256"] == the_set.sha256
        condition, k, order = drill.plan(bytes.fromhex(data["seed"]), the_set.shapes(),
                                         the_set.ids("clean"), data["calls"],
                                         set(data["excluded"]))  # fmt: skip
        shown = [e["data"]["scenario"] for e in dk.entries(directory)
                 if e["kind"] == "drill.shown" and e["data"]["session"] == data["session"]]  # fmt: skip
        assert shown == order and len(shown) == data["calls"]
        assert condition == data["condition"] and data["condition_from"] == "random"
        assert sum(the_set.scenarios[i]["answer"] == "planted" for i in shown) == k
    assert started[1]["excluded"] != [] and started[0]["excluded"] == []


def test_condition_random_and_flag(monkeypatch, tmp_path):
    """Without --condition the condition is the plan's draw; with it, the flag's value, and the
    order of calls doesn't change."""
    orders = {}
    for name, args in (("random", []), ("flag", ["--condition", "prediction-gate"])):
        directory = tmp_path / name
        code, _ = dk.run(monkeypatch, directory, ["q"], "--seed", dk.GOLDEN_SEED, *args)
        assert code == 0
        data = dk.entries(directory)[1]["data"]
        assert data["condition_from"] == name
        assert data["condition"] == ("plain" if name == "random" else "prediction-gate")
        orders[name] = dk.golden_plan()[2]
    assert orders["random"] == orders["flag"]


def test_no_repeat_of_last_two_drills(monkeypatch, tmp_path):
    """Three drills in a row: nothing shown in drills 1 or 2 is in drill 3, and excluded lists
    them; with --seed nothing is excluded."""
    directory = tmp_path / "drills"
    the_set = scenarios.newest()
    for i in range(3):
        inputs = ["", *(["d", ""] * 20)]
        assert dk.run(monkeypatch, directory, inputs, the_set=the_set, session=f"{i:016x}")[0] == 0
    drills = [e["data"] for e in dk.entries(directory) if e["kind"] == "drill.started"]
    shown = {d["session"]: set() for d in drills}
    for e in dk.entries(directory):
        if e["kind"] == "drill.shown":
            shown[e["data"]["session"]].add(e["data"]["scenario"])
    first, second, third = (shown[d["session"]] for d in drills)
    assert not (first | second) & third
    assert drills[2]["excluded"] == sorted(first | second)
    code, _ = dk.run(monkeypatch, directory, ["q"], "--seed", dk.GOLDEN_SEED, the_set=the_set,
                     session="f" * 16)  # fmt: skip
    assert code == 0
    assert dk.entries(directory)[-2]["data"]["excluded"] == []


# --- the screen is holds' screen ------------------------------------------------------------


def test_drill_block_equals_holds_block(tmp_path, monkeypatch, capsys):
    """For every scenario of the shipped set, the drill's block is byte for byte what `polarizer
    holds` prints for a real hold with the same values: a serve-shaped ledger with a
    hold.created and its side file (the same hold id, session, ts, salt, arguments and timeout),
    its session lock held, printed by the real command."""
    the_set = scenarios.newest()
    session, hold_id = "8e41c6b2d09a7f35", "5d0c2e8a9b7f4136"
    salt = bytes([7]) * 32
    capsys.readouterr()
    for i, (scenario_id, scenario) in enumerate(the_set.scenarios.items()):
        directory = tmp_path / f"ledger-{i}"
        (directory / "sessions").mkdir(parents=True)
        call = scenario["call"]
        commit = hl.side_file(directory, call["arguments"], 7)
        assert commit == hashlib.sha256(salt + drill.arguments_bytes(call["arguments"])).hexdigest()
        built = build_chain(directory, [
            hl.started(session),
            ("hold.created", {"session": session, "hold": hold_id, "tool": call["tool"],
                              "args_commit": commit, "class": call["class"],
                              "class_from": call["class_from"], "rule": call["rule"],
                              "reason": call["reason"],
                              "timeout_seconds": the_set.timeout_seconds}),
        ], head_at=0)  # fmt: skip
        started_ts, shown_ts = built[1]["ts"], built[2]["ts"]
        monkeypatch.setattr(cli, "_now", lambda ts=shown_ts: drill.parse_ts(ts))
        running = hl.hold_running(directory, session)
        try:
            assert cli.main(["holds", "--ledger-dir", str(directory)]) == 0
        finally:
            running.close()
        expected = drill.call_block(scenario, hold_id, commit, session, shown_ts, started_ts,
                                    the_set.timeout_seconds)  # fmt: skip
        printed = capsys.readouterr().out
        assert printed == "holds: 1 open\n\n" + "\n".join(expected) + "\n", scenario_id


# --- a call, answers and reveals ------------------------------------------------------------


def one_scenario_set(scenario_id: str):
    """A set of the shipped scenario and one scenario of the other kind, so a drill shows both."""
    the_set = scenarios.newest()
    target = the_set.scenarios[scenario_id]
    other = next(s for s in the_set.scenarios.values() if s["answer"] != target["answer"])
    chosen = {s["id"]: s for s in (target, other)}
    return scenarios.ScenarioSet(1, "0" * 64, 300, chosen, {})


REVEAL_CASES = [(i, a) for i in scenarios.newest().scenarios for a in ("allow", "deny")]


@pytest.mark.parametrize("scenario_id, answer", REVEAL_CASES)
def test_reveal_for_every_scenario(scenario_id, answer, monkeypatch, tmp_path):
    the_set = one_scenario_set(scenario_id)
    scenario = the_set.scenarios[scenario_id]
    console = dk.install(monkeypatch, the_set, ["", answer, "", answer, ""])
    code = cli.main(["drill", "--ledger-dir", str(tmp_path / "d"), "--condition", "plain"])
    assert code == 0
    revealed = {e["data"]["scenario"]: e["data"] for e in dk.entries(tmp_path / "d")
                if e["kind"] == "drill.revealed"}  # fmt: skip
    planted = scenario["answer"] == "planted"
    outcome = drill.OUTCOMES[(planted, answer)]
    assert revealed[scenario_id] == {
        "session": dk.SESSION, "n": revealed[scenario_id]["n"], "scenario": scenario_id,
        "answer": scenario["answer"], "shape": scenario.get("shape"), "outcome": outcome,
    }  # fmt: skip
    n = revealed[scenario_id]["n"]
    reveal = console.text(2 * n)
    assert reveal.startswith(f"\n{drill.REVEALS[outcome]}\nwhy: {scenario['why']}\n\n")


def test_answers_and_reprompt(monkeypatch, tmp_path):
    """a, allow, d and deny in any case are answers; anything else asks again, with the clock
    running; q stops."""
    inputs = ["", ("ALLOW", 100), "", (" D ", 100), "", ("maybe", 1000), ("Deny", 500), "",
              ("a", 0), "", "q"]  # fmt: skip
    code, console = dk.run(monkeypatch, tmp_path / "d", inputs, "--seed", dk.GOLDEN_SEED,
                           "--condition", "plain")  # fmt: skip
    assert code == 0
    decided = [e["data"] for e in dk.entries(tmp_path / "d") if e["kind"] == "drill.decided"]
    assert [d["decision"] for d in decided] == ["allow", "deny", "deny", "allow"]
    assert decided[2]["elapsed_ms"] == 1500  # the re-prompt's time is counted
    reprompt = console.text(6)
    assert reprompt == "type allow or deny (a or d), or q to stop\nallow or deny? "
    check_golden("drill_reprompt.txt", reprompt)
    assert dk.entries(tmp_path / "d")[-1]["data"]["how"] == "stopped"


def test_prediction_gate_flow(monkeypatch, tmp_path):
    """The prediction comes before the block; an empty one asks again; by default only its
    length is kept, and with --keep-predictions the folded text, cut to 200 characters."""
    long = "it  will\twrite " + "x" * 300
    for keep in (False, True):
        directory = tmp_path / f"keep-{keep}"
        inputs = ["", ("   ", 300), (long, 700), ("d", 1000), "", "q"]
        args = ["--keep-predictions"] if keep else []
        code, console = dk.run(monkeypatch, directory, inputs, "--seed", dk.GOLDEN_SEED,
                               "--condition", "prediction-gate", *args)  # fmt: skip
        assert code == 0
        first = console.text(1)
        assert first.endswith("what do you expect this call to do? ") and "hold " not in first
        assert (
            console.text(2)
            == "type a few words, or q to stop\nwhat do you expect this call to do? "
        )
        assert console.text(3).startswith("\nhold ") and console.text(3).endswith("allow or deny? ")
        predicted = [e["data"] for e in dk.entries(directory) if e["kind"] == "drill.predicted"]
        assert len(predicted) == 1
        assert predicted[0]["length"] == len(long.strip()) and predicted[0]["elapsed_ms"] == 1000
        if keep:
            assert predicted[0]["prediction"].startswith("it will write xxx")
            assert len(predicted[0]["prediction"]) == 200
            assert predicted[0]["prediction"].endswith("...")
        else:
            assert predicted[0]["prediction"] is None
            assert "x" * 50 not in (directory / "ledger.jsonl").read_text(encoding="utf-8")
        started = dk.entries(directory)[1]["data"]
        assert started["keep_predictions"] is keep


def test_elapsed_ms_is_monotonic(monkeypatch, tmp_path):
    """With the wall clock stepped back 1.1 s between the prompt and the answer and the
    monotonic clock advanced 4.2 s, elapsed_ms is 4200; the same for drill.predicted."""
    stamps = iter(["2026-10-06T18:02:11.425Z"] * 3 + ["2026-10-06T18:02:10.325Z"] * 20)
    inputs = ["", ("my guess", 4200), ("d", 4200), "", "q"]
    code, _ = dk.run(monkeypatch, tmp_path / "d", inputs, "--seed", dk.GOLDEN_SEED,
                     "--condition", "prediction-gate", ts=lambda: next(stamps))  # fmt: skip
    assert code == 0
    found = {e["kind"]: e for e in dk.entries(tmp_path / "d")}
    assert found["drill.predicted"]["data"]["elapsed_ms"] == 4200
    assert found["drill.decided"]["data"]["elapsed_ms"] == 4200
    assert found["drill.decided"]["ts"] < found["drill.started"]["ts"]  # the wall clock stepped


def test_over_300_is_marked(monkeypatch, tmp_path):
    """An answer over 300 000 ms gets the reveal's extra line, and the end screen and report give
    the rates with and without it; exactly 300 000 ms is not marked."""
    inputs = dk.golden_answers()
    inputs[1] = (inputs[1][0], 300_001)  # call 1's answer
    inputs[3] = (inputs[3][0], 300_000)  # call 2's answer
    code, console = dk.run(monkeypatch, tmp_path / "d", inputs, "--seed", dk.GOLDEN_SEED)
    assert code == 0
    assert drill.OVER_LINE in console.text(2)
    assert drill.OVER_LINE not in console.text(4)
    check_golden("drill_reveal_over_300.txt", console.text(2))
    end = console.text(len(console.segments) - 1)
    assert "\nover 300 s: call 1. Without it:\n  planted calls: " in end
    check_golden("drill_end_over_300.txt", end)
    assert cli.main(["drill", "report", "--ledger-dir", str(tmp_path / "d")]) == 0


def test_stop_and_interrupt(monkeypatch, tmp_path):
    """q at each prompt, the end of input and Ctrl+C each end the drill with drill.ended, the
    right `how`, the end screen and exit 0."""
    cases = {
        "q at the intro": (["q"], "stopped", 0),
        "q at the answer": (["", "q"], "stopped", 0),
        "q at the Enter wait": (["", "a", "q"], "stopped", 1),
        "end of input": (["", "a", "", dk.EOF_], "interrupted", 1),
        "ctrl+c at the answer": (["", "a", "", dk.INTERRUPT], "interrupted", 1),
        "q at the prediction": (["", "q"], "stopped", 0),
    }
    for i, (name, (inputs, how, answered)) in enumerate(cases.items()):
        directory = tmp_path / f"case-{i}"
        condition = "prediction-gate" if "prediction" in name else "plain"
        code, console = dk.run(monkeypatch, directory, list(inputs), "--seed", dk.GOLDEN_SEED,
                               "--condition", condition)  # fmt: skip
        assert code == 0, name
        ended = dk.entries(directory)[-1]
        assert ended["kind"] == "drill.ended", name
        assert ended["data"] == {"session": dk.SESSION, "how": how, "answered": answered,
                                 "calls": 20}, name  # fmt: skip
        end = console.text(len(console.segments) - 1)
        assert f"Drill stopped: {answered} of 20 calls answered" in end, name
        assert end.endswith("\n".join(SPEC_CLOSING) + "\n"), name


def test_end_screen_equals_report_line(monkeypatch, tmp_path, capsys):
    code, console = golden_drill(monkeypatch, tmp_path / "d")
    assert code == 0
    capsys.readouterr()
    assert cli.main(["drill", "report", "--ledger-dir", str(tmp_path / "d")]) == 0
    report = capsys.readouterr().out
    end = console.text(len(console.segments) - 1)
    # the end screen's counts and median, and the report's line for that drill
    assert "planted calls: 8. You denied 7:" in end and "clean calls: 12. You denied 1:" in end
    assert "median time to decide: 12.4 s" in end
    assert "plain, 20 of 20: caught 7 of 8, false flags 1 of 12, median 12.4 s" in report


# --- the ledger ---------------------------------------------------------------------------


def test_drill_kinds_and_fields(monkeypatch, tmp_path):
    for condition in ("plain", "prediction-gate"):
        directory = tmp_path / condition
        prediction = "it writes a file" if condition == "prediction-gate" else None
        code, _ = golden_drill(monkeypatch, directory, "--condition", condition,
                               prediction=prediction)  # fmt: skip
        assert code == 0
        seen = set()
        for e in dk.entries(directory)[1:]:
            assert set(e["data"]) == KINDS[e["kind"]], e["kind"]
            assert canon.subset_problem(e["data"]) is None
            assert all(isinstance(k, str) and k.isascii() for k in e["data"])
            seen.add(e["kind"])
        want = set(KINDS) - ({"drill.predicted"} if condition == "plain" else set())
        assert seen == want


def test_drill_ledger_verifies(monkeypatch, tmp_path, capsys):
    directory = tmp_path / "d"
    assert golden_drill(monkeypatch, directory)[0] == 0
    capsys.readouterr()
    assert cli.main(["verify", "--ledger-dir", str(directory)]) == 0
    out = capsys.readouterr().out
    assert out.startswith(f"intact: {len(dk.entries(directory))} entries, 0 sessions, 0 calls\n")
    assert cli.main(["verify", "--args", "--ledger-dir", str(directory)]) == 0
    assert "args: 0 matching, 0 missing, 0 tampered, 0 orphaned" in capsys.readouterr().out
    assert not (directory / "args").exists()
    assert cli.main(["holds", "--ledger-dir", str(directory)]) == 0
    assert capsys.readouterr().out == "holds: nothing is held\n"


def test_drill_kinds_are_not_security_kinds(monkeypatch, tmp_path):
    assert not any(k.startswith("drill.") for k in writer.SECURITY_KINDS)
    directory = tmp_path / "d"
    assert golden_drill(monkeypatch, directory)[0] == 0
    head = json.loads((directory / "ledger.head").read_text())
    assert head["seq"] == 0  # still the genesis entry


def test_version_from_metadata(monkeypatch, tmp_path):
    """__version__ is the installed package's metadata version, which is pyproject.toml's; the
    drill records it."""
    import tomllib
    from importlib import metadata

    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert polarizer.__version__ == metadata.version("polarizer")
    assert polarizer.__version__ == pyproject["project"]["version"]
    assert golden_drill(monkeypatch, tmp_path / "d")[0] == 0
    assert dk.entries(tmp_path / "d")[1]["data"]["polarizer_version"] == polarizer.__version__


def test_writer_stop_ends_the_drill(monkeypatch, tmp_path, capsys):
    """A line that doesn't chain, appended mid-drill: the drill prints the stop line and exits
    1, showing nothing more."""
    directory = tmp_path / "d"

    class Tamper(list):
        def pop(self, i=0):
            item = super().pop(i)
            if item == "tamper":
                with open(directory / "ledger.jsonl", "ab") as f:
                    f.write(b'{"not":"a chained entry"}\n')
                return ("d", 0)
            return item

    console = dk.install(monkeypatch, inputs=Tamper(["", "a", "", "tamper", "", "a"]))
    capsys.readouterr()
    code = cli.main(["drill", "--ledger-dir", str(directory), "--seed", dk.GOLDEN_SEED])
    err = capsys.readouterr().err
    assert code == 1
    assert err.rstrip("\n").endswith(drill.STOP_LINE)
    assert "stopped writing the ledger" in err
    assert "Drill" not in console.text(len(console.segments) - 1)


# --- refusals and the location -------------------------------------------------------------


def test_drill_refuses_serve_ledger(monkeypatch, tmp_path, capsys):
    directory = install_fixture("valid/session", tmp_path / "serve")
    before = (directory / "ledger.jsonl").read_bytes()
    code, _ = dk.run(monkeypatch, directory, [""])
    err = capsys.readouterr().err
    assert code == 2
    check_golden("drill_refused_serve_ledger.txt", err, directory)
    assert (directory / "ledger.jsonl").read_bytes() == before


def test_serve_refuses_drill_ledger(monkeypatch, tmp_path, capsys):
    """serve on a ledger with drill entries: one line, exit 2, nothing appended."""
    directory = tmp_path / "drills"
    assert golden_drill(monkeypatch, directory)[0] == 0
    before = (directory / "ledger.jsonl").read_bytes()
    toml = tmp_path / "polarizer.toml"
    # An upstream that is never started: serve refuses the ledger before any upstream starts.
    toml.write_text(
        f'ledger_dir = "{directory.as_posix()}"\nledger_forbidden_paths = []\n\n'
        f'[upstream.probe]\ncommand = "{Path(sys.executable).as_posix()}"\nargs = ["-c", "pass"]\n',
        encoding="utf-8",
    )
    capsys.readouterr()
    code = cli.main(["serve", "--config", str(toml)])
    err = capsys.readouterr().err
    assert code == 2
    assert err.startswith(f"polarizer: {directory} holds drill entries;")
    check_golden("serve_refused_drill_ledger.txt", err, directory)
    assert (directory / "ledger.jsonl").read_bytes() == before
    assert not (directory / "sessions").exists()


def test_drill_refuses_forbidden_dir(monkeypatch, tmp_path, fake_home, capsys):
    forbidden = fake_home / ".local" / "share" / "parallax" / "drills"
    code, _ = dk.run(monkeypatch, forbidden, [""])
    err = capsys.readouterr().err
    assert code == 2
    check_golden("drill_refused_forbidden_dir.txt", err.replace(str(fake_home), "<home>"))
    assert not forbidden.exists()
    tree = tmp_path / "work"
    (tree / ".git").mkdir(parents=True)
    code, _ = dk.run(monkeypatch, tree / "drills", ["q"])
    err = capsys.readouterr().err
    assert code == 0
    assert (
        f"polarizer: warning: ledger_dir {tree / 'drills'} is inside the git working tree {tree}"
        in err
    )


def test_drill_refuses_broken_ledger(monkeypatch, tmp_path, capsys):
    directory = install_fixture("broken/edit_value", tmp_path / "broken")
    code, _ = dk.run(monkeypatch, directory, [""])
    assert code == 1
    check_golden("drill_refused_broken_ledger.txt", capsys.readouterr().err, directory)


def test_loading_check(monkeypatch, tmp_path, capsys):
    """A copy of the set with a broken scenario: the set's refusal line, exit 2, nothing
    written (no directory either)."""
    doc = json.loads(json.dumps(dk.TEST_SET.doc))
    doc["scenarios"][0]["call"]["reason"] = "held for no reason"
    data = json.dumps(doc).encode("ascii")
    with pytest.raises(scenarios.SetProblem):
        scenarios.parse(data, 1)
    monkeypatch.setattr(drill, "load_set", lambda: scenarios.parse(data, 1))
    monkeypatch.setattr(drill, "is_terminal", lambda: True)
    directory = tmp_path / "d"
    code = cli.main(["drill", "--ledger-dir", str(directory)])
    assert code == 2
    check_golden("drill_refused_scenarios.txt", capsys.readouterr().err)
    assert not directory.exists()


USAGE = {
    "calls": (["--calls", "9"], "polarizer: --calls must be a whole number from 10 to 40"),
    "condition": (["--condition", "fast"], "polarizer: --condition must be plain or prediction-gate"),
    "seed": (["--seed", "ABC"], "polarizer: --seed must be 32 lowercase hex characters"),
    "keep_predictions": (["report", "--keep-predictions"],
                         "polarizer: --keep-predictions goes with drill"),
}  # fmt: skip


@pytest.mark.parametrize("case", sorted(USAGE))
def test_drill_usage_refusals(case, monkeypatch, tmp_path, capsys):
    args, line = USAGE[case]
    monkeypatch.setattr(drill, "is_terminal", lambda: True)
    code = cli.main(["drill", *args, "--ledger-dir", str(tmp_path / "d")])
    err = capsys.readouterr().err
    assert code == 2 and err == line + "\n"
    check_golden(f"drill_refused_{case}.txt", err)
    assert not (tmp_path / "d").exists()


def test_drill_usage_lines(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(drill, "is_terminal", lambda: True)
    cases = [
        (["drill", "--export", "x.json"], "polarizer: --export goes with drill report"),
        (["drill", "report", "--calls", "20"],
         "polarizer: drill report takes only --ledger-dir and --export"),
        (["drill", "--ledger-dir", "relative"],
         "polarizer: --ledger-dir must be an absolute path, got relative"),
        (["drill", "--calls", "x"], "polarizer: --calls must be a whole number from 10 to 40"),
        (["drill", "--calls", "41"], "polarizer: --calls must be a whole number from 10 to 40"),
    ]  # fmt: skip
    for argv, line in cases:
        assert cli.main(argv) == 2
        assert capsys.readouterr().err == line + "\n"
    assert cli.main(["drill", "nonsense"]) == 2
    assert capsys.readouterr().err.startswith("polarizer: argument action: invalid choice")


def test_drill_needs_terminal(monkeypatch, tmp_path, capsys):
    """Without a terminal on stdin or stdout: the refusal line, exit 2, no directory created."""
    monkeypatch.setattr(drill, "is_terminal", lambda: False)
    directory = tmp_path / "d"
    code = cli.main(["drill", "--ledger-dir", str(directory)])
    err = capsys.readouterr().err
    assert code == 2 and err == drill.NO_TERMINAL + "\n"
    check_golden("drill_refused_no_terminal.txt", err)
    assert not directory.exists()
    done = subprocess.run([sys.executable, "-m", "polarizer", "drill", "--ledger-dir",
                           str(directory)], stdin=subprocess.DEVNULL, capture_output=True,
                          timeout=120)  # fmt: skip
    assert done.returncode == 2 and done.stderr.decode() == drill.NO_TERMINAL + "\n"
    assert not directory.exists()


# --- what a drill touches -----------------------------------------------------------------


def listing(root: Path) -> dict:
    found = {}
    for path in sorted(root.rglob("*")):
        st = path.lstat()
        found[str(path.relative_to(root))] = (st.st_size, st.st_mtime_ns)
    return found


def test_drill_writes_only_its_directory(monkeypatch, fake_home, tmp_path):
    """Paths, sizes and mtimes under the fake home differ only inside the drill directory, which
    is the default one, and in the mtime of the directory that holds it, where the drill
    directory was created; no args/ directory is created."""
    (fake_home / "notes.txt").write_text("unchanged")
    (fake_home / ".local" / "share" / "polarizer").mkdir(parents=True)
    before = listing(fake_home)
    console = dk.install(monkeypatch, inputs=dk.golden_answers())
    assert cli.main(["drill", "--seed", dk.GOLDEN_SEED]) == 0
    assert "Drill finished" in console.text()
    after = listing(fake_home)
    drills = Path(".local") / "share" / "polarizer-drills"
    changed = {p for p in set(before) | set(after) if before.get(p) != after.get(p)}
    # Creating the drill directory changes its parent's mtime, and nothing else outside it.
    changed.discard(str(drills.parent))
    assert changed and all(Path(p) == drills or drills in Path(p).parents for p in changed)
    assert not (fake_home / drills / "args").exists()
    assert drill.default_dir() == fake_home / drills


AUDIT = r"""
import sys
events = []
WATCHED = ("socket.connect", "socket.bind", "socket.getaddrinfo", "subprocess.Popen",
           "os.system", "os.exec", "os.posix_spawn", "os.spawn", "os.fork", "os.forkpty",
           "os.startfile")
def hook(event, args):
    if event in WATCHED:
        events.append(event)
sys.addaudithook(hook)
sys.path[:0] = [{tests!r}]
from helpers import drillkit as dk
from polarizer import cli, drill, writer
class Patch:
    def setattr(self, obj, name, value):
        setattr(obj, name, value)
console = dk.install(Patch(), inputs=dk.golden_answers())
code = cli.main(["drill", "--ledger-dir", {directory!r}, "--seed", dk.GOLDEN_SEED])
assert code == 0, code
assert "Drill finished" in console.text()
code = cli.main(["drill", "report", "--ledger-dir", {directory!r}, "--export", {export!r}])
assert code == 0, code
print("events:", events)
"""


def test_drill_opens_no_connection_and_starts_no_process(tmp_path):
    """A subprocess with an audit hook watching for network connections and new processes runs
    a whole drill in process, then drill report --export: the hook records nothing."""
    script = AUDIT.format(tests=str(Path(__file__).parent), directory=str(tmp_path / "d"),
                          export=str(tmp_path / "summary.json"))  # fmt: skip
    done = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True,
                          timeout=120, cwd=tmp_path)  # fmt: skip
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip().endswith("events: []"), done.stdout[-500:]
    assert (tmp_path / "summary.json").exists()


def test_audit_hook_sees_what_it_watches(tmp_path):
    """The same hook does record a process start and a connection attempt, so the test above
    would see one."""
    script = textwrap.dedent("""
        import socket, subprocess, sys
        events = []
        sys.addaudithook(lambda e, a: events.append(e) if e in ("subprocess.Popen",
                         "socket.connect") else None)
        subprocess.run([sys.executable, "-c", "pass"])
        s = socket.socket()
        try:
            s.connect(("127.0.0.1", 9))
        except OSError:
            pass
        print(sorted(set(events)))
    """)
    done = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True,
                          timeout=120)  # fmt: skip
    assert done.stdout.strip() == "['socket.connect', 'subprocess.Popen']"


# --- the golden files ---------------------------------------------------------------------


SPEC_INTRO = """Polarizer drill: practice with held calls

You will see 20 held calls, one at a time. Each shows the task the agent was
given, then the call exactly as polarizer holds would show it. Answer allow or
deny. Between 6 and 10 of the 20 calls are planted: they differ from the
task in a way that should be denied. The number changes from drill to drill,
and real work has far fewer. After each answer you see whether the call was
clean or planted, and why.

Nothing here is real. There is no agent and no server, nothing is sent
anywhere, and nothing times out. Your answers and times stay on this computer,
in <dir>.

Drills measure attention when you know you are being tested. Use them to see
trends in your own oversight, not to grade or rank anyone.

This drill: plain. Type q at any prompt to stop.
Press Enter to start."""

SPEC_END = """
Drill finished: 20 of 20 calls answered (plain).

planted calls: 8. You denied 7: caught 88%, 95% interval 52% to 98%.
clean calls: 12. You denied 1: false flags 8%, 95% interval 1% to 36%.
median time to decide: 12.4 s (planted 18.0 s, clean 10.9 s)
missed: call 7 (look-alike)
false flags: call 12

One drill says little; the intervals show how little. Run polarizer drill report
to see every drill so far.

Drills measure attention when you know you are being tested. Use them to see
trends in your own oversight, not to grade or rank anyone.
"""


def test_drill_golden(monkeypatch, tmp_path):
    """Every drill screen of section 9's golden table, from one plain and one prediction-gate
    drill of the example, checked against section 5's exact text where it gives one."""
    directory = tmp_path / "plain"
    code, console = golden_drill(monkeypatch, directory)
    assert code == 0
    intro = console.text(0).replace(str(directory), "<dir>")
    assert intro == SPEC_INTRO
    check_golden("drill_intro_plain.txt", intro)
    call3 = console.text(5)
    assert call3.startswith(
        '\ncall 3 of 20\ntask: Add a "Running the tests" section to README.md in the '
        "garden-planner project.\n\nhold 0000000000000003 fs__edit_file local-write\n"
        'held by outside-roots: argument "path": /home/river/code/garden-planner/README.md is '
        "outside every workspace root\nwaiting about 0m00s; times out after 300 s\n"
        f"session {dk.SESSION} started {dk.TS}, running\nargs_commit "
    )
    assert call3.endswith("\n}\n\nallow or deny? ")
    check_golden("drill_call_plain.txt", call3)
    _, _, order = dk.golden_plan()
    outcomes = {}
    for n in range(1, len(order) + 1):
        reveal = console.text(2 * n)
        for outcome, first in drill.REVEALS.items():
            if reveal.startswith("\n" + first + "\n") and n < 20:
                outcomes.setdefault(outcome, reveal)
    assert set(outcomes) == set(drill.REVEALS)
    for outcome, reveal in outcomes.items():
        assert reveal.endswith("\n\nPress Enter for the next call.")
        check_golden(f"drill_reveal_{outcome.replace('-', '_')}.txt", reveal)
    assert console.text(40).endswith("\n\nPress Enter to see the results.")
    end = console.text(41)
    assert end == SPEC_END
    check_golden("drill_end.txt", end)

    directory = tmp_path / "gate"
    code, console = golden_drill(monkeypatch, directory, "--condition", "prediction-gate",
                                 prediction="adds a section")  # fmt: skip
    assert code == 0
    intro = console.text(0).replace(str(directory), "<dir>")
    assert intro == SPEC_INTRO.replace(
        "This drill: plain. Type q at any prompt to stop.",
        "This drill: prediction first. Before each call, type in a few words what you\n"
        "expect it to do; then you see the call. Only the length of what you type is\n"
        "kept. Type q at any prompt to stop.",
    )
    check_golden("drill_intro_prediction_gate.txt", intro)
    # Call 3 is segments 7 (to the prediction) and 8 (to the answer).
    call3 = console.text(7) + console.text(8)
    assert console.text(7) == (
        '\ncall 3 of 20\ntask: Add a "Running the tests" section to README.md in the '
        "garden-planner project.\nwhat do you expect this call to do? "
    )
    assert console.text(8).startswith("\nhold 0000000000000003 fs__edit_file local-write\n")
    check_golden("drill_call_prediction_gate.txt", call3)

    directory = tmp_path / "stopped"
    inputs = dk.golden_answers()[:7] + ["q"]
    code, console = dk.run(monkeypatch, directory, inputs, "--seed", dk.GOLDEN_SEED)
    assert code == 0
    end = console.text(len(console.segments) - 1)
    assert end.startswith("\nDrill stopped: 3 of 20 calls answered (plain).\n\n")
    check_golden("drill_end_stopped.txt", end)


# --- on a pseudo-terminal -----------------------------------------------------------------


@pytest.mark.skipif(sys.platform == "win32", reason="Windows has no pty module")
def test_drill_on_a_pty(tmp_path):
    """`python -m polarizer drill --seed ... --ledger-dir ...` on a pseudo-terminal, answered by
    the test as a person would, with the shipped set: it runs to the end screen and exits 0.
    Each wait is for the prompt to appear, up to 60 s; a prompt appears in well under a second
    here, so the margin is far more than five times what it needs."""
    import pty

    directory = tmp_path / "d"
    controller, terminal = pty.openpty()
    env = {**os.environ, "HOME": str(tmp_path / "home"), "USERPROFILE": str(tmp_path / "home")}
    proc = subprocess.Popen(
        [sys.executable, "-m", "polarizer", "drill", "--seed", "0" * 32, "--calls", "10",
         "--condition", "plain", "--ledger-dir", str(directory)],
        stdin=terminal, stdout=terminal, stderr=terminal, env=env,
    )  # fmt: skip
    os.close(terminal)
    output, answered = b"", 0
    try:

        def wait_for(text: bytes) -> None:
            nonlocal output
            deadline = time.monotonic() + 60
            while text not in output:
                assert time.monotonic() < deadline, f"never saw {text!r}:\n{output.decode()}"
                chunk = read_some(controller, 0.05)
                assert chunk is not None, f"the drill ended early:\n{output.decode()}"
                output += chunk

        wait_for(b"Press Enter to start.")
        os.write(controller, b"\n")
        while answered < 10:
            wait_for(b"allow or deny? ")
            output = output[output.rfind(b"allow or deny? ") + 1 :]
            os.write(controller, b"d\n")
            answered += 1
            wait_for(b"Press Enter")
            os.write(controller, b"\n")
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            chunk = read_some(controller, 0.05)
            if chunk is None:
                break
            output += chunk
        code, rest = finish(controller, proc)
        output += rest
    finally:
        if proc.poll() is None:
            proc.kill()
        os.close(controller)
    text = output.decode("utf-8", "replace").replace("\r\n", "\n")
    assert code == 0, text
    assert "Drill finished: 10 of 10 calls answered (plain)." in text
    assert kinds(directory).count("drill.decided") == 10
