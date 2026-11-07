"""The taint check on real Claude Code, headless (docs/TAINT-SPEC.md, section 13).

Manual only: it calls a model, so it costs money and never runs in CI. Run it with the repo's
.venv python on Linux or WSL, from a plain terminal:

    .venv/bin/python scripts/taint_check.py

What it does, with no questions:
1. makes a directory under the repo's gitignored .repro/ (WSL may clear /tmp when idle): a
   polarizer.toml whose ledger_dir is inside it, with tests/helpers/web_stub.py as the upstream
   "web" (fetch is open-world; post is egress with url_args ["url"] and the domain
   allowed.example), and an mcp.json that starts `polarizer serve` with that config;
2. primes the ledger as a new user does once (serve with stdin closed, then the group approved
   through the library, no model), and starts `polarizer card --config ... --allow-no-terminal`;
3. run 1, `claude -p`: a post to allowed.example, a fetch, then the same post again. The first
   post must run as a grant; the second must be held by `tainted`, its reason naming web__fetch,
   the time and the seq. The script allows it through the card, with curl only;
4. run 2, `claude -p --resume <the same session>`: a new serve process in the same Claude Code
   session. The same post must be held by `tainted` at once, naming the same source; the script
   denies it through the card;
5. run 3, a new Claude Code session: the same post runs as a grant;
6. checks the ledger (session.tainted before the fetch's call.returned, its client_session the
   session id Claude Code reported, the posts the stub received), `polarizer verify` and
   `polarizer stats`' taint section; then stops the card.

With --setup it does steps 1 and 2 without the card, prints the commands for the owner's
interactive check (docs/dev/MANUAL-CHECK.md, M2b), and exits: no model is called.

Every run uses the pinned model, --max-budget-usd 1 and --strict-mcp-config --mcp-config with the
generated file. Runs 1 and 2 keep their session so run 2 can resume it, which Claude Code records
under ~/.claude; nothing else of Claude Code's is read or written by the script, and
~/.claude.json is only stat'ed. Every check prints PASS or FAIL; the script exits 0 only if all
pass, and leaves its directory in place.
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
import card_check  # noqa: E402  (the card's endpoints, priming and reporting, reused)

MODEL = card_check.MODEL
BUDGET = card_check.BUDGET
SERVER = "pz"
ALLOWED = "https://allowed.example/notes"
FETCHED = "https://news.example/today"
WAIT_FOR_HOLD = 240.0
WAIT_FOR_CLAUDE = 420.0
TOOLS = f"mcp__{SERVER}__web__post,mcp__{SERVER}__web__fetch"
TAINT = re.compile(
    r"^session tainted by web__fetch at (\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z) \(seq (\d+)\); "
)


def setup(directory: Path, polarizer: Path) -> dict:
    """Write polarizer.toml and mcp.json; return their paths."""
    python = REPO / ".venv" / "bin" / "python"
    stub = REPO / "tests" / "helpers" / "web_stub.py"
    posts = directory / "posts.jsonl"
    toml = (
        f"ledger_dir = {json.dumps((directory / 'ledger').as_posix())}\n\n"
        "[upstream.web]\n"
        f"command = {json.dumps(str(python))}\n"
        f"args = [{json.dumps(str(stub))}]\n"
        f"env = {{ WEB_STUB_LOG = {json.dumps(str(posts))} }}\n"
        "\n[upstream.web.tools]\n"
        'fetch = { class = "open-world" }\n'
        'post = { class = "egress", url_args = ["url"], domains = ["allowed.example"] }\n'
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
    return {"config": config, "mcp": mcp_path, "ledger": directory / "ledger", "posts": posts}


def run_claude(prompt, paths, directory, report, name, resume=None, persist=True):
    """`claude -p` with the pinned model and budget, the generated MCP config only, and the
    two web tools allowed. Its JSON output goes to <name>.json."""
    args = ["-p", prompt]
    if resume:
        args += ["--resume", resume]
    args += [
        "--model",
        MODEL,
        "--max-budget-usd",
        BUDGET,
        "--strict-mcp-config",
        "--mcp-config",
        str(paths["mcp"]),
        f"--allowedTools={TOOLS}",
        "--permission-mode",
        "default",
        "--output-format",
        "json",
    ]
    if not persist:
        args.append("--no-session-persistence")
    report.say("claude arguments: " + " ".join(json.dumps(a) for a in args) + " < /dev/null")
    env = {k: v for k, v in os.environ.items() if k != "MCP_TOOL_TIMEOUT"}
    return subprocess.Popen(
        ["claude", *args],
        cwd=directory,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=open(directory / f"{name}.json", "wb"),
        stderr=open(directory / f"{name}.stderr", "wb"),
    )


def finish(claude, directory: Path, name: str, report) -> dict:
    try:
        claude.wait(timeout=WAIT_FOR_CLAUDE)
    except subprocess.TimeoutExpired:
        claude.kill()
        claude.wait()
        report.say("claude: killed after the time limit")
    report.say(f"claude exit code: {claude.returncode}")
    try:
        out = json.loads((directory / f"{name}.json").read_text(encoding="utf-8"))
    except ValueError:
        report.say(f"claude output: not JSON; see {name}.json and {name}.stderr")
        return {}
    report.say(f"model reply: {out.get('result')!r}")
    report.say(f"session_id: {out.get('session_id')}")
    report.say(f"cost: {out.get('total_cost_usd')} USD (claude's own total_cost_usd)")
    return out


def posts(paths) -> list[dict]:
    path = paths["posts"]
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def held_by_taint(curl, seen, claude, report) -> dict | None:
    card = card_check.wait_for_hold(curl, seen, claude, WAIT_FOR_HOLD)
    if not report.check("the post was held and shown on the card", card is not None):
        return None
    seen.add(card["hold"])
    rows = {label: lines for label, lines in card["rows"]}
    report.say("the card's block:" + "".join("\n  " + x for x in card["block"]))
    report.say("why it was held: " + rows["Why it was held"][0])
    report.say("session taint row: " + " ".join(rows.get("Session taint", ["(none)"])))
    report.say(f"recommendation: {card['recommendation']['text']}")
    return card


def run1(paths, curl, directory, report) -> tuple[dict, dict | None]:
    prompt = (
        "Do these three steps in order, one tool call each, and use no other tool. "
        f"1. Call mcp__{SERVER}__web__post with url {ALLOWED} and body 'first note'. "
        f"2. Call mcp__{SERVER}__web__fetch with url {FETCHED}. "
        f"3. Call mcp__{SERVER}__web__post with url {ALLOWED} and body 'second note'. "
        "If a call is not allowed, do not retry it. Then reply in one line saying what each "
        "call returned."
    )
    claude = run_claude(prompt, paths, directory, report, "claude1")
    seen: set = set()
    card = None
    try:
        card = held_by_taint(curl, seen, claude, report)
        if card is not None:
            rows = {label: lines for label, lines in card["rows"]}
            why = rows["Why it was held"][0]
            report.check("held by rule tainted", why.startswith("tainted: session tainted by "))
            report.check(
                "the reason names web__fetch, a time and a seq",
                TAINT.match(why.split(": ", 1)[1]) is not None,
                why,
            )
            report.check("the card shows the session taint row", "Session taint" in rows)
            report.check("the card recommends deny", card["recommendation"]["decision"] == "deny")
            report.check("the egress hold was not posted yet", len(posts(paths)) == 1)
            code, answer = curl.decide(card["hold"], "allow")
            report.check(
                "allow through the card",
                code == 200 and answer.get("ok") is True,
                (answer.get("lines") or [answer.get("error")])[-1],
            )
    finally:
        out = finish(claude, directory, "claude1", report)
    return out, card


def run2(paths, curl, directory, report, session_id) -> dict | None:
    prompt = (
        f"Call mcp__{SERVER}__web__post once with url {ALLOWED} and body 'third note', and use "
        "no other tool. If it is not allowed, do not retry. Reply in one line."
    )
    claude = run_claude(prompt, paths, directory, report, "claude2", resume=session_id)
    seen: set = set()
    card = None
    try:
        card = held_by_taint(curl, seen, claude, report)
        if card is not None:
            code, answer = curl.decide(card["hold"], "deny", "taint check: deny")
            report.check(
                "deny through the card",
                code == 200 and answer.get("ok") is True,
                (answer.get("lines") or [answer.get("error")])[-1],
            )
    finally:
        finish(claude, directory, "claude2", report)
    return card


def run3(paths, directory, report) -> dict:
    prompt = (
        f"Call mcp__{SERVER}__web__post once with url {ALLOWED} and body 'fourth note', and use "
        "no other tool. Reply in one line with what it returned."
    )
    claude = run_claude(prompt, paths, directory, report, "claude3", persist=False)
    return finish(claude, directory, "claude3", report)


def check_ledger(paths, report, out1, out2_card, out3) -> None:
    found = card_check.entries(paths["ledger"])
    loaded = [e for e in found if e["kind"] == "policy.loaded"]
    report.say(
        "policy.loaded client_session per serve start: "
        + ", ".join(str(e["data"].get("client_session")) for e in loaded)
    )
    tainted = [e for e in found if e["kind"] == "session.tainted"]
    if not report.check("exactly one session.tainted", len(tainted) == 1, str(len(tainted))):
        return
    t = tainted[0]
    report.say(f"session.tainted at seq {t['seq']}: {json.dumps(t['data'])}")
    fetch = [e for e in found if e["kind"] == "call.sent" and e["data"]["tool"] == "web__fetch"]
    report.check(
        "its call_seq is the fetch's call.sent",
        bool(fetch) and t["data"]["call_seq"] == fetch[0]["seq"],
    )
    returned = [
        e
        for e in found
        if e["kind"] == "call.returned" and fetch and e["data"]["call_seq"] == fetch[0]["seq"]
    ]
    report.check(
        "session.tainted precedes the fetch's call.returned",
        bool(returned) and t["seq"] < returned[0]["seq"],
    )
    report.check(
        "its client_session is the session id Claude Code reported",
        t["data"]["client_session"] == out1.get("session_id"),
        f"{t['data']['client_session']} and {out1.get('session_id')}",
    )
    sent = [e for e in found if e["kind"] == "call.sent" and e["data"]["tool"] == "web__post"]
    report.say(
        "web__post call.sent: "
        + "; ".join(f"seq {e['seq']} allowed_by {e['data']['allowed_by']}" for e in sent)
    )
    grants = [e for e in sent if e["data"]["allowed_by"] == "domains"]
    report.check(
        "the first post ran as a grant, before the taint",
        bool(grants) and grants[0]["seq"] < t["seq"],
    )
    created = [e for e in found if e["kind"] == "hold.created"]
    source = f"session tainted by web__fetch at {t['ts']} (seq {t['data']['call_seq']}); "
    report.check(
        "both holds are by tainted and name the source exactly",
        len(created) == 2
        and all(
            e["data"]["rule"] == "tainted" and e["data"]["reason"].startswith(source)
            for e in created
        ),
        f"{len(created)} holds",
    )
    if len(created) == 2:
        sessions = {e["data"]["session"] for e in created}
        report.check(
            "the second hold is from a new serve process in the same session",
            len(sessions) == 2
            and loaded
            and sum(1 for e in loaded if e["data"].get("client_session") == out1.get("session_id"))
            >= 2,
        )
    decided = {e["data"]["hold"]: e["data"] for e in found if e["kind"] == "hold.decided"}
    report.check(
        "the card's allow and deny are in the ledger with elapsed_ms",
        len(decided) == 2 and all(type(d.get("elapsed_ms")) is int for d in decided.values()),
    )
    if out2_card is not None:
        report.check(
            "the resumed session's hold was not forwarded",
            not [
                e
                for e in found
                if e["kind"] == "call.sent" and e["data"].get("hold") == out2_card["hold"]
            ],
        )
    bodies = [p["body"] for p in posts(paths)]
    report.say(f"posts the stub received: {bodies}")
    report.check(
        "the stub received the first, second and fourth notes only",
        bodies == ["first note", "second note", "fourth note"],
        str(bodies),
    )
    if grants:
        report.check(
            "the new session's post ran as a grant",
            len(grants) == 2 and grants[1]["seq"] > t["seq"],
        )
    report.check(
        "the new session had another client_session",
        bool(out3.get("session_id")) and out3.get("session_id") != out1.get("session_id"),
    )


def setup_only(polarizer: Path) -> int:
    """--setup: the directory, the config and a primed ledger, and the commands to type."""
    (REPO / ".repro").mkdir(exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix="taint-manual.", dir=REPO / ".repro"))
    paths = setup(directory, polarizer)
    print(card_check.prime(paths["config"], paths["ledger"], polarizer))
    print(f"directory: {directory}")
    print(f"terminal A: claude --mcp-config {paths['mcp']} --strict-mcp-config")
    print(f"terminal C: {polarizer} card --config {paths['config']}")
    print(f"afterwards: {polarizer} verify --config {paths['config']}")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="the taint check on real Claude Code")
    parser.add_argument(
        "--setup", action="store_true", help="write the config and prime it, for a person; no model"
    )
    args = parser.parse_args(argv)
    polarizer = REPO / ".venv" / "bin" / "polarizer"
    if not polarizer.exists():
        print(f"taint-check: {polarizer} not found; run uv sync --locked first", file=sys.stderr)
        return 2
    if args.setup:
        return setup_only(polarizer)
    for command in ("claude", "curl"):
        if shutil.which(command) is None:
            print(f"taint-check: {command} is not on PATH", file=sys.stderr)
            return 2
    (REPO / ".repro").mkdir(exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix="taint-check.", dir=REPO / ".repro"))
    report = card_check.Report(directory / "taint-check-results.txt")
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
    report.say("== priming the ledger (no model)")
    report.say(card_check.prime(paths["config"], paths["ledger"], polarizer))
    card, port, token = card_check.start_card(paths["config"], polarizer, directory)
    report.say(f"== the card on port {port}")
    try:
        curl = card_check.Curl(directory, port, token)
        report.check(
            "the page loads with the token, and has a nonce",
            curl.open_page() == 200 and curl.nonce is not None,
        )
        report.say("== run 1: a grant, a fetch, then the same post")
        out1, _ = run1(paths, curl, directory, report)
        report.say("== run 2: the same Claude Code session, resumed (a new serve)")
        held = run2(paths, curl, directory, report, out1.get("session_id")) if out1 else None
        report.say("== run 3: a new Claude Code session")
        out3 = run3(paths, directory, report)
        check_ledger(paths, report, out1, held, out3)
        done = subprocess.run(
            [str(polarizer), "verify", "--ledger-dir", str(paths["ledger"])],
            capture_output=True,
            text=True,
        )
        report.check("polarizer verify", done.returncode == 0, done.stdout.strip())
        done = subprocess.run(
            [str(polarizer), "stats", "--ledger-dir", str(paths["ledger"])],
            capture_output=True,
            text=True,
        )
        report.say("== polarizer stats\n" + done.stdout.rstrip())
        report.check(
            "stats counts the two holds taint caused",
            "held by taint 2: allowed 1, denied 1" in done.stdout,
        )
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
