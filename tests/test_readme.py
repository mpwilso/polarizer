"""README.md's setup, run as written, so the README can't drift from the code.

Every command in the Setup section's sh blocks is run in README order, except the ones that need
the network or Claude Code (SKIPPED); the install line, which fetches the release wheel, is
parsed instead, as is the git install line in the text after it. The polarizer.toml block is written where the README says,
with /home/you replaced by a temporary home; only its Filesystem server's command and args are
swapped for the stdlib probe, so nothing is fetched. Its tools are then unclassified, so every
call is held. A raw stdio client stands in for Claude Code to make those calls, which gives the
second terminal's holds, allow and deny real holds to act on. approve, allow and deny get a
pseudo-terminal as stdin, as in a person's terminal; Windows has none, so there they are given
--allow-no-terminal.

Every `polarizer ...` command shown anywhere in README.md must also parse with the real
argument parser. Outside Setup, the "Try a drill" commands run too: the drill on a
pseudo-terminal (POSIX only, since it needs a terminal) and the report without one. Every
relative link must name a file and heading that exist, and every absolute link must be a
well-formed https URL (checked, never fetched). The README keeps its layout and length; every
limit and every evidence claim the v0.1 README listed is in docs/LIMITS.md or
docs/EVIDENCE.md, where the detail moved, and every related project it names is in
docs/verified-facts.md, with the same link.
"""

import json
import os
import re
import shlex
import subprocess
import sys
import time
import tomllib
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from conftest import ROOT
from helpers import rig
from helpers.raw import RawClient
from packaging.utils import parse_wheel_filename

import polarizer
from polarizer import cli, drill

README = (ROOT / "README.md").read_text(encoding="utf-8")
SKIPPED = {
    "uv tool install ": "needs the network; test_install_lines_parse parses it",
    "npx ": "needs the network; the probe stands in for the Filesystem server",
    "claude ": "needs Claude Code; a raw stdio client stands in for it",
}
PLACEHOLDERS = {
    "<group id>": "@GROUP@",
    "<hold id>": "@HOLD@",
    "<prefix> <tool> <hash>": "fs wait " + "0" * 64,
}


def section(title: str) -> str:
    start = README.index(f"\n## {title}")
    end = README.find("\n## ", start + 1)
    return README[start : end if end != -1 else len(README)]


QUICKSTART = section("Setup")


def blocks(language: str, text: str = QUICKSTART) -> list[str]:
    return re.findall(rf"```{language}\n(.*?)```", text, re.S)


def commands(text: str = QUICKSTART) -> list[str]:
    return [
        line.strip()
        for block in blocks("sh", text)
        for line in block.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]


def spans(text: str) -> list[str]:
    """The inline code spans of Markdown text, outside fenced blocks."""
    return re.findall(r"`([^`\n]+)`", re.sub(r"```.*?```", "", text, flags=re.S))


def output(start: str) -> re.Pattern:
    """The quickstart's quoted output line that starts with `start`, as a pattern: each <...>
    matches any text, and a final "..." any rest of the line."""
    [span] = [s for s in spans(QUICKSTART) if s.startswith(start)]
    parts = re.split(r"<[^>]+>", span.removesuffix("..."))
    rest = ".*" if span.endswith("...") else ""
    return re.compile(".+?".join(re.escape(part) for part in parts) + rest)


def shows(start: str, text: bytes) -> bool:
    pattern = output(start)
    return any(pattern.fullmatch(line) for line in text.decode().splitlines())


def python(*argv, stdin=subprocess.DEVNULL, env=None, timeout=120):
    return subprocess.run(
        [sys.executable, "-m", "polarizer", *argv],
        stdin=stdin,
        capture_output=True,
        env=env,
        timeout=timeout,
    )


def on_a_terminal(argv: list[str], env: dict) -> subprocess.CompletedProcess:
    """argv as a person runs it: stdin a pseudo-terminal (POSIX), or with --allow-no-terminal."""
    if sys.platform == "win32":
        return python(*argv, "--allow-no-terminal", env=env)
    import pty

    controller, terminal = pty.openpty()
    try:
        return python(*argv, stdin=terminal, env=env)
    finally:
        os.close(terminal)
        os.close(controller)


def complete_entries(ledger_dir: Path) -> list[dict]:
    path = ledger_dir / "ledger.jsonl"
    data = path.read_bytes() if path.exists() else b""
    return [json.loads(line) for line in data[: data.rfind(b"\n") + 1].splitlines()]


def held_ids(ledger_dir: Path) -> list[str]:
    return [e["data"]["hold"] for e in complete_entries(ledger_dir) if e["kind"] == "hold.created"]


def answer(client: RawClient, rid: int) -> dict:
    while True:
        line = client.proc.stdout.readline()
        assert line, "serve closed its stdout"
        message = json.loads(line)
        if message.get("id") == rid and "method" not in message:
            return message
        client.other.append(message)


class Session:
    """A running `polarizer serve` behind a raw client: the stand-in for Claude Code."""

    def __init__(self, config: Path, ledger_dir: Path, env: dict):
        argv = [sys.executable, "-m", "polarizer", "serve", "--config", str(config)]
        self.client = RawClient(argv, env=env)
        self.client.initialize()
        self.ledger_dir = ledger_dir
        self.waiting: list[tuple[str, int]] = []  # (hold id, request id), not yet decided
        self.next_id = 100

    def hold_a_call(self) -> str:
        before = len(held_ids(self.ledger_dir))
        rid, self.next_id = self.next_id, self.next_id + 1
        params = {"name": "fs__wait", "arguments": {"seconds": 0}}
        self.client.send({"jsonrpc": "2.0", "id": rid, "method": "tools/call", "params": params})
        deadline = time.monotonic() + 30
        while len(held_ids(self.ledger_dir)) <= before:
            assert time.monotonic() < deadline, "the call was not held"
            time.sleep(0.02)
        hold = held_ids(self.ledger_dir)[-1]
        self.waiting.append((hold, rid))
        return hold


def test_quickstart_runs_as_written(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    env = {**os.environ, "HOME": str(home), "USERPROFILE": str(home)}
    ledger_dir = home / ".local" / "share" / "polarizer"
    shell: dict[str, str] = {}
    found: dict[str, str] = {}
    session = None
    ran, skipped = [], []

    [toml] = blocks("toml")
    for line in ('command = "npx"\n', 'args = ["-y", "@modelcontextprotocol/server-filesystem'):
        assert toml.count(line) == 1, line
    toml = toml.replace("/home/you", home.as_posix())
    toml = toml.replace('command = "npx"', f"command = {rig.toml_str(sys.executable)}")
    toml = re.sub(r'args = \["-y", [^\n]*\]', lambda _: f"args = [{rig.toml_str(rig.PROBE)}]", toml)

    def expand(token: str) -> str:
        if token.startswith("~/"):
            return str(home / token[2:])
        if token.startswith("$"):
            return shell[token[1:]]
        return {"@GROUP@": found.get("group", ""), "@HOLD@": found.get("hold", "")}.get(
            token, token
        )

    try:
        for command in commands():
            if any(command.startswith(prefix) for prefix in SKIPPED):
                skipped.append(command)
                continue
            line = command
            for text, mark in PLACEHOLDERS.items():
                line = line.replace(text, mark)
            closed = line.endswith("< /dev/null")
            argv = shlex.split(line.removesuffix("< /dev/null"))
            ran.append(command)

            if argv[0] == "mkdir":
                assert argv[1] == "-p"
                for path in argv[2:]:
                    Path(expand(path)).mkdir(parents=True, exist_ok=True)
                continue
            if re.fullmatch(r"[A-Z]+=.*", argv[0]) and len(argv) == 1:
                name, value = argv[0].split("=", 1)
                assert value == "$HOME/.config/polarizer/polarizer.toml"
                shell[name] = value.replace("$HOME", str(home))
                config = Path(shell[name])
                if not config.exists():  # "Save this as ~/.config/polarizer/polarizer.toml"
                    config.write_text(toml, encoding="utf-8")
                continue
            assert argv[0] == "polarizer", f"the quickstart runs {command!r}; teach this test"
            sub = argv[1] if len(argv) > 1 else ""
            if sub in ("allow", "deny"):
                # The hold `holds --wait` listed, or else a new one, as a person would see it.
                waiting = [hold for hold, _ in session.waiting]
                if found.get("hold") not in waiting:
                    found["hold"] = session.hold_a_call()
            args = [expand(a) for a in argv[1:]]

            if sub == "--help":
                done = python(*args, env=env)
                assert done.returncode == 0
                for name in ("serve", "verify", "pending", "approve", "holds", "allow", "deny"):
                    assert name.encode() in done.stdout
            elif sub == "serve":
                assert closed, "the quickstart's serve runs with its input closed"
                done = python(*args, env=env)
                assert done.returncode == 0, done.stderr
                assert shows("polarizer: <n> tools wait", done.stderr), done.stderr
            elif sub == "pending":
                done = python(*args, env=env)
                assert done.returncode == 0, done.stderr
                [group] = [x for x in done.stdout.decode().splitlines() if x.startswith("group ")]
                assert shows("group <group id> covers", group.encode())
                found["group"] = group.split()[1]
            elif sub == "approve":
                done = on_a_terminal(args, env)
                assert done.returncode == 0, done.stderr
                assert done.stdout.decode().splitlines()[-1].startswith("approved ")
            elif sub == "verify":
                done = python(*args, env=env)
                assert done.returncode == 0, done.stdout
                assert shows("intact: ...", done.stdout)
            elif sub == "holds":
                assert "--wait" in args
                if session is None:
                    session = Session(Path(shell["CONFIG"]), ledger_dir, env)
                hold = session.hold_a_call()
                done = python(*args, env=env, timeout=60)
                assert done.returncode == 0, done.stderr
                if "--bell" in args:
                    assert done.stdout.startswith(b"\a")
                assert hold.encode() in done.stdout
                found["hold"] = hold
            elif sub in ("allow", "deny"):
                hold = found.pop("hold")
                assert hold in args
                [rid] = [r for h, r in session.waiting if h == hold]
                session.waiting.remove((hold, rid))
                done = on_a_terminal(args, env)
                assert done.returncode == 0, done.stderr
                result = answer(session.client, rid)["result"]
                if sub == "allow":
                    assert result.get("isError") is False
                else:
                    assert result["isError"] is True
                    assert shows("polarizer: <tool> was not", result["content"][0]["text"].encode())
            else:
                pytest.fail(f"the quickstart runs {command!r}; teach this test")
    finally:
        if session is not None:
            assert session.client.close() == 0

    assert session is not None and not session.waiting
    for prefix in SKIPPED:
        assert any(c.startswith(prefix) for c in skipped), f"nothing skipped for {prefix!r}"
    subs = {shlex.split(c)[1] for c in ran if c.startswith("polarizer ")}
    assert subs == {"--help", "serve", "pending", "approve", "verify", "holds", "allow", "deny"}
    kinds = [e["kind"] for e in complete_entries(ledger_dir)]
    assert kinds.count("hold.decided") == 2 and kinds.count("call.refused") == 1
    done = python("verify", "--ledger-dir", str(ledger_dir), "--args", env=env)
    assert done.returncode == 0 and done.stdout.startswith(b"intact: ")


def test_install_lines_parse():
    """Setup installs the release wheel first; the text after the block gives the git line for
    the latest code and says it needs git. Both are parsed, never run or fetched: the release
    URL names this version's tag on the repository in pyproject.toml and the wheel `uv build`
    makes, read by the wheel filename parser as this package and version, for any Python 3."""
    repository = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"][
        "urls"
    ]["Source"]
    version = polarizer.__version__
    wheel = f"polarizer-{version}-py3-none-any.whl"
    installs = [c for c in commands() if c.startswith("uv tool install ")]
    release = f"uv tool install {repository}/releases/download/v{version}/{wheel}"
    assert installs == [release] and commands()[0] == release
    argv = shlex.split(installs[0])
    assert argv[:3] == ["uv", "tool", "install"] and len(argv) == 4
    url = urlsplit(argv[3])
    assert url.scheme == "https" and not url.query and not url.fragment
    name, parsed, build, tags = parse_wheel_filename(url.path.rsplit("/", 1)[1])
    assert (name, str(parsed), build) == ("polarizer", version, ())
    assert {str(tag) for tag in tags} == {"py3-none-any"}
    git = [s for s in spans(QUICKSTART) if s.startswith("uv tool install ")]
    assert git == [f"uv tool install git+{repository}"]
    argv = shlex.split(git[0])
    assert argv[:3] == ["uv", "tool", "install"] and len(argv) == 4
    assert urlsplit(argv[3].removeprefix("git+")).scheme == "https"
    assert f"`{git[0]}`, which needs git." in QUICKSTART


def test_quickstart_config_matches_the_example():
    """The README's polarizer.toml is polarizer.example.toml without its comments, as it says,
    and its npx commands name the same pinned version."""
    [toml] = blocks("toml")
    readme = tomllib.loads(toml)
    example = tomllib.loads((ROOT / "polarizer.example.toml").read_text(encoding="utf-8"))
    assert readme == example
    package = example["upstream"]["fs"]["args"][1]
    assert package.startswith("@modelcontextprotocol/server-filesystem@")
    npx = [c for c in commands() if c.startswith("npx ")]
    assert npx and all(f" {package} " in c for c in npx)


def test_quickstart_mcp_config():
    """The MCP config runs `polarizer serve --config <absolute path>` with the path the README
    saves polarizer.toml to, and Claude Code is started with that file only."""
    [block] = blocks("json")
    servers = json.loads(block)["mcpServers"]
    assert list(servers) == ["polarizer"]
    assert servers["polarizer"]["command"].endswith("/polarizer")
    assert servers["polarizer"]["args"] == [
        "serve",
        "--config",
        "/home/you/.config/polarizer/polarizer.toml",
    ]
    [claude] = [c for c in commands() if c.startswith("claude ")]
    argv = shlex.split(claude)
    assert "--strict-mcp-config" in argv
    assert argv[argv.index("--mcp-config") + 1] == "$HOME/.config/polarizer/mcp.json"
    assert ".mcp.json" not in claude


def test_every_polarizer_command_parses():
    """Every `polarizer ...` command in README.md, in a code span or a block, names a real
    command with real flags."""
    shown = [s for s in spans(README) if s.startswith("polarizer ")]
    shown += [c for c in commands(README) if c.startswith("polarizer ")]
    assert len(shown) > 10
    assert {c for c in commands(README) if c.startswith("polarizer ")} >= set(DRILL_COMMANDS)
    for text in shown:
        line = text.removesuffix("< /dev/null")
        for placeholder, value in {**PLACEHOLDERS, "<path>": "/p/polarizer.toml"}.items():
            line = line.replace(placeholder, value)
        argv = shlex.split(line)[1:]
        if argv == ["--help"]:
            continue  # run by test_quickstart_runs_as_written
        try:
            cli._parser().parse_args(argv)
        except cli.UsageError as e:
            pytest.fail(f"README shows `{text}`: {e}")


def slug(heading: str) -> str:
    """GitHub's anchor for a heading."""
    text = re.sub(r"[^\w\- ]", "", heading.strip().lower())
    return text.replace(" ", "-")


def test_readme_links_resolve():
    """Every relative link, image and srcset names a file, and every #anchor a heading in it (an
    anchor alone means this README). scripts/check_docs.py checks every doc the same way."""
    links = re.findall(r"\]\(([^)]+)\)", README)
    links += re.findall(r'\b(?:src|srcset)="([^"]+)"', README)
    local = [link for link in links if not link.startswith("https://")]
    assert len(local) > 10
    for link in local:
        target, _, anchor = link.partition("#")
        path = ROOT / target if target else ROOT / "README.md"
        assert path.exists(), link
        if anchor:
            text = re.sub(r"```.*?```", "", path.read_text(encoding="utf-8"), flags=re.S)
            headings = re.findall(r"^#{1,6} (.+)$", text, re.M)
            assert anchor in {slug(h) for h in headings}, link
    for tag in re.findall(r"<img\b[^>]*>", README):
        assert re.search(r'\balt="[^"]+"', tag), tag


FENCE = re.compile(r"^ *```(.*)$")


def fence_problems(text: str) -> list[str]:
    """What is wrong with the code fences of Markdown text. Fence lines (three backticks, after
    any list indentation) pair up in order: an opening fence may carry a language, a closing
    fence carries none, so a fence with a language inside an open block means an opening fence
    above it is missing. A block whose content starts with "{" is labelled json."""
    problems, opened, language, content = [], 0, "", []
    for number, line in enumerate(text.splitlines(), 1):
        match = FENCE.match(line)
        if not match:
            if opened:
                content.append(line)
            continue
        label = match.group(1).strip()
        if not opened:
            opened, language, content = number, label, []
        elif label:
            problems.append(f"line {number}: ```{label} inside the block opened at line {opened}")
            opened, language, content = number, label, []
        else:
            if "\n".join(content).strip().startswith("{") and language != "json":
                problems.append(f"line {opened}: a block starting with {{ is not labelled json")
            opened = 0
    if opened:
        problems.append(f"line {opened}: the block opened here is never closed")
    return problems


def test_readme_fences_pair_up():
    """README.md's code fences pair up, and its JSON is labelled json. The check catches the
    Setup section's MCP config with its opening ```json fence missing, which turns the JSON
    into plain text and lets its closing fence swallow the claude command."""
    assert fence_problems(README) == []
    opening = '```json\n{\n  "mcpServers"'
    assert README.count(opening) == 1
    broken = README.replace(opening, opening.removeprefix("```json\n"))
    assert fence_problems(broken)


def test_docs_fences_pair_up():
    """The same fence check for every Markdown file at the root and under docs/."""
    paths = sorted(ROOT.glob("*.md")) + sorted((ROOT / "docs").rglob("*.md"))
    assert len(paths) > 20
    found = {}
    for path in paths:
        problems = fence_problems(path.read_text(encoding="utf-8"))
        if problems:
            found[path.relative_to(ROOT).as_posix()] = problems
    assert found == {}


def test_absolute_links_are_well_formed():
    """Every absolute link (Markdown links, src, srcset and href) is an https URL with a host
    name, no spaces and no trailing punctuation caught from the sentence. Nothing is fetched."""
    links = re.findall(r"\]\(([a-z]+:[^)]*)\)", README)
    links += re.findall(r'\b(?:src|srcset|href)="([a-z]+:[^"]*)"', README)
    assert len(links) > 15
    for link in links:
        parts = urlsplit(link)
        assert parts.scheme == "https", link
        assert re.fullmatch(r"([a-z0-9-]+\.)+[a-z]{2,}", parts.hostname or ""), link
        assert not re.search(r"\s", link) and not link.endswith((".", ",", ";", ":")), link


# Try a drill ---------------------------------------------------------------------------------

DRILL_COMMANDS = ["polarizer drill --calls 10 --condition guided", "polarizer drill report"]


def test_try_a_drill_commands():
    """The section's sh block is exactly the drill and the report, it links the guide, and it
    says the drill is offline."""
    text = section("Try a drill")
    assert commands(text) == DRILL_COMMANDS
    assert "(docs/DRILL-GUIDE.md)" in text and "offline" in text


def test_try_a_drill_excerpt_is_the_guides():
    """The call and the answer shown are a trimmed copy of docs/DRILL-GUIDE.md's, which
    tests/test_drill_guide.py compares with what a drill prints: the call's lines in the
    guide's order, and the answer whole."""
    guide = (ROOT / "docs" / "DRILL-GUIDE.md").read_text(encoding="utf-8")
    guide_call, guide_answer = blocks("text", guide)
    call, answer = blocks("text", section("Try a drill"))
    lines = guide_call.splitlines()
    positions = [lines.index(line) for line in call.splitlines()]
    assert positions == sorted(positions) and len(positions) < len(lines)
    assert answer == guide_answer


@pytest.mark.skipif(sys.platform == "win32", reason="the drill needs a pseudo-terminal")
def test_try_a_drill_runs(tmp_path):
    """The drill on a pseudo-terminal in a temporary home, every call allowed, then the report
    without a terminal, as a person runs them. Each prompt appears within a second here; the
    whole drill has 300 s."""
    import pty

    from test_drill_guide import finish, read_some

    home = tmp_path / "home"
    home.mkdir()
    env = {**os.environ, "HOME": str(home), "USERPROFILE": str(home)}
    controller, terminal = pty.openpty()
    argv = [sys.executable, "-m", "polarizer", *shlex.split(DRILL_COMMANDS[0])[1:]]
    proc = subprocess.Popen(argv, stdin=terminal, stdout=terminal, stderr=terminal, env=env,
                            cwd=tmp_path)  # fmt: skip
    os.close(terminal)
    replies = {
        b"Press Enter to start.": b"\n",
        b"allow or deny? ": b"a\n",
        b"Press Enter for the next call.": b"\n",
        b"Press Enter to see the results.": b"\n",
    }
    output, seen = b"", b""
    deadline = time.monotonic() + 300
    try:
        while time.monotonic() < deadline:
            chunk = read_some(controller, 0.05)
            if chunk is None:
                break
            output += chunk
            seen += chunk
            for prompt, reply in replies.items():
                if seen.endswith(prompt):
                    os.write(controller, reply)
                    seen = b""
                    break
        else:
            pytest.fail(f"the drill did not end:\n{output.decode()}")
        code, rest = finish(controller, proc)
        output += rest
    finally:
        if proc.poll() is None:
            proc.kill()
        os.close(controller)
    assert code == 0, output.decode()
    assert b"Drill finished: 10 of 10 calls answered (guided)." in output
    assert output.count(drill.PLAIN_PREFIX.encode()) == 10
    report = python(*shlex.split(DRILL_COMMANDS[1])[1:], env=env)
    assert report.returncode == 0, report.stderr
    lines = report.stdout.decode().splitlines()
    assert lines[0] == "drills: 1 finished, 0 stopped early; 10 calls answered (scenario set 1)"
    assert "  planted calls: 5. Denied 0: caught 0%, 95% interval 0% to 44%." in lines


# The layout, and the detail that moved out of the README -----------------------------------

SECTIONS = [
    "Why it exists",
    "Try a drill",
    "What a hold looks like",
    "What's different",
    "Compared with Claude Code's permission prompts",
    "Proof",
    "How it works",
    "Related projects",
    "Known limits",
    "Setup",
    "What's here",
    "How it was built",
    "What's next",
]

# One phrase from each limit and each evidence claim of the v0.1 README (commit 9496bef), which
# moved to docs/LIMITS.md and docs/EVIDENCE.md. The two-verifier claim was reworded on purpose.
OLD_LIMITS = (
    "Polarizer only sees calls routed through it",
    "Tools only. Resources, prompts and completions from upstream",
    "Requests from an upstream server to the client (elicitation,",
    "No scanning of tool descriptions, no rules on argument value",
    "Results pass through the MCP Python SDK, which drops fields",
    "Each upstream starts once per session. One that fails to sta",
    "Pins check a tool's definition (name, description, parameter",
    "If you approve a poisoned definition without reading it, Pol",
    "A class is your statement about a tool. Polarizer can't chec",
    "A decision is not instant. A call already under way when you",
    "Only the top-level arguments you name in `path_args` are che",
    "Paths are resolved when the call arrives. A symbolic link cr",
    "A `~` in a hold pattern means the home directory in Polarize",
    "The built-in patterns protect Claude Code's settings in thei",
    "On Windows, the device names `COM1` to `COM3` and `LPT1` to",
    "`polarizer.example.toml` uses POSIX paths. On native Windows",
    "A tool's own annotations (`readOnlyHint`, `destructiveHint`,",
    "A held call waits inside the Polarizer process that Claude C",
    "If you allow a call and that process dies before it forwards",
    "In Claude Code 2.1.289, a call still running after about 123",
    "Claude Code's own permission prompt for an MCP tool comes be",
    "If `MCP_TOOL_TIMEOUT` is set below the hold timeout, Claude",
    "There is no page or notification for holds yet. Run `polariz",
    "A hold is only as good as the person reading it. If you allo",
    "An agent that can run commands as you can also run `polarize",
    "Someone who can write to the ledger directory can rewrite th",
    "Call arguments are kept in side files in plain text, protect",
    "Each entry's time is the wall clock. The chain proves order,",
)

OLD_EVIDENCE = (
    "Five jobs: Linux (ubuntu-24.04) with Python 3.11, 3.12 and 3",
    "agree on every conformance fixture, and on 5,000 randomly damaged chains",
    "The pinned Everything and Filesystem servers (2026.8.31) ran",
    "`scripts/live-check.sh`, run once with Claude Code 2.1.288 a",
    "With Claude Code 2.1.288, Esc during a 30 s call: the upstre",
    "`scripts/rugpull-check.sh` passed twice with Claude Code 2.1",
    "With Claude Code 2.1.289, approving a first group and then a",
    "With Claude Code 2.1.289 at commit c986088, from two termina",
    "docs/verified-facts.md also lists what is not verified",
)


def test_readme_layout():
    """The logo, the hook, the status line, the sections in order, a short page, the text
    diagram in place of the image, and the links to the detail that moved out of it."""
    assert README.count("\n") <= 280
    assert 'srcset="docs/brand/lockup-dark.svg"' in README
    assert "<b>Human in the loop only works if the human is still looking.</b>" in README
    status = README[README.index("\nStatus: ") :].split("\n\n")[0]
    assert status.startswith("\nStatus: v0.1, a preview. Built: the gateway and its ledger, ")
    assert re.search(r"\bM\d", status) is None
    assert re.findall(r"^## (.+)$", README, re.M) == SECTIONS
    assert "(docs/LIMITS.md)" in section("Known limits")
    assert "(docs/EVIDENCE.md)" in section("Proof")
    # The "How it works" image is replaced by a text diagram; the SVG files stay in docs/img/.
    assert "how-it-works" not in README
    assert blocks("text", section("How it works"))
    for name in ("how-it-works-light.svg", "how-it-works-dark.svg"):
        assert (ROOT / "docs" / "img" / name).exists()


def test_related_projects_are_recorded():
    """Every link in Related projects is in docs/verified-facts.md's record of them, and the
    section dates what it says."""
    facts = (ROOT / "docs" / "verified-facts.md").read_text(encoding="utf-8")
    start = facts.index("\n## Related projects (from the owner's research, October 2026")
    record = facts[start : facts.index("\n## ", start + 1)]
    text = section("Related projects")
    links = re.findall(r"\]\((https://[^)]+)\)", text)
    assert len(links) == 12
    assert [link for link in links if f"({link})" not in record] == []
    assert "as their documentation described them in October 2026" in text


def test_limits_and_evidence_moved():
    limits = (ROOT / "docs" / "LIMITS.md").read_text(encoding="utf-8")
    evidence = (ROOT / "docs" / "EVIDENCE.md").read_text(encoding="utf-8")
    assert [p for p in OLD_LIMITS if p not in limits] == []
    assert [p for p in OLD_EVIDENCE if p not in evidence] == []


def test_verifier_claim_is_qualified():
    """The two verifiers were written from one spec by one builder: wherever their agreement
    is claimed for a reader, the shared-misreading caveat and the RFC 8785 vectors go with it."""
    for name in ("README.md", "docs/EVIDENCE.md", "CHANGELOG.md"):
        text = (ROOT / name).read_text(encoding="utf-8")
        assert "standalone reference verifier" not in text, name
        assert "same builder" in text and "RFC 8785 test vectors" in text, name
