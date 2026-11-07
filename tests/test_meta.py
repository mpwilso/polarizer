"""Tool _meta: served only for the keys meta_allowlist names, and pinned under
POLARIZER-TOOLDEF/2 (docs/SCAN-SPEC.md, section 8, and section 11, tests/test_meta.py)."""

import hashlib
import json

import anyio
import mcp_types as types
import pytest
from helpers import rig
from helpers.fakes import FakeUpstream
from mcp import Client

from polarizer import cli, config, defhash
from polarizer.decisions import Decider

# test_defhash.py's fixed definition and hash (docs/PIN-SPEC.md, section 10).
WAIT = {
    "name": "wait",
    "description": "Wait for a number of seconds.",
    "inputSchema": {
        "type": "object",
        "properties": {"seconds": {"type": "number"}},
        "required": ["seconds"],
    },
}
WAIT_HASH = "ac0778cf2bd27c6f802ca57ffc736ffddcd97d9773898dcc1c9c68ea9bc54e8b"
META = {"vendor/ui": {"icon": "clock"}, "vendor/secret": "not served"}


def tool(obj: dict) -> types.Tool:
    return types.Tool.model_validate(obj, by_alias=True, by_name=False)


def _load(tmp_path, upstream_text: str):
    path = tmp_path / "polarizer.toml"
    ledger = json.dumps((tmp_path / "ledger").as_posix())
    path.write_text(f"ledger_dir = {ledger}\n\n[upstream.p]\n{upstream_text}", encoding="utf-8")
    return config.load(path, require_env=False)


def test_meta_allowlist_config(tmp_path, capsys):
    cfg = _load(tmp_path, 'command = "x"\nmeta_allowlist = ["vendor/ui", "b"]\n')
    assert cfg.upstreams[0].meta_allowlist == ("vendor/ui", "b")
    assert _load(tmp_path, 'command = "x"\n').upstreams[0].meta_allowlist == ()
    line = (
        '[upstream.p] "meta_allowlist" must be a list of at most 32 strings of 1 to 256 characters'
    )
    for bad in ('"a"', "[1]", '[""]', "[" + ", ".join(f'"k{i}"' for i in range(33)) + "]",
                '["' + "k" * 257 + '"]'):  # fmt: skip
        with pytest.raises(config.ConfigError) as caught:
            _load(tmp_path, f'command = "x"\nmeta_allowlist = {bad}\n')
        assert str(caught.value).endswith(line), bad
    # serve refuses it before anything starts, exit 2.
    path = tmp_path / "polarizer.toml"
    assert cli.main(["serve", "--config", str(path)]) == 2
    assert capsys.readouterr().err.rstrip("\n").endswith(line)


def test_no_allowlist_keeps_tooldef_1():
    plain = defhash.definition(tool(WAIT))
    assert plain[0] == WAIT_HASH
    with_meta = tool({**WAIT, "_meta": META})
    assert defhash.definition(with_meta) == plain
    assert defhash.definition(with_meta, ("other/key",)) == plain


def test_allowed_meta_hashed_under_2():
    def_hash, canon = defhash.definition(tool({**WAIT, "_meta": META}), ("vendor/ui", "absent"))
    form = json.loads(canon)
    assert form["_meta"] == {"vendor/ui": {"icon": "clock"}}
    assert def_hash == hashlib.sha256(b"POLARIZER-TOOLDEF/2\n" + canon).hexdigest()
    assert def_hash != hashlib.sha256(b"POLARIZER-TOOLDEF/1\n" + canon).hexdigest()
    assert {k: v for k, v in form.items() if k != "_meta"} == json.loads(
        defhash.definition(tool(WAIT))[1]
    )


def _list(ledger_dir, fake, meta_allow, mode="auto", approve=True):
    async def scenario():
        specs = [rig.spec("f", fake.server, meta_allow=meta_allow)]
        async with rig.gateway(ledger_dir, specs, approve=approve) as gw:
            async with Client(gw.server, mode=mode) as client:
                return (await client.list_tools()).tools

    return anyio.run(scenario)


def test_allowed_meta_served(tmp_path):
    fake = FakeUpstream(definitions=[tool({**WAIT, "_meta": META})])
    for mode in ("auto", "legacy"):
        [served] = _list(tmp_path / mode, fake, ("vendor/ui",), mode)
        assert served.name == "f__wait"
        assert served.meta == {"vendor/ui": {"icon": "clock"}}, mode


def test_meta_change_is_drift(tmp_path):
    ledger_dir = tmp_path / "ledger"
    fake = FakeUpstream(definitions=[tool({**WAIT, "_meta": META})])
    assert len(_list(ledger_dir, fake, ("vendor/ui",))) == 1
    fake.definitions = [tool({**WAIT, "_meta": {**META, "vendor/secret": "changed"}})]
    assert len(_list(ledger_dir, fake, ("vendor/ui",), approve=False)) == 1
    assert rig.kinds(ledger_dir, "tool.drift") == []
    fake.definitions = [tool({**WAIT, "_meta": {**META, "vendor/ui": {"icon": "bell"}}})]
    assert _list(ledger_dir, fake, ("vendor/ui",), approve=False) == []
    assert len(rig.kinds(ledger_dir, "tool.drift")) == 1


def test_migration_stated_at_start(tmp_path, capfd):
    ledger_dir = tmp_path / "ledger"
    fake = FakeUpstream(definitions=[tool({**WAIT, "_meta": META})])
    [first] = _list(ledger_dir, fake, ())
    assert first.meta is None
    capfd.readouterr()
    assert _list(ledger_dir, fake, ("vendor/ui",), approve=False) == []
    err = capfd.readouterr().err
    assert (
        "polarizer: 1 tools changed form because meta_allowlist changed what _meta they serve "
        "(POLARIZER-TOOLDEF/2 serves _meta, /1 none); each is hidden until approved again; run "
        "polarizer pending\n"
    ) in err
    [drift] = rig.kinds(ledger_dir, "tool.drift")
    assert drift["data"]["approved_hash"] == WAIT_HASH
    decider = Decider.open(ledger_dir)
    try:
        decider.approve_one("f", "wait", drift["data"]["live_hash"], lambda line: None)
    finally:
        decider.close()
    [served] = _list(ledger_dir, fake, ("vendor/ui",), approve=False)
    assert served.meta == {"vendor/ui": {"icon": "clock"}}
    assert "changed form" not in capfd.readouterr().err


def test_copy_prefix_by_form(tmp_path):
    ledger_dir = tmp_path / "ledger"
    ledger_dir.mkdir()
    copies = [
        defhash.definition(tool(WAIT)),
        defhash.definition(tool({**WAIT, "_meta": META}), ("vendor/ui",)),
        defhash.instructions_definition("Use wait to pause."),
    ]
    for def_hash, canon in copies:
        defhash.write_copy(ledger_dir, def_hash, canon)
        assert defhash.read_copy(ledger_dir, def_hash) == json.loads(canon)
    # Bytes stored under another form's prefix: a /2 form named by its /1 hash, a /1 form named
    # by its /2 hash, and instructions named as a tool definition.
    for canon, prefix in (
        (copies[1][1], b"POLARIZER-TOOLDEF/1\n"),
        (copies[0][1], b"POLARIZER-TOOLDEF/2\n"),
        (copies[2][1], b"POLARIZER-TOOLDEF/1\n"),
    ):
        wrong = hashlib.sha256(prefix + canon).hexdigest()
        defhash.write_copy(ledger_dir, wrong, canon)
        with pytest.raises(defhash.CopyProblem, match="is not a valid tool"):
            defhash.read_copy(ledger_dir, wrong)
