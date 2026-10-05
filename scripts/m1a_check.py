"""Reads what scripts/m1a-check.sh needs: polarizer.toml, the output of `polarizer pending`, and
the ledger. Stdlib only. Text for the person goes to stderr, which the shell script prints and
records; the one value the script uses goes to stdout.

    m1a_check.py toml <polarizer.toml> ledger_dir|rugpull|expected
    m1a_check.py group <expected count>        (pending's output on stdin)
    m1a_check.py changed                       (pending's output on stdin)
    m1a_check.py count <ledger dir> drift|group-approved|wait-approved-alone
    m1a_check.py last-refused <ledger dir>

Exit 0 on success, 1 when what it found is not what the check expects (the reason on stderr),
2 on a usage error, and 3 from `group` when no new definition is pending at all.
"""

import json
import os
import re
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path

# How many tools each upstream of manual/polarizer.manual.toml lists (docs/dev/MANUAL-CHECK.md,
# M1a).
EXPECTED_TOOLS = {"probe": 7, "fs": 14}
NOTHING = "pending: nothing waits for a decision"
HEX = "[0-9a-f]{64}"
SUMMARY = re.compile(r"pending: (\d+) new, (\d+) changed, (\d+) unservable\Z")
NEW = re.compile(rf"new (\S+) ({HEX})(, after rejecting {HEX})?\Z")
CHANGED = re.compile(rf"changed (\S+) ({HEX}), approved ({HEX})\Z")
GROUP = re.compile(rf"group ({HEX}) covers the (\d+) new definitions above ")


def say(text: str = "") -> None:
    print(text, file=sys.stderr)


def printable(text: str) -> str:
    """Text from the ledger as printable ASCII: anything else as \\xNN or \\uNNNN."""
    out = []
    for c in text:
        n = ord(c)
        if 0x20 <= n <= 0x7E:
            out.append(c)
        elif n < 0x100:
            out.append(f"\\x{n:02x}")
        else:
            out.append(c.encode("unicode_escape").decode("ascii"))
    return "".join(out)


# --- polarizer.toml


def toml_value(path: Path, key: str) -> str:
    """ledger_dir with ~ expanded, the probe's PROBE_RUGPULL, or how many new definitions the
    first run should list. An absent value is an empty string."""
    with open(path, "rb") as f:
        doc = tomllib.load(f)
    upstreams = doc.get("upstream", {})
    if key == "ledger_dir":
        value = doc.get("ledger_dir", "")
        return os.path.normpath(os.path.expanduser(value)) if value else ""
    if key == "rugpull":
        return upstreams.get("probe", {}).get("env", {}).get("PROBE_RUGPULL", "")
    if key == "expected":
        unknown = sorted(set(upstreams) - set(EXPECTED_TOOLS))
        if unknown:
            raise ValueError(f"the check doesn't know how many tools {', '.join(unknown)} lists")
        return str(sum(EXPECTED_TOOLS[name] for name in upstreams))
    raise ValueError(f"unknown key {key}")


# --- polarizer pending's output (docs/PIN-SPEC.md, section 7)


@dataclass
class Block:
    header: str
    kind: str  # new, changed, unservable or group
    match: re.Match | None
    note: str | None = None  # the line saying why a block is not in the group
    problem: str | None = None  # a stored copy that fails its check
    definition: dict | None = None
    cls: str | None = None  # the class line of pending --config

    @property
    def name(self) -> str:
        return self.match.group(1) if self.match else ""


def parse(text: str) -> tuple[tuple[int, int, int], list[Block]]:
    """The counts from pending's first line, and its blocks in order."""
    parts = text.strip("\n").split("\n\n")
    if parts[0] == NOTHING:
        return (0, 0, 0), []
    summary = SUMMARY.match(parts[0])
    if summary is None:
        raise ValueError(f"not the output of polarizer pending: {printable(parts[0][:200])}")
    counts = (int(summary.group(1)), int(summary.group(2)), int(summary.group(3)))
    blocks = []
    for part in parts[1:]:
        header, *rest = part.split("\n")
        if header.startswith("classes: "):
            break  # pending --config's classes section comes last, after the group line
        kind = header.split(" ", 1)[0]
        pattern = {"new": NEW, "changed": CHANGED, "group": GROUP}.get(kind)
        block = Block(header, kind, pattern.match(header) if pattern else None)
        # After the header: a note (not in a group, more than one definition), the class
        # line that pending --config adds (docs/HOLD-SPEC.md, section 8), then the definition
        # or a stored-copy problem.
        if rest and not rest[0].startswith(("{", "stored copy ", "class ")):
            block.note = rest.pop(0)
        if rest and rest[0].startswith("class "):
            block.cls = rest.pop(0)
        if rest and rest[0].startswith("stored copy "):
            block.problem = rest.pop(0)
        if rest:
            block.definition = json.loads("\n".join(rest))
        blocks.append(block)
    return counts, blocks


def annotation(definition: dict | None) -> str:
    hints = (definition or {}).get("annotations") or {}

    def shown(value):
        return {True: "yes", False: "no", None: "not set"}.get(value, printable(repr(value)))

    read_only, destructive = hints.get("readOnlyHint"), hints.get("destructiveHint")
    return f"read-only: {shown(read_only)}, destructive: {shown(destructive)}"


def group(text: str, expected: int) -> int:
    """For `approve`: the first run's group, if it is exactly what a fresh first run lists."""
    counts, blocks = parse(text)
    news = [b for b in blocks if b.kind == "new"]
    say(text.strip("\n").split("\n")[0])
    if not news:
        say("no new definition is pending")
        return 3
    say(f"new definitions: {counts[0]} (the check expects {expected})")
    width = max(len(b.name) for b in news)
    for b in news:
        say(f"  {b.name.ljust(width)}  {annotation(b.definition)}")
    names = [b.name for b in news]
    several = sorted({n for n in names if names.count(n) > 1})
    say(f"tools with more than one waiting hash: {', '.join(several) or 'none'}")
    problems = []
    if counts != (expected, 0, 0):
        problems.append(
            f"pending lists {counts[0]} new, {counts[1]} changed and {counts[2]} unservable;"
            f" a fresh first run lists {expected} new and nothing else"
        )
    problems += [f"{b.name}: {b.note}" for b in news if b.note]
    problems += [f"{b.name}: {b.problem}" for b in news if b.problem]
    groups = [b for b in blocks if b.kind == "group" and b.match]
    if not groups:
        problems.append("pending printed no group line")
    elif int(groups[0].match.group(2)) != expected:
        problems.append(f"the group covers {groups[0].match.group(2)} definitions, not {expected}")
    if problems:
        say("not groupable as a fresh first run:")
        for p in problems:
            say(f"  {p}")
        if "probe__wait" in several:
            say(
                "probe__wait has more than one definition, so the probe started more than once"
                " before this approval, and every start after the first is the rug pull."
                " Polarizer must start only once before approve."
            )
        return 1
    print(groups[0].match.group(1))
    return 0


def changed(text: str) -> int:
    """For `rugpull` and `approve-changed`: the changed probe__wait block's two hashes."""
    _, blocks = parse(text)
    say(text.strip("\n").split("\n")[0])
    waits = [b for b in blocks if b.kind == "changed" and b.name == "probe__wait" and b.match]
    others = [b.header for b in blocks if b not in waits and b.kind != "group"]
    for header in others[:5]:
        say(f"also pending: {header}")
    if len(others) > 5:
        say(f"also pending: {len(others) - 5} more")
    if len(waits) != 1:
        say(f"pending shows {len(waits)} changed probe__wait blocks; the check expects 1")
        return 1
    wait = waits[0]
    say(wait.header)
    if wait.problem:
        say(wait.problem)
        return 1
    say(f"new description: {json.dumps((wait.definition or {}).get('description'))}")
    print(wait.match.group(2), wait.match.group(3))
    return 0


# --- the ledger, read without verifying (polarizer verify does that)


def entries(ledger_dir: Path) -> list[tuple[str, dict]]:
    path = ledger_dir / "ledger.jsonl"
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if isinstance(entry, dict):
            out.append((line, entry))
    return out


def count(ledger_dir: Path, what: str) -> int:
    """drift: every tool.drift entry. group-approved: approvals made as part of a group.
    wait-approved-alone: approvals of probe/wait by name after its first drift."""
    n, wait_drifted = 0, False
    for _, e in entries(ledger_dir):
        kind, data = e.get("kind"), e.get("data") or {}
        is_wait = (data.get("upstream"), data.get("tool")) == ("probe", "wait")
        alone = data.get("group") is None
        if kind == "tool.drift":
            wait_drifted = wait_drifted or is_wait
            n += int(what == "drift")
        elif kind == "tool.approved" and what == "group-approved":
            n += int(not alone)
        elif kind == "tool.approved" and what == "wait-approved-alone":
            n += int(wait_drifted and is_wait and alone)
    return n


def last_refused(ledger_dir: Path) -> str:
    lines = [line for line, e in entries(ledger_dir) if e.get("kind") == "call.refused"]
    return printable(lines[-1]) if lines else "none"


def main(argv: list[str]) -> int:
    try:
        match argv:
            case ["toml", path, key]:
                print(toml_value(Path(path), key))
            case ["group", expected]:
                return group(sys.stdin.read(), int(expected))
            case ["changed"]:
                return changed(sys.stdin.read())
            case ["count", ledger_dir, what]:
                print(count(Path(ledger_dir), what))
            case ["last-refused", ledger_dir]:
                print(last_refused(Path(ledger_dir)))
            case _:
                say(__doc__.strip().split("\n\n")[1])
                return 2
    except (OSError, ValueError, tomllib.TOMLDecodeError) as e:
        say(f"m1a_check: {printable(str(e))}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
