"""`polarizer drill report` and `--export` (docs/MEASURE-SPEC.md, section 9): the report of the
7-drill fixture (tests/helpers/drillledger.py) against section 9's exact lines, pooling and the
comparison, that it writes nothing, and the export's schema and what it must never hold."""

import json
import os
import re
from pathlib import Path

import pytest
from conftest import install_fixture
from helpers import drillledger as fx
from test_drill import check_golden, listing

from polarizer import cli, drill

SPEC_REPORT = """drills: 5 finished, 2 stopped early; 116 calls answered (scenario set 1)
calls seen in an earlier drill: 9 of 116

plain: 3 drills
  planted calls: 17. Denied 13: caught 76%, 95% interval 52% to 91%.
  clean calls: 42. Denied 3: false flags 7%, 95% interval 2% to 20%.
  median time to decide: 12.4 s
prediction gate: 4 drills
  planted calls: 17. Denied 15: caught 88%, 95% interval 65% to 97%.
  clean calls: 40. Denied 2: false flags 5%, 95% interval 1% to 17%.
  median time to decide: 16.1 s

prediction gate minus plain
  caught: +12 points, 95% interval -15 to +37: not distinguishable from noise at these numbers.
  false flags: -2 points, 95% interval -15 to +11: not distinguishable from noise at these numbers.

by kind of planted call
  changed argument: 7 planted, 6 caught
  different tool: 6 planted, 6 caught
  extra effect: 8 planted, 6 caught
  misleading summary: 7 planted, 5 caught
  look-alike: 6 planted, 5 caught

each drill
  2026-10-06 plain, 20 of 20: caught 5 of 6, false flags 1 of 14, median 12.4 s
  2026-10-20 prediction gate, 17 of 20, stopped: caught 4 of 5, false flags 0 of 12, median 15.0 s

Drills measure attention when you know you are being tested. Use them to see
trends in your own oversight, not to grade or rank anyone.""".split("\n")


def report(directory: Path, capsys, *args) -> tuple[int, str, str]:
    code = cli.main(["drill", "report", "--ledger-dir", str(directory), *args])
    out, err = capsys.readouterr()
    return code, out, err


def in_order(lines: list[str], within: list[str]) -> bool:
    """Every line of `lines` appears in `within`, in the same order."""
    rest = iter(within)
    return all(any(line == other for other in rest) for line in lines)


def test_report_on_fixture(tmp_path, capsys):
    directory = fx.write(tmp_path / "drills")
    code, out, err = report(directory, capsys)
    assert code == 0 and err == ""
    lines = out.rstrip("\n").split("\n")
    # Section 9's example, every line in order; the over-300 lines and the other drills' lines
    # are in between (section 16, question 4).
    assert in_order(SPEC_REPORT, lines), out
    assert "  over 300 s: 1 answer. Without it:" in lines
    assert "    clean calls: 41. Denied 3: false flags 7%, 95% interval 2% to 20%." in lines
    assert (
        "  2026-10-08 plain, 20 of 20: caught 4 of 6, false flags 1 of 14, median 12.4 s, "
        "1 over 300 s"
    ) in lines
    check_golden("drill_report.txt", out)


def test_report_pools_and_compares(tmp_path, capsys):
    """A condition's lines pool its drills; one condition only gives the "no drills yet"
    comparison line; a drill with no drill.ended says so; a drill with no answer is left out."""
    directory = fx.write(tmp_path / "plain", fx.DRILLS[:3])
    code, out, _ = report(directory, capsys)
    assert code == 0
    assert "prediction gate minus plain: no prediction-gate drills yet" in out
    assert "\nprediction gate:" not in out and "plain: 3 drills" in out
    check_golden("drill_report_one_condition.txt", out)
    directory = fx.write(tmp_path / "gate", fx.DRILLS[3:])
    out = report(directory, capsys)[1]
    assert "prediction gate minus plain: no plain drills yet" in out

    def unfinished():
        date, condition, calls, _, answered, keep = fx.drill_1()
        return date, condition, calls, None, answered[:4], keep

    def empty():
        date, condition, calls, _, _, keep = fx.drill_1()
        return date, condition, calls, "stopped", [], keep

    directory = fx.write(tmp_path / "unfinished", [unfinished, empty])
    out = report(directory, capsys)[1]
    assert "drills: 0 finished, 1 stopped early; 4 calls answered" in out
    assert "2026-10-06 plain, 4 of 20, did not end:" in out
    assert out.count("2026-10-06 plain") == 1  # the drill with no answer is left out
    assert drill._sets({1, 2}) == "scenario sets 1 and 2"
    assert drill._sets({1, 2, 3}) == "scenario sets 1, 2 and 3"


def test_report_keeps_guided_apart(tmp_path, capsys):
    """Guided drills are their own condition: their own block, compared with each other
    condition only as a labelled difference with its interval and phrase, their own lines by
    kind of planted call, and never in the plain or prediction-gate numbers, which are what
    they were without them."""
    directory = fx.write(tmp_path / "drills", fx.WITH_GUIDED)
    code, out, err = report(directory, capsys)
    assert code == 0 and err == ""
    lines = out.rstrip("\n").split("\n")
    plain_only = report(fx.write(tmp_path / "seven"), capsys)[1].rstrip("\n").split("\n")
    block = lambda ls, head: ls[ls.index(head) : ls.index(head) + 4]  # noqa: E731
    for head in ("plain: 3 drills", "prediction gate: 4 drills"):
        assert block(lines, head) == block(plain_only, head)
    assert block(lines, "guided: 2 drills") == [
        "guided: 2 drills",
        "  planted calls: 13. Denied 12: caught 92%, 95% interval 66% to 99%.",
        "  clean calls: 17. Denied 2: false flags 12%, 95% interval 3% to 35%.",
        "  median time to decide: 9.0 s",
    ]
    assert "prediction gate minus plain" in lines
    for title in ("guided minus plain", "guided minus prediction gate"):
        at = lines.index(title)
        for row, label in zip(lines[at + 1 : at + 3], ("caught", "false flags"), strict=True):
            assert row.startswith(f"  {label}: ") and " points, 95% interval " in row
            assert row.endswith(("not distinguishable from noise at these numbers.",
                                 "distinguishable from noise at these numbers."))  # fmt: skip
    kinds = plain_only.index("by kind of planted call")
    at = lines.index("by kind of planted call, plain and prediction gate")
    assert lines[at + 1 : at + 6] == plain_only[kinds + 1 : kinds + 6]
    at = lines.index("by kind of planted call, guided")
    assert lines[at + 1 : at + 6] == [
        "  changed argument: 3 planted, 3 caught", "  different tool: 3 planted, 3 caught",
        "  extra effect: 3 planted, 2 caught", "  misleading summary: 2 planted, 2 caught",
        "  look-alike: 2 planted, 2 caught",
    ]  # fmt: skip
    assert "  2026-10-05 guided, 10 of 10: caught 5 of 5, false flags 1 of 5, median 7.0 s" in lines
    assert lines[0] == "drills: 7 finished, 2 stopped early; 146 calls answered (scenario set 1)"
    check_golden("drill_report_guided.txt", out)

    # A person's first report, after one guided drill: nothing to compare yet.
    out = report(fx.write(tmp_path / "first", [fx.guided_first]), capsys)[1]
    assert "prediction gate minus plain: no prediction-gate or plain drills yet\n" in out
    assert "guided minus plain: no plain drills yet\n" in out
    assert "guided minus prediction gate: no prediction-gate drills yet\n" in out
    assert "\nby kind of planted call, guided\n" in out
    assert "plain and prediction gate" not in out and "\nplain:" not in out


def test_export_keeps_guided_apart(tmp_path, capsys):
    """The export of the ledger with guided drills: guided is its own condition, compared with
    the other two in guided_difference, with its own guided_by_shape; the plain and
    prediction-gate fields, by_shape and difference are the seven-drill fixture's."""
    seven, both = tmp_path / "seven.json", tmp_path / "both.json"
    assert report(fx.write(tmp_path / "a"), capsys, "--export", str(seven))[0] == 0
    assert report(fx.write(tmp_path / "b", fx.WITH_GUIDED), capsys, "--export", str(both))[0] == 0
    a, b = json.loads(seven.read_bytes()), json.loads(both.read_bytes())
    check_schema(a)
    check_schema(b)
    for key in ("difference", "by_shape"):
        assert a[key] == b[key], key
    for condition in ("plain", "prediction-gate"):
        assert a["conditions"][condition] == b["conditions"][condition]
    assert a["conditions"]["guided"] is None and a["guided_by_shape"] is None
    assert a["guided_difference"] == {"minus_plain": {"catch": None, "false_flag": None},
                                      "minus_prediction_gate": {"catch": None,
                                                                "false_flag": None}}  # fmt: skip
    guided = b["conditions"]["guided"]
    assert (guided["drills"], guided["planted"], guided["caught"]) == (2, 13, 12)
    assert (guided["clean"], guided["false_flags"]) == (17, 2)
    assert set(b["guided_difference"]["minus_plain"]["catch"]) == {
        "points", "low_points", "high_points", "distinguishable"}  # fmt: skip
    assert b["guided_by_shape"]["extra-effect"] == {"planted": 3, "caught": 2}
    assert [r["condition"] for r in b["per_drill"]][::8] == ["guided", "guided"]
    assert b["drills"] == 9 and b["answered"] == 146
    check_golden("drill_export_guided.json", both.read_text(encoding="utf-8"))


def test_report_none_and_no_ledger(tmp_path, capsys):
    directory = install_fixture("valid/minimal", tmp_path / "empty")
    code, out, _ = report(directory, capsys)
    assert code == 0 and out == f"drills: none yet in {directory}; run polarizer drill\n"
    check_golden("drill_report_none.txt", out, directory)
    missing = tmp_path / "missing"
    code, out, _ = report(missing, capsys)
    assert code == 0 and out == f"drills: none yet in {missing}; run polarizer drill\n"
    check_golden("drill_report_no_ledger.txt", out, missing)
    assert not missing.exists()


def test_report_on_a_broken_ledger(tmp_path, capsys):
    directory = install_fixture("broken/edit_value", tmp_path / "broken")
    code, out, _ = report(directory, capsys)
    assert code == 1 and out.startswith("tampered: ")


def test_report_is_read_only(tmp_path, capsys):
    """Paths, sizes and mtimes are unchanged by drill report, and by --export apart from the
    export file."""
    directory = fx.write(tmp_path / "drills")
    before = listing(tmp_path)
    assert report(directory, capsys)[0] == 0
    assert listing(tmp_path) == before
    export = tmp_path / "summary.json"
    assert report(directory, capsys, "--export", str(export))[0] == 0
    after = listing(tmp_path)
    assert {k: v for k, v in after.items() if k != "summary.json"} == before
    assert "summary.json" in after


# --- the export ---------------------------------------------------------------------------

LITERALS = {
    "polarizer-drill-summary", "plain", "prediction-gate", "guided", "finished", "stopped",
    "interrupted", "none",
}  # fmt: skip
R_KEYS = {"percent", "low_percent", "high_percent"}
D_KEYS = {"points", "low_points", "high_points", "distinguishable"}
C_KEYS = {"drills", "planted", "caught", "clean", "false_flags", "catch", "false_flag",
          "median_ms", "over_300s", "catch_within_300s", "false_flag_within_300s"}  # fmt: skip
DRILL_KEYS = {"date", "condition", "calls", "answered", "ended", "planted", "caught", "clean",
              "false_flags", "median_ms", "over_300s"}  # fmt: skip
TOP_KEYS = {"format", "format_version", "polarizer_version", "scenario_sets", "first_date",
            "last_date", "drills", "answered", "repeats", "conditions", "difference",
            "guided_difference", "by_shape", "guided_by_shape", "per_drill"}  # fmt: skip
VERSION = re.compile(r"[0-9A-Za-z.+-]{1,32}")
DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")


def check_schema(doc) -> None:
    """Every key is in the schema, and every value is an integer, a boolean, null or an allowed
    string: one of the literals, a version, a set version of digits or a date."""
    assert set(doc) == TOP_KEYS
    assert doc["format"] == "polarizer-drill-summary" and doc["format_version"] == 2
    assert VERSION.fullmatch(doc["polarizer_version"])
    assert all(re.fullmatch("[0-9]+", v) for v in doc["scenario_sets"])
    assert DATE.fullmatch(doc["first_date"]) and DATE.fullmatch(doc["last_date"])
    assert set(doc["conditions"]) == {"plain", "prediction-gate", "guided"}
    for c in doc["conditions"].values():
        if c is None:
            continue
        assert set(c) == C_KEYS
        for key in ("catch", "false_flag", "catch_within_300s", "false_flag_within_300s"):
            assert c[key] is None or set(c[key]) == R_KEYS
    differences = [doc["difference"], *doc["guided_difference"].values()]
    assert set(doc["guided_difference"]) == {"minus_plain", "minus_prediction_gate"}
    for difference in differences:
        assert set(difference) == {"catch", "false_flag"}
        assert all(d is None or set(d) == D_KEYS for d in difference.values())
    for by_shape in (doc["by_shape"], doc["guided_by_shape"]):
        if by_shape is None:
            continue
        assert set(by_shape) == set(drill.scenarios.SHAPES)
        assert all(set(v) == {"planted", "caught"} for v in by_shape.values())
    for row in doc["per_drill"]:
        assert set(row) == DRILL_KEYS
        assert DATE.fullmatch(row["date"])

    def walk(value, key=None):
        if isinstance(value, dict):
            for k, v in value.items():
                walk(v, k)
        elif isinstance(value, list):
            for v in value:
                walk(v, key)
        elif isinstance(value, str):
            allowed = (
                value in LITERALS
                or key in ("date", "first_date", "last_date")
                and DATE.fullmatch(value)
                or key == "polarizer_version"
                and VERSION.fullmatch(value)
                or key == "scenario_sets"
                and re.fullmatch("[0-9]+", value)
                or key in ("format",)
                and value == "polarizer-drill-summary"
            )
            assert allowed, (key, value)
        else:
            assert value is None or type(value) in (int, bool), (key, value)

    walk(doc)


def test_export_schema(tmp_path, capsys):
    directory = fx.write(tmp_path / "drills")
    export = tmp_path / "summary.json"
    code, out, err = report(directory, capsys, "--export", str(export))
    assert code == 0 and err == ""
    check_golden("drill_report.txt", out)  # the report is printed as without --export
    data = export.read_bytes()
    assert data.endswith(b"\n") and data.count(b"\n") == 1
    doc = json.loads(data)
    assert data == (json.dumps(doc, sort_keys=True, separators=(",", ":")) + "\n").encode()
    check_schema(doc)
    assert doc["conditions"]["plain"]["catch"] == {"percent": 76, "low_percent": 52,
                                                   "high_percent": 91}  # fmt: skip
    assert doc["difference"]["catch"] == {"points": 12, "low_points": -15, "high_points": 37,
                                          "distinguishable": False}  # fmt: skip
    assert doc["drills"] == 7 and doc["answered"] == 116 and doc["repeats"] == 9
    assert doc["conditions"]["plain"]["over_300s"] == 1
    check_golden("drill_export.json", data.decode())


def test_export_has_no_free_text_paths_or_names(tmp_path, capsys):
    """The allowlist check, and none of the fixture's prediction text, ledger path, session ids,
    seed, scenario ids, hold ids, args_commit values or times of day in the file's bytes."""
    directory = fx.write(tmp_path / "drills")
    export = tmp_path / "summary.json"
    assert report(directory, capsys, "--export", str(export))[0] == 0
    data = export.read_bytes()
    check_schema(json.loads(data))
    entries = [json.loads(line) for line in (directory / "ledger.jsonl").read_text().splitlines()]
    assert fx.PREDICTION in (directory / "ledger.jsonl").read_text()  # it is in the ledger...
    forbidden = {fx.PREDICTION, str(directory), str(tmp_path), fx.SEED, "T09:30", "09:30:00"}
    for e in entries:
        data_ = e["data"]
        forbidden |= {data_.get(k) for k in ("session", "scenario", "hold", "args_commit")}
    forbidden.discard(None)
    for text in forbidden:
        assert text.encode() not in data, text  # ...and not in the export
    assert b"length" not in data and b'prediction"' not in data


def test_export_refusals(tmp_path, capsys):
    """An existing file and a ledger with no drills: the line, exit 2, nothing written."""
    directory = fx.write(tmp_path / "drills")
    existing = tmp_path / "taken.json"
    existing.write_text("mine\n")
    code, out, err = report(directory, capsys, "--export", str(existing))
    assert code == 2 and out == "" and existing.read_text() == "mine\n"
    assert err == f"polarizer: {existing} already exists; nothing was written\n"
    check_golden("drill_export_refused_existing.txt", err.replace(str(tmp_path), "<dir>"))
    empty = install_fixture("valid/minimal", tmp_path / "empty")
    target = tmp_path / "none.json"
    code, out, err = report(empty, capsys, "--export", str(target))
    assert code == 2 and out == "" and err == "polarizer: no drills to export\n"
    check_golden("drill_export_refused_no_drills.txt", err)
    assert not target.exists()


@pytest.mark.skipif(os.name == "nt", reason="POSIX directory permissions")
def test_export_cannot_write(tmp_path, capsys):
    directory = fx.write(tmp_path / "drills")
    target = tmp_path / "no-such-dir" / "summary.json"
    code, _, err = report(directory, capsys, "--export", str(target))
    assert code == 2 and err.startswith(f"polarizer: cannot write {target}: ")
