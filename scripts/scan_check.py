"""The scan check on real Claude Code, headless (docs/SCAN-SPEC.md, section 12).

Manual only: it calls a model, so it costs money and never runs in CI. Run it with the repo's
.venv python on Linux or WSL, from a plain terminal:

    .venv/bin/python scripts/scan_check.py

What it does, with no questions:
1. makes a directory under the repo's gitignored .repro/ (WSL may clear /tmp when idle): a
   polarizer.toml whose ledger_dir is inside it, with tests/helpers/lab_stub.py as the upstream
   "lab" (add and echo are clean, lookup's description is poisoned, and its instructions are
   clean), and an mcp.json that starts `polarizer serve` with that config;
2. records what the server lists as a new user does once (serve with stdin closed), with no
   approval, and checks `polarizer pending`: lookup is flagged and left out of the group;
3. starts `polarizer card --config ... --allow-no-terminal` and, with curl only, checks the
   review view (the same flags, the group of the clean tools and the instructions), then
   approves the group with the card's `Approve all unflagged`;
4. run 1, `claude -p --output-format stream-json`: Claude Code's start event must list add and
   echo and not lookup; the model calls add, and repeats the pass phrase from the approved
   instructions if it got them;
5. approves lookup alone through the card;
6. run 2, `claude -p`, told to call no tool: the start event must now list lookup;
7. checks the ledger (the group's three approvals, lookup's alone with its flags, no call to
   lookup), and `polarizer verify`; then stops the card.

With --setup it does steps 1 and 2, prints the commands for the owner's interactive check
(docs/dev/MANUAL-CHECK.md, M1b), and exits: no model is called.

Every run uses the pinned model, --max-budget-usd 1, --strict-mcp-config --mcp-config with the
generated file, and --no-session-persistence; only add is allowed, so a call to lookup would be
refused by Claude Code's own permissions in -p mode. Nothing of Claude Code's is read, and
~/.claude.json is only stat'ed. Every check prints PASS or FAIL; the script exits 0 only if all
pass, and leaves its directory in place.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
import card_check  # noqa: E402  (the card's process, curl and reporting, reused)

MODEL = card_check.MODEL
BUDGET = card_check.BUDGET
SERVER = "pz"
WAIT_FOR_CLAUDE = 420.0
PHRASE = "pelican-42"  # in tests/helpers/lab_stub.py's instructions
TOOL = f"mcp__{SERVER}__lab__"
GROUP_NAMES = ["lab__:instructions", "lab__add", "lab__echo"]
LOOKUP_RULES = ["conceal-from-user", "instruction-tag", "read-before-use", "secret-path"]


def setup(directory: Path, polarizer: Path) -> dict:
    """Write polarizer.toml and mcp.json; return their paths."""
    python = REPO / ".venv" / "bin" / "python"
    stub = REPO / "tests" / "helpers" / "lab_stub.py"
    calls = directory / "lookup-calls.jsonl"
    toml = (
        f"ledger_dir = {json.dumps((directory / 'ledger').as_posix())}\n\n"
        "[upstream.lab]\n"
        f"command = {json.dumps(str(python))}\n"
        f"args = [{json.dumps(str(stub))}]\n"
        f"env = {{ LAB_STUB_LOG = {json.dumps(str(calls))} }}\n"
        "\n[upstream.lab.tools]\n"
        'add = { class = "local-read" }\n'
        'echo = { class = "local-read" }\n'
        'lookup = { class = "local-read" }\n'
    )
    config = directory / "polarizer.toml"
    config.write_text(toml, encoding="utf-8")
    mcp = {
        "mcpServers": {
            SERVER: {"command": str(polarizer), "args": ["serve", "--config", str(config)]}
        }
    }
    mcp_path = directory / "mcp.json"
    mcp_path.write_text(json.dumps(mcp, indent=2) + "\n", encoding="utf-8")
    return {"config": config, "mcp": mcp_path, "ledger": directory / "ledger", "calls": calls}


def record(config: Path, polarizer: Path) -> str:
    """serve with stdin closed: it lists the server and records what it sees. No approval."""
    done = subprocess.run(
        [str(polarizer), "serve", "--config", str(config)],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        timeout=180,
    )
    if done.returncode != 0:
        raise SystemExit(f"scan-check: serve exited {done.returncode}: {done.stderr!r}")
    return done.stderr.decode("utf-8", "replace").strip()


def pending(polarizer: Path, config: Path) -> str:
    done = subprocess.run(
        [str(polarizer), "pending", "--config", str(config)], capture_output=True, text=True
    )
    return done.stdout


def check_pending(text: str, report, after_group: bool = False) -> None:
    lines = text.splitlines()
    report.say("== polarizer pending" + "".join("\n  " + x for x in lines))
    headers = [i for i, x in enumerate(lines) if x.startswith("new lab__lookup ")]
    if not report.check("pending lists lookup as new", len(headers) == 1):
        return
    i = headers[0]
    report.check(
        "lookup's block says it is flagged and not grouped",
        lines[i + 1] == "flagged by the scanner; read it, then approve it by name",
    )
    flags = [x for x in lines[i + 2 :] if x.startswith("flag ")][:4]
    report.check(
        "lookup's flags name the instruction tag, the read before use, the secret path and the "
        "line kept from the user",
        sorted(x.split()[1] for x in flags) == LOOKUP_RULES,
        "; ".join(x.split(" (")[0] for x in flags),
    )
    if after_group:
        report.check(
            "after the group, only lookup waits",
            lines[0] == "pending: 1 new, 0 changed, 0 unservable" and "\ngroup " not in text,
            lines[0],
        )
    else:
        report.check(
            "the group line covers the three unflagged definitions",
            lines[-1].endswith(
                "covers the 3 unflagged new definitions above for tools with no decision yet "
                "and one definition waiting"
            ),
            lines[-1],
        )


def review(curl) -> dict:
    code, body = curl.run(
        "-b", str(curl.jar), "-H", f"X-Polarizer-Nonce: {curl.nonce}", f"{curl.base}/review"
    )
    if code != 200:
        raise RuntimeError(f"GET /review answered {code}")
    return json.loads(body)


def approve(curl, payload: dict) -> tuple[int, dict]:
    code, answer = curl.run(
        "-b",
        str(curl.jar),
        "-H",
        f"X-Polarizer-Nonce: {curl.nonce}",
        "-H",
        "Content-Type: application/json",
        "-H",
        f"Origin: {curl.base}",
        "--data-binary",
        "@-",
        f"{curl.base}/approve",
        data=json.dumps(payload).encode(),
    )
    return code, json.loads(answer) if answer.startswith(b"{") else {"raw": answer.decode()}


def check_review(found: dict, report) -> dict | None:
    by_name = {d["name"]: d for d in found["definitions"]}
    report.say("== the card's review view")
    for name, item in by_name.items():
        report.say(f"  {name}: in_group {item['in_group']}, {len(item['flags'])} flags")
        for line in item["flags"]:
            report.say(f"    {line}")
    lookup = by_name.get("lab__lookup")
    report.check(
        "the review shows lookup's flags beside its definition, and not in the group",
        lookup is not None
        and sorted({x.split()[1] for x in lookup["flags"]}) == LOOKUP_RULES
        and lookup["in_group"] is False
        and lookup["definition"][0] == "{",
    )
    group = found.get("group")
    report.check(
        "the review's group covers the clean tools and the instructions",
        group is not None and group["names"] == GROUP_NAMES,
        str(group and group["names"]),
    )
    return group


def run_claude(prompt: str, paths: dict, directory: Path, name: str, report) -> subprocess.Popen:
    args = [
        "-p",
        prompt,
        "--model",
        MODEL,
        "--max-budget-usd",
        BUDGET,
        "--strict-mcp-config",
        "--mcp-config",
        str(paths["mcp"]),
        f"--allowedTools={TOOL}add",
        "--permission-mode",
        "default",
        "--no-session-persistence",
        "--output-format",
        "stream-json",
        "--verbose",
    ]
    report.say("claude arguments: " + " ".join(json.dumps(a) for a in args) + " < /dev/null")
    env = {k: v for k, v in os.environ.items() if k != "MCP_TOOL_TIMEOUT"}
    return subprocess.Popen(
        ["claude", *args],
        cwd=directory,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=open(directory / f"{name}.jsonl", "wb"),
        stderr=open(directory / f"{name}.stderr", "wb"),
    )


def finish(claude, directory: Path, name: str, report) -> tuple[list, dict]:
    """(the tools Claude Code's start event listed, the result event)."""
    try:
        claude.wait(timeout=WAIT_FOR_CLAUDE)
    except subprocess.TimeoutExpired:
        claude.kill()
        claude.wait()
        report.say("claude: killed after the time limit")
    report.say(f"claude exit code: {claude.returncode}")
    events = []
    for line in (directory / f"{name}.jsonl").read_text(encoding="utf-8").splitlines():
        try:
            events.append(json.loads(line))
        except ValueError:
            continue
    init = next((e for e in events if e.get("type") == "system" and e.get("subtype") == "init"), {})
    result = next((e for e in events if e.get("type") == "result"), {})
    tools = [t for t in init.get("tools", []) if t.startswith(f"mcp__{SERVER}__")]
    servers = init.get("mcp_servers")
    report.say(f"start event: mcp_servers {servers}; {SERVER} tools {tools}")
    report.say(f"model reply: {result.get('result')!r}")
    report.say(f"cost: {result.get('total_cost_usd')} USD (claude's own total_cost_usd)")
    return tools, result


def lookup_calls(paths) -> list:
    path = paths["calls"]
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def check_ledger(paths, report, group_id) -> None:
    found = card_check.entries(paths["ledger"])
    approved = [e for e in found if e["kind"] == "tool.approved"]
    for e in approved:
        report.say(f"seq {e['seq']} tool.approved: {json.dumps(e['data'], sort_keys=True)}")
    in_group = [e["data"]["tool"] for e in approved if e["data"]["group"] == group_id]
    report.check(
        "the group recorded one tool.approved each for the instructions, add and echo",
        group_id is not None and in_group == [":instructions", "add", "echo"],
        str(in_group),
    )
    alone = [e["data"] for e in approved if e["data"]["tool"] == "lookup"]
    report.check(
        "lookup was approved alone, after the group, with its flags",
        len(alone) == 1 and alone[0]["group"] is None and alone[0].get("flags") == LOOKUP_RULES,
        str(alone),
    )
    report.check(
        "every approval through the card is marked terminal false",
        all(e["data"].get("terminal") is False for e in approved),
    )
    sent = [e["data"]["tool"] for e in found if e["kind"] == "call.sent"]
    report.say(f"call.sent tools: {sent}")
    report.check("run 1 called add through Polarizer", "lab__add" in sent)
    report.check(
        "lookup was never called",
        "lab__lookup" not in sent and lookup_calls(paths) == [],
        str(lookup_calls(paths)),
    )


def setup_only(polarizer: Path) -> int:
    """--setup: the directory, the config and a recorded ledger, and the commands to type."""
    (REPO / ".repro").mkdir(exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix="scan-manual.", dir=REPO / ".repro"))
    paths = setup(directory, polarizer)
    print(record(paths["config"], polarizer))
    print(f"directory: {directory}")
    print(f"terminal B: {polarizer} pending --config {paths['config']}")
    print(f"terminal C: {polarizer} card --config {paths['config']}")
    print(f"terminal A: claude --mcp-config {paths['mcp']} --strict-mcp-config")
    print(f"afterwards: {polarizer} verify --config {paths['config']}")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="the scan check on real Claude Code")
    parser.add_argument(
        "--setup", action="store_true", help="write the config and record it, for a person"
    )
    args = parser.parse_args(argv)
    polarizer = REPO / ".venv" / "bin" / "polarizer"
    if not polarizer.exists():
        print(f"scan-check: {polarizer} not found; run uv sync --locked first", file=sys.stderr)
        return 2
    if args.setup:
        return setup_only(polarizer)
    for command in ("claude", "curl"):
        if shutil.which(command) is None:
            print(f"scan-check: {command} is not on PATH", file=sys.stderr)
            return 2
    (REPO / ".repro").mkdir(exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix="scan-check.", dir=REPO / ".repro"))
    report = card_check.Report(directory / "scan-check-results.txt")
    report.say(f"== conditions, {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}")
    version = subprocess.run(["claude", "--version"], capture_output=True, text=True).stdout.strip()
    commit = subprocess.run(
        ["git", "-C", str(REPO), "rev-parse", "--short", "HEAD"], capture_output=True, text=True
    ).stdout.strip()
    report.say(f"claude version: {version}; model {MODEL}; budget {BUDGET} USD per run")
    report.say(f"polarizer commit: {commit or 'unknown'}; directory: {directory}")
    names = sorted(k for k in os.environ if k.startswith(("CLAUDE", "ANTHROPIC")))
    report.say("CLAUDE_* and ANTHROPIC_* variables set (names only): " + " ".join(names))
    before = card_check.claude_json_stat()
    paths = setup(directory, polarizer)
    report.say("== recording what the server lists (serve with stdin closed; no approval)")
    report.say(record(paths["config"], polarizer))
    check_pending(pending(polarizer, paths["config"]), report)
    card, port, token = card_check.start_card(paths["config"], polarizer, directory)
    report.say(f"== the card on port {port}")
    group_id = None
    try:
        curl = card_check.Curl(directory, port, token)
        report.check(
            "the page loads with the token, and has a nonce",
            curl.open_page() == 200 and curl.nonce is not None,
        )
        group = check_review(review(curl), report)
        if group is not None:
            group_id = group["id"]
            code, answer = approve(curl, {"group": group_id})
            report.say(
                "approve all unflagged:" + "".join("\n  " + x for x in answer.get("lines", []))
            )
            report.check(
                "Approve all unflagged through the card",
                code == 200 and answer.get("ok") is True,
                answer.get("error", ""),
            )
        report.say("== run 1: after the group")
        prompt = (
            f"Call {TOOL}add once with a=2 and b=3, and use no other tool. Then reply in one "
            f"line: the sum; whether a tool named {TOOL}lookup is available to you; and the lab "
            "pass phrase, if this session's MCP server instructions give one."
        )
        tools, result = finish(
            run_claude(prompt, paths, directory, "claude1", report), directory, "claude1", report
        )
        report.check(
            "Claude Code lists add and echo, and not lookup",
            f"{TOOL}add" in tools and f"{TOOL}echo" in tools and f"{TOOL}lookup" not in tools,
            str(tools),
        )
        report.say(
            "the pass phrase from the approved instructions in the reply: "
            + ("yes" if PHRASE in str(result.get("result")) else "no")
        )
        check_pending(pending(polarizer, paths["config"]), report, after_group=True)
        found = review(curl)
        lookup = next((d for d in found["definitions"] if d["name"] == "lab__lookup"), None)
        report.check(
            "the review still shows lookup, flagged", lookup is not None and lookup["flags"]
        )
        if lookup is not None:
            body = {"upstream": "lab", "tool": "lookup", "def_hash": lookup["def_hash"]}
            code, answer = approve(curl, body)
            report.say("approve lookup:" + "".join("\n  " + x for x in answer.get("lines", [])))
            report.check(
                "lookup approved alone through the card",
                code == 200 and answer.get("ok") is True,
                answer.get("error", ""),
            )
        report.say("== run 2: after lookup was approved alone")
        prompt = "Reply with the single word ready. Do not call any tool."
        tools, _ = finish(
            run_claude(prompt, paths, directory, "claude2", report), directory, "claude2", report
        )
        report.check("Claude Code now lists lookup", f"{TOOL}lookup" in tools, str(tools))
        check_ledger(paths, report, group_id)
        done = subprocess.run(
            [str(polarizer), "verify", "--ledger-dir", str(paths["ledger"])],
            capture_output=True,
            text=True,
        )
        report.check("polarizer verify", done.returncode == 0, done.stdout.strip())
    finally:
        card_check.stop_card(card, report)
    report.say(
        f"~/.claude.json bookkeeping: before {before}; after {card_check.claude_json_stat()}"
    )
    report.say(
        f"== {'all checks passed' if report.failed == 0 else f'{report.failed} checks failed'}"
    )
    report.say(f"results: {report.path}")
    return 0 if report.failed == 0 else 1


if __name__ == "__main__":
    sys.path.insert(0, str(REPO / "src"))
    sys.exit(main())
