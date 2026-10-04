"""The parts of scripts/rugpull-check.sh that are easier in Python: writing its config files,
priming its ledger, approving the changed definition, reporting each Claude Code run, and
deciding pass or fail from the ledger and the probe's log. `prime` and `approve` import
Polarizer (rugpull-check.sh runs this with the repo's .venv python); the rest is stdlib only.

    rugpull_check.py setup <dir> <repo>   writes polarizer.toml, mcp-original.json, mcp-changed.json
    rugpull_check.py prime <dir>          records and approves the original definitions (no model)
    rugpull_check.py mark <dir> <name>    records how long the ledger and the probe log are now
    rugpull_check.py approve <dir>        approves probe wait's changed definition (no model)
    rugpull_check.py report <dir> <run>   one run's exit code, reply and cost
    rugpull_check.py costs <dir>          each run's cost and the total
    rugpull_check.py check <dir>          the checks, from ledger/ and probe.log

The two mcp configs differ only in PROBE_PHASE, which Polarizer passes to the probe through
polarizer.toml's "${PROBE_PHASE}" (docs/PROXY-SPEC.md, the upstream's environment). So the
probe's definitions depend only on which config Claude Code was given, never on how many
times anything started.

Each run's part of the ledger and of the probe log is what was appended between its two marks
(`<run>.start` and `<run>.end`). The model's replies are printed for information only; no check
reads them.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

from live_check import _messages, probe_classes
from live_check import prime as prime_ledger
from m1a_check import printable

SERVER = "pz"  # the server's name in the mcp configs, so the tool is mcp__pz__probe__wait
TOOL = "probe__wait"
PHASES = ("original", "changed")
RUNS = {"A": "original", "B": "changed", "C": "changed"}
FIRST_LINES = 8


def setup(directory: Path, repo: Path) -> None:
    python = repo / ".venv" / "bin" / "python"
    toml = (
        f"ledger_dir = {json.dumps((directory / 'ledger').as_posix())}\n\n"
        "[upstream.probe]\n"
        f"command = {json.dumps(str(python))}\n"
        f"args = [{json.dumps(str(repo / 'tests' / 'helpers' / 'probe_server.py'))}]\n"
        f"env = {{ PROBE_LOG = {json.dumps(str(directory / 'probe.log'))}, "
        'PROBE_PHASE = "${PROBE_PHASE}" }\n' + probe_classes()
    )
    (directory / "polarizer.toml").write_text(toml, encoding="utf-8")
    serve = ["-m", "polarizer", "serve", "--config", str(directory / "polarizer.toml")]
    for phase in PHASES:
        server = {"command": str(python), "args": serve, "env": {"PROBE_PHASE": phase}}
        config = {"mcpServers": {SERVER: server}}
        text = json.dumps(config, indent=2) + "\n"
        (directory / f"mcp-{phase}.json").write_text(text, encoding="utf-8")


def prime(directory: Path) -> int:
    """live-check's priming, with the probe in its original phase."""
    os.environ["PROBE_PHASE"] = "original"
    return prime_ledger(directory)


def approve(directory: Path) -> int:
    """What a person does after `polarizer pending` shows the changed probe__wait: approve its
    new hash by name, through the library code `polarizer approve probe wait <hash>` runs."""
    from polarizer import pins
    from polarizer.decisions import Decider, Refusal

    try:
        decider = Decider.open(directory / "ledger")
    except Refusal as e:
        print(f"approve: {e.line}")
        return 1
    try:
        found = pins.blocks(decider.pins, decider.ledger_dir, "probe")
        changed = [b for b in found if b.kind == "changed" and b.tool == "wait"]
        if len(changed) != 1:
            kinds = [f"{b.kind} {b.upstream}__{b.tool}" for b in found]
            print(f"approve: expected one changed probe__wait; pending has {kinds or 'nothing'}")
            return 1
        block = changed[0]
        print(f"approve: changed probe__wait {block.def_hash}, approved {block.approved}")
        decider.approve_one("probe", "wait", block.def_hash, lambda line: print(f"  {line}"))
    except Refusal as e:
        print(f"approve: {e.line}")
        return 1
    finally:
        decider.close()
    return 0


def _lines(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8", errors="replace").splitlines() if path.exists() else []


def mark(directory: Path, name: str) -> None:
    path = directory / "marks.json"
    marks = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    marks[name] = {
        "ledger": len(_lines(directory / "ledger" / "ledger.jsonl")),
        "probe": len(_lines(directory / "probe.log")),
    }
    path.write_text(json.dumps(marks, indent=2) + "\n", encoding="utf-8")


# --- a run's own record: run-<X>.code, run-<X>.out (claude's stdout) and run-<X>.err


def run_state(directory: Path, run: str) -> dict:
    """code (None if claude never ran), out (claude's JSON result or None) and head (the first
    lines of its stdout and stderr, printable)."""
    code_file = directory / f"run-{run}.code"
    if not code_file.exists():
        return {"code": None, "out": None, "head": []}
    code = int(code_file.read_text(encoding="utf-8").strip() or "-1")
    stdout = (directory / f"run-{run}.out").read_text(encoding="utf-8", errors="replace")
    stderr = _lines(directory / f"run-{run}.err")
    try:
        out = json.loads(stdout)
    except ValueError:
        out = None
    if isinstance(out, list):  # a stream of messages; the result is the one typed "result"
        out = next((m for m in out if isinstance(m, dict) and m.get("type") == "result"), None)
    if not isinstance(out, dict):
        out = None
    head = [f"stdout: {printable(x)[:200]}" for x in stdout.splitlines()[:FIRST_LINES]]
    head += [f"stderr: {printable(x)[:200]}" for x in stderr[:FIRST_LINES]]
    return {"code": code, "out": out, "head": head}


def run_problem(directory: Path, run: str) -> str | None:
    """Why run <run>'s checks can't be evaluated, or None if claude ran and exited 0."""
    state = run_state(directory, run)
    if state["code"] is None:
        return f"run {run} did not run claude (see the lines before the checks)"
    if state["code"] != 0:
        head = " | ".join(state["head"]) or "no output"
        return f"run {run}: claude exited {state['code']}; first lines: {head}"
    return None


def report(directory: Path, run: str) -> int:
    state = run_state(directory, run)
    if state["code"] is None:
        print(f"run {run}: claude did not run")
        return 0
    print(f"run {run}: claude exit code {state['code']}")
    out = state["out"]
    if out is None or state["code"] != 0:
        print(f"run {run}: first lines of claude's output:")
        for line in state["head"] or ["(none)"]:
            print(f"  {line}")
    if out is not None:
        reply = printable(str(out.get("result")))[:400]
        print(f"run {run}: model reply (information only, not used for pass or fail): {reply}")
        print(f"run {run}: cost {out.get('total_cost_usd')} USD (claude's own total_cost_usd)")
    return 0


def costs(directory: Path) -> int:
    total, unknown = 0.0, []
    for run in RUNS:
        out = run_state(directory, run)["out"] or {}
        cost = out.get("total_cost_usd")
        if isinstance(cost, int | float) and not isinstance(cost, bool):
            total += cost
            print(f"run {run}: {cost} USD")
        else:
            unknown.append(run)
            print(f"run {run}: unknown")
    more = f" (runs {', '.join(unknown)} reported no cost)" if unknown else ""
    print(f"total: {round(total, 6)} USD{more}")
    return 0


# --- the checks


def _slice(items: list, marks: dict, start: str, end: str, key: str) -> list:
    if start not in marks or end not in marks:
        return []
    return items[marks[start][key] : marks[end][key]]


def _wait_calls(probe_lines: list[str]) -> list[dict]:
    return [
        m
        for _, m in _messages(probe_lines)
        if m.get("method") == "tools/call" and (m.get("params") or {}).get("name") == "wait"
    ]


def _call_checks(run: str, problem, ledger: list[dict], part: list[dict], probe: list[str]):
    """A's and C's two checks: the probe got the call, and the ledger recorded it as ok."""
    names = (
        f"{run}: the probe received tools/call wait",
        f"{run}: the ledger has call.sent and call.returned outcome ok for {TOOL}",
    )
    if problem:
        return [(name, False, problem) for name in names]
    calls = _wait_calls(probe)
    results = [
        (
            names[0],
            bool(calls),
            f"{len(calls)} tools/call wait in the probe's log during run {run}",
        )
    ]
    sent = [e for e in part if e["kind"] == "call.sent" and e["data"].get("tool") == TOOL]
    seqs = {e["seq"] for e in sent}
    returned = [
        e for e in ledger if e["kind"] == "call.returned" and e["data"].get("call_seq") in seqs
    ]
    ok = [e for e in returned if e["data"].get("outcome") == "ok"]
    if ok:
        detail = (
            f"call.sent at seq {ok[0]['data']['call_seq']}, call.returned ok at seq {ok[0]['seq']}"
        )
    else:
        outcomes = [e["data"].get("outcome") for e in returned]
        detail = f"{len(sent)} call.sent for {TOOL} during run {run}; outcomes: {outcomes}"
    results.append((names[1], bool(ok), detail))
    return results


def evaluate(
    ledger: list[dict],
    probe: list[str],
    marks: dict,
    problems: dict,
    verified: tuple[bool, str],
) -> list[tuple[str, bool, str]]:
    """(check, passed, detail) for each check, in order. `problems` maps a run to why its checks
    can't be evaluated (None when claude exited 0); `verified` is polarizer verify's verdict."""

    def part(run):
        return _slice(ledger, marks, f"{run}.start", f"{run}.end", "ledger")

    def probe_part(run):
        return _slice(probe, marks, f"{run}.start", f"{run}.end", "probe")

    results = _call_checks("A", problems.get("A"), ledger, part("A"), probe_part("A"))

    names = (
        "B: the ledger has a tool.drift for probe wait whose approved_hash and live_hash differ",
        "B: the probe received no tools/call wait",
        f"B: the ledger has no call.sent for {TOOL} in B's session",
    )
    drift = None
    if problems.get("B"):
        results += [(name, False, problems["B"]) for name in names]
    else:
        b = part("B")
        drifts = [
            e
            for e in b
            if e["kind"] == "tool.drift"
            and (e["data"].get("upstream"), e["data"].get("tool")) == ("probe", "wait")
            and e["data"].get("approved_hash") != e["data"].get("live_hash")
        ]
        if drifts:
            drift = drifts[0]
            d = drift["data"]
            detail = (
                f"tool.drift at seq {drift['seq']}: approved {d['approved_hash'][:16]}..., "
                f"live {d['live_hash'][:16]}..."
            )
        else:
            anywhere = sum(e["kind"] == "tool.drift" for e in ledger)
            detail = f"none during run B; tool.drift entries in the whole ledger: {anywhere}"
        results.append((names[0], bool(drifts), detail))

        sessions = {e["data"]["session"] for e in b if e["kind"] == "session.started"}
        started = any(m.split(" ", 2)[1:2] == ["start"] for m in probe_part("B"))
        if not sessions or not started:
            why = (
                "Polarizer did not start during run B"
                if not sessions
                else "the probe did not start during run B"
            )
            detail = f"{why}, so the absence of a call proves nothing"
            results += [(names[1], False, detail), (names[2], False, detail)]
        else:
            calls = _wait_calls(probe_part("B"))
            in_b = {e["seq"] for e in b}
            results.append(
                (names[1], not calls, f"{len(calls)} tools/call wait in the probe's log")
            )
            sent = [
                e
                for e in ledger
                if e["kind"] == "call.sent"
                and e["data"].get("tool") == TOOL
                and (e["data"].get("session") in sessions or e["seq"] in in_b)
            ]
            refused = [
                e
                for e in ledger
                if e["kind"] == "call.refused" and e["data"].get("session") in sessions
            ]
            detail = (
                f"session {', '.join(sorted(sessions))}: {len(sent)} call.sent for {TOOL}, "
                f"{len(refused)} call.refused (information only)"
            )
            results.append((names[2], not sent, detail))

    name = "approval: the ledger has tool.approved for probe wait's new hash"
    if drift is None:
        results.append((name, False, "no drift was found during run B, so there is no new hash"))
    else:
        live = drift["data"]["live_hash"]
        approved = [
            e
            for e in ledger
            if e["kind"] == "tool.approved"
            and e["seq"] > drift["seq"]
            and (e["data"].get("upstream"), e["data"].get("tool")) == ("probe", "wait")
            and e["data"].get("def_hash") == live
        ]
        detail = (
            f"tool.approved at seq {approved[0]['seq']} for {live[:16]}..."
            if approved
            else f"no tool.approved for {live[:16]}... after the drift"
        )
        results.append((name, bool(approved), detail))

    results += _call_checks("C", problems.get("C"), ledger, part("C"), probe_part("C"))
    results.append(("the ledger verifies as intact", *verified))
    return results


def verify(directory: Path) -> tuple[bool, str]:
    done = subprocess.run(
        [sys.executable, "-m", "polarizer", "verify", "--ledger-dir", str(directory / "ledger")],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        errors="replace",
        timeout=120,
    )
    said = " | ".join(printable(x) for x in (done.stdout + done.stderr).splitlines())
    return done.returncode == 0, f"polarizer verify exit code {done.returncode}: {said}"


def check(directory: Path) -> int:
    ledger = []
    for line in _lines(directory / "ledger" / "ledger.jsonl"):
        try:
            entry = json.loads(line)
        except ValueError:
            continue  # verify reports it
        if isinstance(entry, dict) and isinstance(entry.get("data"), dict):
            ledger.append(entry)
    path = directory / "marks.json"
    marks = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    problems = {run: run_problem(directory, run) for run in RUNS}
    results = evaluate(ledger, _lines(directory / "probe.log"), marks, problems, verify(directory))
    for name, ok, detail in results:
        print(f"{'PASS' if ok else 'FAIL'}  {name}: {detail}")
    failed = [name for name, ok, _ in results if not ok]
    if failed:
        print(f"rugpull check FAILED: {len(failed)} of {len(results)} checks failed")
        return 1
    print("rugpull check passed")
    return 0


def main(argv: list[str]) -> int:
    match argv[1:]:
        case ["setup", directory, repo]:
            setup(Path(directory), Path(repo))
            return 0
        case ["prime", directory]:
            return prime(Path(directory))
        case ["mark", directory, name]:
            mark(Path(directory), name)
            return 0
        case ["approve", directory]:
            return approve(Path(directory))
        case ["report", directory, run] if run in RUNS:
            return report(Path(directory), run)
        case ["costs", directory]:
            return costs(Path(directory))
        case ["check", directory]:
            return check(Path(directory))
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
