"""The parts of scripts/live-check.sh that are easier in Python: writing its two config files,
priming its ledger, and deciding pass or fail from the logs. Standard library only, apart from
`prime`, which imports Polarizer (live-check.sh runs this with the repo's .venv python).

    live_check.py setup <dir> <repo>   writes <dir>/polarizer.toml and <dir>/mcp.json
    live_check.py prime <dir>          records and approves the probe's tools (no model)
    live_check.py check <dir>          reads <dir>/wire.log, probe.log, ledger/ and claude.json

From M1a no tool is exposed until a person approves it, so the probe's wait would be hidden
from the model. `prime` does what a new user does once (docs/PIN-SPEC.md, section 7, First run):
`polarizer serve` with stdin closed, which lists the probe and records its definitions, then
the group approval `polarizer approve --group` would make, through the same library code.

The check passes only if both hold (docs/m0-plan.md, build step 7):
- the probe's log shows notifications/cancelled within 2 seconds of Claude Code's own cancel
  (taken from the wiretap between Claude Code and Polarizer);
- the ledger has a call.returned with outcome cancelled for that call.
Otherwise it exits 1 and names each failed check.
"""

import json
import sys
from pathlib import Path

SERVER = "pz"  # the server's name in mcp.json, so the tool is mcp__pz__probe__wait
TOOL = "probe__wait"
WITHIN = 2.0  # seconds from Claude Code's cancel to the probe's


def setup(directory: Path, repo: Path) -> None:
    python = repo / ".venv" / "bin" / "python"
    toml = (
        f"ledger_dir = {json.dumps((directory / 'ledger').as_posix())}\n\n"
        "[upstream.probe]\n"
        f"command = {json.dumps(str(python))}\n"
        f"args = [{json.dumps(str(repo / 'tests' / 'helpers' / 'probe_server.py'))}]\n"
        f"env = {{ PROBE_LOG = {json.dumps(str(directory / 'probe.log'))} }}\n"
    )
    (directory / "polarizer.toml").write_text(toml, encoding="utf-8")
    serve = ["-m", "polarizer", "serve", "--config", str(directory / "polarizer.toml")]
    config = {
        "mcpServers": {
            SERVER: {
                "command": str(python),
                "args": [
                    str(repo / "scripts" / "wiretap.py"),
                    str(directory / "wire.log"),
                    str(python),
                    *serve,
                ],
            }
        }
    }
    (directory / "mcp.json").write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")


def prime(directory: Path) -> int:
    """Prime <dir>/ledger: a serve run with stdin closed, then approve the pending group.
    Prints one line and returns the exit code."""
    import subprocess

    from polarizer.decisions import Decider

    config = directory / "polarizer.toml"
    serve = [sys.executable, "-m", "polarizer", "serve", "--config", str(config)]
    done = subprocess.run(serve, stdin=subprocess.DEVNULL, capture_output=True, timeout=120)
    if done.returncode != 0:
        print(f"prime: serve exited {done.returncode}: {done.stderr.decode('utf-8', 'replace')}")
        return 1
    decider = Decider.open(directory / "ledger")
    try:
        members, group_id = decider.pending_group()
        if group_id is None:
            print("prime: nothing to approve")
            return 1
        errors: list[str] = []
        code = decider.approve_group(group_id, None, lambda line: None, errors.append)
    finally:
        decider.close()
    if code != 0:
        print(f"prime: approving failed: {'; '.join(errors)}")
        return 1
    print(f"prime: approved {len(members)} definitions as group {group_id}")
    return 0


def _messages(lines: list[str], direction: str | None = None) -> list[tuple[float, dict]]:
    """(time, message) for each JSON line of a wiretap log (`<t> <dir> <json>`, filtered by
    direction) or a probe log (`<t> <json>`, direction None)."""
    found = []
    for line in lines:
        parts = line.split(" ", 2 if direction else 1)
        if direction and (len(parts) < 3 or parts[1] != direction):
            continue
        try:
            found.append((float(parts[0]), json.loads(parts[-1])))
        except ValueError:
            continue  # the probe's start line, or the wiretap's <end of input>
    return found


def evaluate(wire: list[str], probe: list[str], ledger: list[dict]) -> list[tuple[str, bool, str]]:
    """(check, passed, detail) for each check, in order. The last two decide the result."""
    results = []
    calls = [m for _, m in _messages(probe) if m.get("method") == "tools/call"]
    called = any((m.get("params") or {}).get("name") == "wait" for m in calls)
    results.append(
        (
            "the model called probe__wait",
            called,
            "the probe received tools/call wait"
            if called
            else "no tools/call reached the probe (the model's choice, outside Polarizer)",
        )
    )

    sent_by_claude = [
        t for t, m in _messages(wire, "c2s") if m.get("method") == "notifications/cancelled"
    ]
    got_by_probe = [t for t, m in _messages(probe) if m.get("method") == "notifications/cancelled"]
    if not sent_by_claude:
        ok, detail = False, "Claude Code sent no notifications/cancelled (see wire.log)"
    elif not got_by_probe:
        ok, detail = False, "the probe's log has no notifications/cancelled"
    else:
        gap = got_by_probe[0] - sent_by_claude[0]
        ok = -0.01 <= gap <= WITHIN  # both logs round to 1 ms
        detail = (
            f"Claude Code cancelled at {sent_by_claude[0]:.3f}, the probe got it {gap:.3f} s later"
        )
    results.append(
        ("the probe got notifications/cancelled within 2 s of Claude Code's", ok, detail)
    )

    sent = {e["seq"]: e for e in ledger if e["kind"] == "call.sent" and e["data"]["tool"] == TOOL}
    cancelled = [
        e
        for e in ledger
        if e["kind"] == "call.returned"
        and e["data"]["call_seq"] in sent
        and e["data"]["outcome"] == "cancelled"
    ]
    ok = bool(cancelled)
    if ok:
        entry = cancelled[0]
        detail = (
            f"call.returned at seq {entry['seq']} for call.sent at seq {entry['data']['call_seq']}"
        )
    else:
        outcomes = [e["data"]["outcome"] for e in ledger if e["kind"] == "call.returned"]
        detail = f"{len(sent)} {TOOL} call(s) in the ledger; outcomes recorded: {outcomes}"
    results.append(
        ("the ledger has call.returned with outcome cancelled for that call", ok, detail)
    )
    return results


def check(directory: Path) -> int:
    def lines(name: str) -> list[str]:
        path = directory / name
        return path.read_text(encoding="utf-8").splitlines() if path.exists() else []

    ledger = [json.loads(line) for line in lines("ledger/ledger.jsonl")]
    results = evaluate(lines("wire.log"), lines("probe.log"), ledger)
    for name, ok, detail in results:
        print(f"{'PASS' if ok else 'FAIL'}  {name}: {detail}")
    failed = [name for name, ok, _ in results[1:] if not ok]
    if not results[0][1]:
        failed.insert(0, results[0][0])
    if failed:
        print(f"live check FAILED: {'; '.join(failed)}")
        return 1
    print("live check passed")
    return 0


def main(argv: list[str]) -> int:
    if len(argv) == 4 and argv[1] == "setup":
        setup(Path(argv[2]), Path(argv[3]))
        return 0
    if len(argv) == 3 and argv[1] == "prime":
        return prime(Path(argv[2]))
    if len(argv) == 3 and argv[1] == "check":
        return check(Path(argv[2]))
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
