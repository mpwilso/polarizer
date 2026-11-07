"""scripts/taint_check.py, the taint check, without a model: the files it writes, the stand-in
server it runs, and its refusals. The check itself runs only by hand, since it calls a model
(docs/verified-facts.md, Taint check, headless)."""

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

from conftest import ROOT

from polarizer import config, policy


def _load():
    sys.path.insert(0, str(ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location(
        "taint_check", ROOT / "scripts" / "taint_check.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_setup_writes_a_config_serve_accepts(tmp_path):
    check = _load()
    polarizer = tmp_path / "bin" / "polarizer"
    paths = check.setup(tmp_path, polarizer)
    cfg = config.load(paths["config"], require_env=False)
    assert cfg.ledger_dir == tmp_path / "ledger"
    [web] = cfg.upstreams
    assert web.prefix == "web" and web.args == (str(ROOT / "tests" / "helpers" / "web_stub.py"),)
    assert web.env == {"WEB_STUB_LOG": str(tmp_path / "posts.jsonl")}
    assert web.tools["fetch"].cls == "open-world"
    post = web.tools["post"]
    assert (post.cls, post.url_args, post.domains) == ("egress", ("url",), ("allowed.example",))
    mcp = json.loads(paths["mcp"].read_text(encoding="utf-8"))
    assert mcp == {
        "mcpServers": {
            "pz": {"command": str(polarizer), "args": ["serve", "--config", str(paths["config"])]}
        }
    }
    built = policy.build(cfg)
    grant = policy.evaluate(built, "web", "post", {"url": check.ALLOWED, "body": "x"}, {})
    assert (grant.action, grant.rule) == ("allow", "domains")


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
    assert capsys.readouterr().err == "taint-check: claude is not on PATH\n"
    assert not (tmp_path / ".repro").exists()


def test_web_stub_answers(tmp_path):
    """The stand-in lists fetch and post, fetches without the network, and logs each post."""
    log = tmp_path / "posts.jsonl"
    stub = ROOT / "tests" / "helpers" / "web_stub.py"
    requests = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
         "params": {"name": "fetch", "arguments": {"url": "https://news.example/"}}},
        {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
         "params": {"name": "post", "arguments": {"url": "https://a.example/", "body": "hi"}}},
    ]  # fmt: skip
    done = subprocess.run(
        [sys.executable, str(stub)],
        input="".join(json.dumps(r) + "\n" for r in requests),
        capture_output=True, text=True, timeout=30,
        env={**os.environ, "WEB_STUB_LOG": str(log)},
    )  # fmt: skip
    answers = [json.loads(line) for line in done.stdout.splitlines()]
    assert [t["name"] for t in answers[1]["result"]["tools"]] == ["fetch", "post"]
    assert "https://news.example/" in answers[2]["result"]["content"][0]["text"]
    assert answers[3]["result"]["content"][0]["text"] == "posted 2 characters"
    assert [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()] == [
        {"url": "https://a.example/", "body": "hi"}
    ]
    assert Path(stub).read_text(encoding="utf-8").count("urllib") == 0
