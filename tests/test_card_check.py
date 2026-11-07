"""scripts/card_check.py, the card's headless check, without a model: the files it writes and
its refusals. The check itself runs only by hand, since it calls a model (docs/verified-facts.md,
Card check, headless)."""

import importlib.util
import json
import tomllib
from pathlib import Path

from conftest import ROOT

from polarizer import config


def _load():
    spec = importlib.util.spec_from_file_location("card_check", ROOT / "scripts" / "card_check.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_setup_writes_a_config_serve_accepts(tmp_path):
    check = _load()
    polarizer = tmp_path / "bin" / "polarizer"
    paths = check.setup(tmp_path, polarizer)
    assert (paths["work"] / ".git" / "hooks").is_dir()
    cfg = config.load(paths["config"], require_env=False)
    assert cfg.ledger_dir == tmp_path / "ledger"
    assert cfg.policy.workspace_roots == (paths["work"],)
    [fs] = cfg.upstreams
    assert fs.args == ("-y", check.FILESYSTEM, paths["work"].as_posix())
    example = tomllib.loads((ROOT / "polarizer.example.toml").read_text(encoding="utf-8"))
    assert fs.args[1] == example["upstream"]["fs"]["args"][1]
    assert {n: (r.cls, list(r.path_args)) for n, r in fs.tools.items()} == {
        n: (t["class"], t.get("path_args", []))
        for n, t in example["upstream"]["fs"]["tools"].items()
    }
    mcp = json.loads(paths["mcp"].read_text(encoding="utf-8"))
    assert mcp == {
        "mcpServers": {
            "pz": {"command": str(polarizer), "args": ["serve", "--config", str(paths["config"])]}
        }
    }
    assert "planted_calls_per_session" not in paths["config"].read_text(encoding="utf-8")


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
    assert capsys.readouterr().err == "card-check: claude is not on PATH\n"
    assert not any(Path(p).name.startswith("polarizer-card-check.") for p in tmp_path.iterdir())


def test_setup_with_planting(tmp_path):
    """--planted writes planted_calls_per_session = 1, which serve's config reads."""
    check = _load()
    paths = check.setup(tmp_path, tmp_path / "polarizer", planted=1)
    assert config.load(paths["config"], require_env=False).policy.planted_calls_per_session == 1
