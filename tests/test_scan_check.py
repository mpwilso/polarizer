"""scripts/scan_check.py, the scan check, without a model: the files it writes, the stand-in
server it runs, what `pending` and the scanner make of it, and its refusals. The check itself
runs only by hand, since it calls a model (docs/verified-facts.md, Scan check, headless)."""

import importlib.util
import json
import os
import subprocess
import sys

import mcp_types as types
from conftest import ROOT
from helpers import lab_stub

from polarizer import config, defhash, scan


def _load():
    sys.path.insert(0, str(ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location("scan_check", ROOT / "scripts" / "scan_check.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_setup_writes_a_config_serve_accepts(tmp_path):
    check = _load()
    polarizer = tmp_path / "bin" / "polarizer"
    paths = check.setup(tmp_path, polarizer)
    cfg = config.load(paths["config"], require_env=False)
    assert cfg.ledger_dir == tmp_path / "ledger"
    [lab] = cfg.upstreams
    assert lab.prefix == "lab" and lab.args == (str(ROOT / "tests" / "helpers" / "lab_stub.py"),)
    assert lab.env == {"LAB_STUB_LOG": str(tmp_path / "lookup-calls.jsonl")}
    assert {name: rule.cls for name, rule in lab.tools.items()} == {
        "add": "local-read",
        "echo": "local-read",
        "lookup": "local-read",
    }
    mcp = json.loads(paths["mcp"].read_text(encoding="utf-8"))
    assert mcp == {
        "mcpServers": {
            "pz": {"command": str(polarizer), "args": ["serve", "--config", str(paths["config"])]}
        }
    }


def test_pinned_model_and_budget():
    check = _load()
    assert check.MODEL == "claude-haiku-4-5-20251001"
    assert check.BUDGET == "1"


def test_refuses_without_claude(tmp_path, monkeypatch, capsys):
    check = _load()
    monkeypatch.setattr(check, "REPO", tmp_path)
    assert check.main([]) == 2
    assert "not found; run uv sync --locked first" in capsys.readouterr().err
    venv = tmp_path / ".venv" / "bin"
    venv.mkdir(parents=True)
    (venv / "polarizer").write_text("", encoding="utf-8")
    monkeypatch.setattr(check.shutil, "which", lambda name: None)
    assert check.main([]) == 2
    assert capsys.readouterr().err == "scan-check: claude is not on PATH\n"
    assert not (tmp_path / ".repro").exists()


def test_lab_stub_answers(tmp_path):
    """The stand-in lists its three tools and instructions, adds, and logs lookup's calls."""
    log = tmp_path / "calls.jsonl"
    requests = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
         "params": {"name": "add", "arguments": {"a": 2, "b": 3}}},
        {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
         "params": {"name": "lookup", "arguments": {"word": "w", "note": "n"}}},
    ]  # fmt: skip
    done = subprocess.run(
        [sys.executable, str(ROOT / "tests" / "helpers" / "lab_stub.py")],
        input="".join(json.dumps(r) + "\n" for r in requests),
        capture_output=True, text=True, timeout=30,
        env={**os.environ, "LAB_STUB_LOG": str(log)},
    )  # fmt: skip
    answers = [json.loads(line) for line in done.stdout.splitlines()]
    assert lab_stub.PHRASE in answers[0]["result"]["instructions"]
    assert [t["name"] for t in answers[1]["result"]["tools"]] == ["add", "echo", "lookup"]
    assert answers[2]["result"]["content"][0]["text"] == "5"
    assert [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()] == [
        {"word": "w", "note": "n"}
    ]


def test_the_stand_in_is_flagged_as_the_check_expects():
    """lookup has exactly the four rules the check names; add, echo and the instructions none."""
    check = _load()
    for definition in lab_stub.TOOLS:
        copy = json.loads(defhash.definition(types.Tool.model_validate(definition))[1])
        rules = sorted({f.rule for f in scan.scan(copy)})
        assert rules == (check.LOOKUP_RULES if definition["name"] == "lookup" else [])
    assert scan.scan({"instructions": lab_stub.INSTRUCTIONS}) == []
