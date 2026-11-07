"""The card's headless check on real Claude Code (docs/CARD-SPEC.md, section 12, step 4).

Manual only: it calls a model, so it costs money and never runs in CI. Run it with the repo's
.venv python on Linux or WSL, from a plain terminal:

    .venv/bin/python scripts/card_check.py

What it does, with no questions:
1. makes a temporary directory under $TMPDIR (or /tmp): a workspace with .git/hooks, a
   polarizer.toml whose ledger_dir is inside it, and an mcp.json that starts `polarizer serve`
   with that config and the pinned reference Filesystem server;
2. primes the ledger as a new user does once: `polarizer serve` with stdin closed lists the
   server's tools, then their group is approved through the library (no model);
3. starts `polarizer card --config ... --allow-no-terminal` and reads its open line;
4. checks with curl that the card refuses requests without the token, the cookie or the nonce,
   and with another Host;
5. runs `claude -p` (pinned model, --max-budget-usd 1, --strict-mcp-config --mcp-config) with a
   prompt to write a file inside .git/hooks, and to try once more if the call is not allowed;
6. drives the card with curl only: waits for the hold, denies it, waits for the retry's hold,
   allows it;
7. checks the ledger: both decisions with elapsed_ms, the deny refused and never forwarded, the
   allow forwarded and returned ok, the file written once, `polarizer verify` intact, and
   `polarizer stats` counting two decisions timed on one clock; then stops the card.

Every check prints PASS or FAIL; it exits 0 only if all pass. Everything it writes is in the
temporary directory, which it prints and leaves in place. It reads nothing under ~/.claude and
only stats ~/.claude.json, which Claude Code updates on every run.
"""

import argparse
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
FILESYSTEM = "@modelcontextprotocol/server-filesystem@2026.8.31"  # docs/verified-facts.md
MODEL = "claude-haiku-4-5-20251001"
BUDGET = "1"
SERVER = "pz"  # the server's name in mcp.json, so the tool is mcp__pz__fs__write_file
WAIT_FOR_HOLD = 240.0
WAIT_FOR_CLAUDE = 420.0
POLL = 1.5

FS_TOOLS = """
[upstream.fs.tools]
read_file = { class = "local-read", path_args = ["path"] }
read_text_file = { class = "local-read", path_args = ["path"] }
read_media_file = { class = "local-read", path_args = ["path"] }
read_multiple_files = { class = "local-read", path_args = ["paths"] }
list_directory = { class = "local-read", path_args = ["path"] }
list_directory_with_sizes = { class = "local-read", path_args = ["path"] }
directory_tree = { class = "local-read", path_args = ["path"] }
search_files = { class = "local-read", path_args = ["path"] }
get_file_info = { class = "local-read", path_args = ["path"] }
list_allowed_directories = { class = "local-read" }
write_file = { class = "local-write", path_args = ["path"] }
edit_file = { class = "local-write", path_args = ["path"] }
create_directory = { class = "local-write", path_args = ["path"] }
move_file = { class = "destructive", path_args = ["source", "destination"] }
"""


def setup(directory: Path, polarizer: Path, planted: int = 0) -> dict:
    """Write the workspace, polarizer.toml and mcp.json; return their paths."""
    work = directory / "work"
    (work / ".git" / "hooks").mkdir(parents=True)
    policy = f"[policy]\nworkspace_roots = [{json.dumps(work.as_posix())}]\n"
    if planted:
        policy += f"planted_calls_per_session = {planted}\n"
    toml = (
        f"ledger_dir = {json.dumps((directory / 'ledger').as_posix())}\n\n"
        + policy
        + "\n[upstream.fs]\n"
        + 'command = "npx"\n'
        + f'args = ["-y", {json.dumps(FILESYSTEM)}, {json.dumps(work.as_posix())}]\n'
        + "connect_timeout_seconds = 20\n"
        + FS_TOOLS
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
    return {"work": work, "config": config, "mcp": mcp_path, "ledger": directory / "ledger"}


def prime(config: Path, ledger: Path, polarizer: Path) -> str:
    """serve with stdin closed, then approve the pending group. Returns one line."""
    from polarizer.decisions import Decider

    done = subprocess.run(
        [str(polarizer), "serve", "--config", str(config)],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        timeout=180,
    )
    if done.returncode != 0:
        raise SystemExit(
            f"prime: serve exited {done.returncode}: {done.stderr.decode(errors='replace')}"
        )
    decider = Decider.open(ledger)
    try:
        members, group_id = decider.pending_group()
        if group_id is None:
            raise SystemExit("prime: nothing to approve")
        errors: list[str] = []
        code = decider.approve_group(group_id, None, lambda line: None, errors.append)
    finally:
        decider.close()
    if code != 0:
        raise SystemExit(f"prime: approving failed: {'; '.join(errors)}")
    return f"approved {len(members)} definitions as group {group_id}"


class Curl:
    """The card's endpoints, through the curl command only, with a cookie jar."""

    def __init__(self, directory: Path, port: int, token: str):
        self.base = f"http://127.0.0.1:{port}"
        self.port = port
        self.token = token
        self.jar = directory / "cookies.txt"
        self.nonce = None

    def run(self, *args: str, data: bytes | None = None) -> tuple[int, bytes]:
        argv = ["curl", "-sS", "-o", "-", "-w", "\n%{http_code}", *args]
        done = subprocess.run(argv, input=data, capture_output=True, timeout=60)
        body, _, code = done.stdout.rpartition(b"\n")
        return int(code or b"0"), body

    def open_page(self) -> int:
        code, body = self.run("-c", str(self.jar), f"{self.base}/?t={self.token}")
        found = re.search(rb'name="polarizer-nonce" content="([0-9a-f]{64})"', body)
        self.nonce = found[1].decode() if found else None
        return code

    def state(self) -> dict:
        code, body = self.run(
            "-b", str(self.jar), "-H", f"X-Polarizer-Nonce: {self.nonce}", f"{self.base}/state"
        )
        if code != 200:
            raise RuntimeError(f"GET /state answered {code}")
        return json.loads(body)

    def decide(self, hold: str, decision: str, reason=None) -> tuple[int, dict]:
        body = json.dumps({"hold": hold, "decision": decision, "reason": reason}).encode()
        code, answer = self.run(
            "-b",
            str(self.jar),
            "-H",
            f"X-Polarizer-Nonce: {self.nonce}",
            "-H",
            "Content-Type: application/json",
            "-H",
            f"Origin: {self.base}",
            "--data-binary",
            "@-",
            f"{self.base}/decide",
            data=body,
        )
        return code, json.loads(answer) if answer.startswith(b"{") else {"raw": answer.decode()}

    def refusals(self) -> list[tuple[str, int]]:
        """(what was tried, the code) for requests the card must refuse with 403."""
        state = f"{self.base}/state"
        return [
            ("the page without the token", self.run(f"{self.base}/")[0]),
            ("the page with a wrong token", self.run(f"{self.base}/?t={'0' * 64}")[0]),
            (
                "the page with another Host",
                self.run("-H", f"Host: evil.example:{self.port}", f"{self.base}/?t={self.token}")[
                    0
                ],
            ),
            (
                "the state without the cookie",
                self.run("-H", f"X-Polarizer-Nonce: {self.nonce}", state)[0],
            ),
            ("the state without the nonce", self.run("-b", str(self.jar), state)[0]),
            (
                "a decision without Origin",
                self.run(
                    "-b",
                    str(self.jar),
                    "-H",
                    f"X-Polarizer-Nonce: {self.nonce}",
                    "-H",
                    "Content-Type: application/json",
                    "--data-binary",
                    "{}",
                    f"{self.base}/decide",
                )[0],
            ),
        ]


def entries(ledger: Path) -> list[dict]:
    path = ledger / "ledger.jsonl"
    return [json.loads(line) for line in path.read_bytes().splitlines()] if path.exists() else []


def wait_for_hold(curl: Curl, seen: set, claude: subprocess.Popen, timeout: float):
    """The first open hold not in `seen`, from GET /state, or None once claude has exited or
    the time is up."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for card in curl.state()["open"]:
            if card["hold"] not in seen:
                return card
        if claude.poll() is not None:
            return None
        time.sleep(POLL)
    return None


def claude_json_stat() -> str:
    path = Path.home() / ".claude.json"
    if not path.exists():
        return "absent"
    s = path.stat()
    return (
        f"{s.st_size} bytes, mtime {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(s.st_mtime))}"
    )


class Report:
    def __init__(self, path: Path):
        self.path = path
        self.failed = 0

    def say(self, line: str) -> None:
        print(line, flush=True)
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(line + "\n")

    def check(self, name: str, ok: bool, detail: str = "") -> bool:
        self.failed += 0 if ok else 1
        self.say(f"{'PASS' if ok else 'FAIL'}: {name}{': ' + detail if detail else ''}")
        return ok


def start_card(config: Path, polarizer: Path, directory: Path):
    card = subprocess.Popen(
        [str(polarizer), "card", "--config", str(config), "--allow-no-terminal"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=open(directory / "card.stderr", "wb"),
    )
    line = card.stdout.readline().decode()
    found = re.fullmatch(r"open http://127\.0\.0\.1:(\d+)/\?t=([0-9a-f]{64})\n", line)
    if not found:
        card.kill()
        raise SystemExit(f"card-check: the card printed {line!r}")
    return card, int(found[1]), found[2]


def stop_card(card: subprocess.Popen, report: Report) -> None:
    card.send_signal(signal.SIGTERM)
    try:
        rest = card.communicate(timeout=30)[0]
    except subprocess.TimeoutExpired:
        card.kill()
        rest = b""
    report.check(
        "the card stops on SIGTERM with exit 0 and nothing more on stdout",
        card.returncode == 0 and rest == b"",
        f"exit {card.returncode}",
    )


def run_claude(prompt: str, mcp: Path, work: Path, allowed: str, directory: Path, report: Report):
    args = [
        "-p",
        prompt,
        "--model",
        MODEL,
        "--max-budget-usd",
        BUDGET,
        "--strict-mcp-config",
        "--mcp-config",
        str(mcp),
        f"--allowedTools={allowed}",
        "--permission-mode",
        "default",
        "--no-session-persistence",
        "--output-format",
        "json",
    ]
    report.say("claude arguments: " + " ".join(json.dumps(a) for a in args) + " < /dev/null")
    env = {k: v for k, v in os.environ.items() if k != "MCP_TOOL_TIMEOUT"}
    return subprocess.Popen(
        ["claude", *args],
        cwd=work,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=open(directory / "claude.json", "wb"),
        stderr=open(directory / "claude.stderr", "wb"),
    )


def finish_claude(claude: subprocess.Popen, directory: Path, report: Report) -> None:
    try:
        claude.wait(timeout=WAIT_FOR_CLAUDE)
    except subprocess.TimeoutExpired:
        claude.kill()
        claude.wait()
        report.say("claude: killed after the time limit")
    report.say(f"claude exit code: {claude.returncode}")
    try:
        out = json.loads((directory / "claude.json").read_text(encoding="utf-8"))
        report.say(f"model reply: {out.get('result')!r}")
        report.say(f"cost: {out.get('total_cost_usd')} USD (claude's own total_cost_usd)")
    except ValueError:
        report.say("claude output: not JSON; see claude.json and claude.stderr")


def common_end(paths: dict, polarizer: Path, report: Report, timed: int) -> None:
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
    if timed:
        want = f"{timed} of {timed} decision"
        report.check(
            "stats times the card's decisions on one clock",
            want in done.stdout and "were timed on one clock" in done.stdout,
            want,
        )


def check_m3(paths, curl, polarizer, directory, report) -> None:
    target = paths["work"] / ".git" / "hooks" / "card-check.txt"
    content = f"card check {directory.name}"
    prompt = (
        f"Use the tool mcp__{SERVER}__fs__write_file to write the file {target} with the "
        f"content '{content}'. If the tool answers that the call was not allowed, call it "
        "exactly once more with the same arguments. Do not use any other tool or route. Then "
        "reply in one line saying what happened."
    )
    claude = run_claude(
        prompt, paths["mcp"], paths["work"], f"mcp__{SERVER}__fs__write_file", directory, report
    )
    seen: set = set()
    try:
        first = wait_for_hold(curl, seen, claude, WAIT_FOR_HOLD)
        if not report.check("the write was held and shown on the card", first is not None):
            return
        seen.add(first["hold"])
        report.say("the card's block:\n  " + "\n  ".join(first["block"]))
        rows = {label: lines for label, lines in first["rows"]}
        report.say(f"recommendation: {first['recommendation']['text']}")
        report.check(
            "held by write-pattern", rows["Why it was held"][0].startswith("write-pattern")
        )
        report.check("the card recommends deny", first["recommendation"]["decision"] == "deny")
        code, answer = curl.decide(first["hold"], "deny", "card check: deny")
        report.check(
            "deny through the card",
            code == 200 and answer.get("ok") is True,
            (answer.get("lines") or [answer.get("error")])[-1],
        )
        report.check("the file was not written after the deny", not target.exists())
        second = wait_for_hold(curl, seen, claude, WAIT_FOR_HOLD)
        if not report.check(
            "the retry was a new hold",
            second is not None,
            "the model did not retry" if second is None else second["hold"],
        ):
            return
        code, answer = curl.decide(second["hold"], "allow")
        report.check(
            "allow through the card",
            code == 200 and answer.get("ok") is True,
            (answer.get("lines") or [answer.get("error")])[-1],
        )
    finally:
        finish_claude(claude, directory, report)
    found = entries(paths["ledger"])
    decided = {e["data"]["hold"]: e["data"] for e in found if e["kind"] == "hold.decided"}
    for name, hold, decision in (
        ("deny", first["hold"], "deny"),
        ("allow", second["hold"], "allow"),
    ):
        data = decided.get(hold, {})
        report.check(
            f"the {name} is in the ledger with elapsed_ms",
            data.get("decision") == decision and type(data.get("elapsed_ms")) is int,
            f"elapsed_ms {data.get('elapsed_ms')}, terminal {data.get('terminal', 'absent')}",
        )
    refused = [
        e for e in found if e["kind"] == "call.refused" and e["data"].get("hold") == first["hold"]
    ]
    sent = [e for e in found if e["kind"] == "call.sent" and e["data"].get("hold") == first["hold"]]
    report.check("the denied call was refused and never forwarded", len(refused) == 1 and not sent)
    sent = [
        e for e in found if e["kind"] == "call.sent" and e["data"].get("hold") == second["hold"]
    ]
    returned = [
        e
        for e in found
        if e["kind"] == "call.returned" and sent and e["data"].get("call_seq") == sent[0]["seq"]
    ]
    report.check(
        "the allowed call was forwarded and returned ok",
        len(sent) == 1 and len(returned) == 1 and returned[0]["data"]["outcome"] == "ok",
    )
    written = target.read_text(encoding="utf-8") if target.exists() else None
    report.check("the file holds what was allowed", written == content, repr(written))
    ended = curl.state()["ended"]
    report.say("ended on the card: " + "; ".join(f"{e['hold']}: {e['text']}" for e in ended))
    report.check(
        "both holds are in the card's ended list",
        {e["hold"] for e in ended} >= {first["hold"], second["hold"]},
    )
    common_end(paths, polarizer, report, 2)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="the card's headless check on real Claude Code")
    parser.parse_args(argv)
    polarizer = REPO / ".venv" / "bin" / "polarizer"
    if not polarizer.exists():
        print(f"card-check: {polarizer} not found; run uv sync --locked first", file=sys.stderr)
        return 2
    for command in ("claude", "curl", "npx"):
        if shutil.which(command) is None:
            print(f"card-check: {command} is not on PATH", file=sys.stderr)
            return 2
    directory = Path(tempfile.mkdtemp(prefix="polarizer-card-check."))
    report = Report(directory / "card-check-results.txt")
    report.say(f"== conditions, {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}")
    version = subprocess.run(["claude", "--version"], capture_output=True, text=True).stdout.strip()
    commit = subprocess.run(
        ["git", "-C", str(REPO), "rev-parse", "--short", "HEAD"], capture_output=True, text=True
    ).stdout.strip()
    report.say(f"claude version: {version}; model {MODEL}; budget {BUDGET} USD")
    report.say(f"polarizer commit: {commit}; directory: {directory}")
    names = sorted(k for k in os.environ if k.startswith(("CLAUDE", "ANTHROPIC")))
    report.say("CLAUDE_* and ANTHROPIC_* variables set (names only): " + " ".join(names))
    before = claude_json_stat()
    paths = setup(directory, polarizer)
    report.say("== priming the ledger (no model)")
    report.say(prime(paths["config"], paths["ledger"], polarizer))
    card, port, token = start_card(paths["config"], polarizer, directory)
    report.say(f"== the card on port {port}")
    try:
        curl = Curl(directory, port, token)
        report.check(
            "the page loads with the token, and has a nonce",
            curl.open_page() == 200 and curl.nonce is not None,
        )
        for what, code in curl.refusals():
            report.check(f"403 for {what}", code == 403, str(code))
        report.say("== claude")
        check_m3(paths, curl, polarizer, directory, report)
    finally:
        stop_card(card, report)
    report.say(f"~/.claude.json bookkeeping: before {before}; after {claude_json_stat()}")
    report.say(
        f"== {'all checks passed' if report.failed == 0 else f'{report.failed} checks failed'}"
    )
    report.say(f"results: {report.path}")
    return 0 if report.failed == 0 else 1


if __name__ == "__main__":
    sys.path.insert(0, str(REPO / "src"))
    sys.exit(main())
