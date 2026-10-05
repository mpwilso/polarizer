import json
import os
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
CONFORMANCE = ROOT / "conformance"
sys.path.insert(0, str(CONFORMANCE))
sys.path.insert(0, str(ROOT / "tools"))


def expected():
    return json.loads((CONFORMANCE / "expected.json").read_text(encoding="utf-8"))


def fixture_paths(name):
    """(ledger path, head path or None) for a fixture named like "broken/edit_value"."""
    want = expected()["fixtures"][name]
    head = CONFORMANCE / f"{name}.head" if want["head"] else None
    return CONFORMANCE / f"{name}.jsonl", head


def install_fixture(name, ledger_dir):
    """Copy a fixture into ledger_dir as ledger.jsonl (and ledger.head) and return the dir."""
    ledger_dir = Path(ledger_dir)
    ledger_dir.mkdir(parents=True, exist_ok=True)
    ledger, head = fixture_paths(name)
    (ledger_dir / "ledger.jsonl").write_bytes(ledger.read_bytes())
    if head is not None:
        (ledger_dir / "ledger.head").write_bytes(head.read_bytes())
    return ledger_dir


def mask_paths(text, **places):
    """text with each real path replaced by its placeholder (dir=path gives <dir>), and the
    separators in what follows a placeholder written as "/", so a golden file compares the same
    on every OS. The program prints native paths; only the copy the test compares is changed."""
    if not places:
        return text
    for name, path in sorted(places.items(), key=lambda item: -len(str(item[1]))):
        text = text.replace(str(path), f"<{name}>")
    names = "|".join(places)
    return re.sub(rf"<(?:{names})>\S*", lambda m: m.group(0).replace(os.sep, "/"), text)


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    """A temporary home, so nothing reads or writes the real ~/.local/share or ~/.config."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    return home


CHAIN_ID = "a1b2c3d4e5f60718293a4b5c6d7e8f90"


def build_chain(ledger_dir, specs, *, head_at=None, chain_id=CHAIN_ID):
    """Write a valid v1 ledger with fixed timestamps: genesis, then [(kind, data), ...].
    Writes ledger.head pointing at seq head_at when given. Returns the entries."""
    from polarizer.canon import ZERO_HASH, make_entry
    from polarizer.ledger import head_bytes

    ledger_dir = Path(ledger_dir)
    ledger_dir.mkdir(parents=True, exist_ok=True)
    entries, lines, prev = [], [], ZERO_HASH
    for i, (kind, data) in enumerate([("ledger.genesis", {"chain_id": chain_id}), *specs]):
        entry, line = make_entry(i, f"2026-10-02T15:00:00.{i % 1000:03d}Z", kind, data, prev)
        entries.append(entry)
        lines.append(line)
        prev = entry["hash"]
    (ledger_dir / "ledger.jsonl").write_bytes(b"".join(lines))
    if head_at is not None:
        e = entries[head_at]
        (ledger_dir / "ledger.head").write_bytes(head_bytes(chain_id, e["hash"], e["seq"]))
    return entries
