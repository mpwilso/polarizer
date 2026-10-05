"""Reads what scripts/hold-check.sh needs: polarizer.toml, `polarizer pending`'s output, and the
ledger with its holds. Runs on the repo's .venv, so it reads holds with Polarizer's own fold
and session probe (docs/HOLD-SPEC.md, sections 5 and 7). Values the script uses go to stdout;
text for the person goes to stderr, which the script prints and records.

    hold_check.py toml <polarizer.toml> ledger_dir|timeout
    hold_check.py group                          (pending's output on stdin)
    hold_check.py token <ledger dir>
    hold_check.py open-hold <ledger dir> [<hold id to skip>]
    hold_check.py trail <ledger dir> <hold id>
    hold_check.py ended <ledger dir> <hold id>
    hold_check.py returned <ledger dir> <hold id>
    hold_check.py loaded <ledger dir> <timeout seconds>
    hold_check.py loaded-more <ledger dir> <timeout seconds> <count before>

Exit 0 on success, 1 when what it looked for is not there (the reason on stderr), 2 on a usage
error, and 3 from `group` when nothing is pending at all.
"""

import json
import os
import sys
import tomllib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import m1a_check  # noqa: E402

from polarizer import holds, lock  # noqa: E402


def say(text: str = "") -> None:
    print(text, file=sys.stderr)


def toml_value(path: Path, key: str) -> str:
    """ledger_dir with ~ expanded, or [policy] hold_timeout_seconds; "" when absent."""
    with open(path, "rb") as f:
        doc = tomllib.load(f)
    if key == "ledger_dir":
        value = doc.get("ledger_dir", "")
        return os.path.normpath(os.path.expanduser(value)) if value else ""
    if key == "timeout":
        return str(doc.get("policy", {}).get("hold_timeout_seconds", ""))
    raise ValueError(f"unknown key {key}")


def group(text: str) -> int:
    """The group id from pending's output, when everything pending is new and in the group, as
    after a fresh first run. Prints the id; says how many definitions it covers."""
    try:
        (new, changed, unservable), blocks = m1a_check.parse(text)
    except ValueError as e:
        say(f"cannot read polarizer pending's output: {e}")
        return 1
    if (new, changed, unservable) == (0, 0, 0):
        return 3
    found = [b for b in blocks if b.kind == "group"]
    covered = int(found[0].match.group(2)) if found else 0
    say(f"new definitions: {new}, changed: {changed}, unservable: {unservable}")
    if changed or unservable or not found or covered != new:
        say(
            f"the group covers {covered} of them; a fresh first run puts every definition in "
            "it, so this ledger is not fresh: run scripts/hold-check.sh reset"
        )
        return 1
    print(found[0].match.group(1))
    return 0


def entries(ledger_dir: Path) -> list[dict]:
    """The ledger's complete lines, read while serve may be writing."""
    data = (Path(ledger_dir) / "ledger.jsonl").read_bytes()
    return [json.loads(line) for line in data[: data.rfind(b"\n") + 1].splitlines()]


def folded(ledger_dir: Path) -> holds.HoldState:
    state = holds.HoldState()
    for entry in entries(ledger_dir):
        state.apply(entry)
    return state


def token(ledger_dir: Path) -> int:
    """The first 8 hex characters of the ledger's chain id: random per fresh ledger, so the
    files this check asks Claude to write are new each time."""
    first = entries(ledger_dir)[0]
    print(first["data"]["chain_id"][:8])
    return 0


def open_hold(ledger_dir: Path, skip: str | None = None) -> int:
    """The newest open hold whose session is running (other than `skip`): its id."""
    state = folded(ledger_dir)
    running = [
        h
        for h in state.open_holds()
        if h.hold != skip and holds.session_state(ledger_dir, h.session) == lock.RUNNING
    ]
    if not running:
        say("no open hold of a running session")
        return 1
    newest = running[-1]
    say(f"{newest.tool} held by {newest.rule}: {newest.reason}")
    print(newest.hold)
    return 0


def _one_line(data: dict, kind: str, hold: str) -> str:
    if kind == "hold.created":
        return f"{data['tool']}, class {data['class']}, held by {data['rule']}: {data['reason']}"
    if kind == "hold.decided":
        reason = f" ({data['reason']})" if data.get("reason") else ""
        return f"{data['decision']}{reason}"
    if kind == "call.sent":
        return f"{data['tool']}, allowed_by {data.get('allowed_by')}"
    if kind == "call.returned":
        return f"outcome {data['outcome']}, latency_ms {data['latency_ms']}"
    return str(data.get("reason") or data.get("held_by") or "")


def trail(ledger_dir: Path, hold: str) -> int:
    """Every entry for the hold, and the call.returned of its call.sent, one line each."""
    sent = set()
    lines = []
    for e in entries(ledger_dir):
        data, kind = e["data"], e["kind"]
        if data.get("hold") == hold or kind == "call.returned" and data.get("call_seq") in sent:
            if kind == "call.sent":
                sent.add(e["seq"])
            lines.append(f"seq {e['seq']} {kind}: {_one_line(data, kind, hold)}")
    if not lines:
        say(f"no entry names hold {hold}")
        return 1
    for line in lines:
        print(m1a_check.printable(line))
    return 0


def ended(ledger_dir: Path, hold: str) -> int:
    held = folded(ledger_dir).get(hold)
    if held is None or held.ending is None:
        return 1
    print(f"{held.ending.kind} at seq {held.ending.seq}")
    return 0


def returned(ledger_dir: Path, hold: str) -> int:
    """The outcome of the hold's forwarded call, once its call.returned is in the ledger."""
    sent = None
    for e in entries(ledger_dir):
        if e["kind"] == "call.sent" and e["data"].get("hold") == hold:
            sent = e["seq"]
        elif e["kind"] == "call.returned" and sent is not None and e["data"]["call_seq"] == sent:
            print(f"outcome {e['data']['outcome']}, latency_ms {e['data']['latency_ms']}")
            return 0
    return 1


def loaded(ledger_dir: Path, seconds: str) -> int:
    """How many serve starts recorded hold_timeout_seconds = seconds in policy.loaded."""
    count = sum(
        e["kind"] == "policy.loaded" and str(e["data"].get("hold_timeout_seconds")) == seconds
        for e in entries(ledger_dir)
    )
    print(count)
    return 0


def loaded_more(ledger_dir: Path, seconds: str, before: str) -> int:
    """Success once more serve starts have recorded that timeout than `before`."""
    count = sum(
        e["kind"] == "policy.loaded" and str(e["data"].get("hold_timeout_seconds")) == seconds
        for e in entries(ledger_dir)
    )
    return 0 if count > int(before) else 1


def main(argv: list[str]) -> int:
    try:
        match argv:
            case ["toml", path, key]:
                print(toml_value(Path(path), key))
                return 0
            case ["group"]:
                return group(sys.stdin.read())
            case ["token", ledger_dir]:
                return token(Path(ledger_dir))
            case ["open-hold", ledger_dir, *skip] if len(skip) <= 1:
                return open_hold(Path(ledger_dir), skip[0] if skip else None)
            case ["trail", ledger_dir, hold]:
                return trail(Path(ledger_dir), hold)
            case ["ended", ledger_dir, hold]:
                return ended(Path(ledger_dir), hold)
            case ["returned", ledger_dir, hold]:
                return returned(Path(ledger_dir), hold)
            case ["loaded", ledger_dir, seconds]:
                return loaded(Path(ledger_dir), seconds)
            case ["loaded-more", ledger_dir, seconds, before]:
                return loaded_more(Path(ledger_dir), seconds, before)
    except (OSError, ValueError, KeyError, IndexError) as e:
        say(f"hold_check.py: {type(e).__name__}: {e}")
        return 1
    say(__doc__.split("\n\n")[1])
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
