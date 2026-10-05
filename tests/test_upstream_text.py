"""Upstream text on stderr and in the ledger (docs/dev/STAGE5-NOTES.md, Upstream text): whatever an
upstream sends, or an exception built from it, reaches serve's stderr and the ledger only
through polarizer.text.safe(): escaped, on one line, at most 200 characters.

Each test feeds HOSTILE text (escape sequences, a bell, a newline, a carriage return, 5,000
characters, a bidi override and a lone surrogate) through an upstream that fails at connect,
fails a refresh and drops its connection, then checks every stderr line and every string in
the ledger. An upstream's own protocol-error message reaches the client unchanged, by design;
the ledger's copy goes through safe() too, cut to 1 KiB instead of 200 characters."""

import json
import logging
import re
import sys

import anyio
import mcp_types as types
import pydantic
import pytest
from helpers import hostile_server, rig
from helpers.fakes import FakeUpstream
from helpers.raw import RawClient
from mcp import MCPError
from mcp_types import CONNECTION_CLOSED

from polarizer.ledger import verify_bytes
from polarizer.text import SAFE_LIMIT, safe
from polarizer.upstream import SCHEMA_MISMATCH, SafeLog, describe

ESC, BEL, LONE = chr(27), chr(7), chr(0xD800)
HOSTILE = hostile_server.HOSTILE + LONE  # the stub's text, plus what only memory can carry
BACKSLASH = chr(0x5C)


def assert_clean(text: str, what: str) -> None:
    """No raw control character, newline, non-ASCII character or surrogate, and nothing near
    the 5,000-character run."""
    bad = [c for c in text if not (" " <= c <= "~")]
    assert not bad, f"{what}: raw characters {bad[:5]!r} in {text[:120]!r}"
    assert "Z" * (SAFE_LIMIT + 1) not in text, f"{what}: the long run got through"


def assert_stderr_clean(err: str) -> None:
    for line in err.splitlines():
        assert_clean(line, "stderr")
        assert len(line) < 2 * SAFE_LIMIT, f"stderr line of {len(line)} characters"
        assert not line.startswith("INJECTED"), "the upstream started a stderr line of its own"


def strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for k, v in value.items():
            yield k
            yield from strings(v)
    elif isinstance(value, list):
        for v in value:
            yield from strings(v)


def assert_ledger_clean(ledger_dir) -> list[dict]:
    data = (ledger_dir / "ledger.jsonl").read_bytes()
    assert verify_bytes(data, (ledger_dir / "ledger.head").read_bytes()).status == "intact"
    entries = [json.loads(line) for line in data.splitlines()]
    for entry in entries:
        for text in strings(entry["data"]):
            assert_clean(text, f"seq {entry['seq']} {entry['kind']}")
    return entries


# safe() and describe() -------------------------------------------------------------------


def test_safe_escapes_folds_and_cuts():
    assert safe("plain text") == "plain text"
    assert safe(f"a{ESC}[31mb{BEL}") == f"a{BACKSLASH}x1b[31mb{BACKSLASH}x07"
    assert safe("one\ntwo\r\n  three\tfour") == "one two three four"
    assert safe(f"x{LONE}y{chr(0x202E)}z") == f"x{BACKSLASH}ud800y{BACKSLASH}u202ez"
    assert safe(chr(0x1F600)) == f"{BACKSLASH}ud83d{BACKSLASH}ude00"
    long = safe("Z" * 5000)
    assert len(long) == SAFE_LIMIT and long.endswith("...")
    for text in (HOSTILE, "Z" * 5000, ESC * 300, chr(0x1F600) * 100, "a" + chr(0x1F600) * 50):
        once = safe(text)
        assert len(once) <= SAFE_LIMIT
        assert_clean(once, "safe()")
        assert safe(once) == once  # applying it twice changes nothing
    # An escape is never split: cutting 'a' + 50 emoji keeps whole 12-character pairs.
    cut = safe("a" + chr(0x1F600) * 50)
    assert (len(cut) - len("a...")) % 12 == 0


def test_describe_is_safe_and_quotes_no_pydantic_input():
    assert describe(ValueError(HOSTILE)) == safe(HOSTILE)
    assert describe(ExceptionGroup("g", [RuntimeError(HOSTILE)])) == safe(HOSTILE)
    assert describe(RuntimeError()) == "RuntimeError"
    with pytest.raises(pydantic.ValidationError) as caught:
        types.ListToolsResult.model_validate({"tools": [{"name": "t", "inputSchema": HOSTILE}]})
    text = describe(caught.value)
    assert text == f"{SCHEMA_MISMATCH}: dict_type at tools.0.inputSchema"


def test_log_records_are_one_safe_line(capsys):
    """SafeLog, which serve installs for every log record at WARNING and above, the SDK's
    included: one safe line, the exception described instead of a traceback."""
    logger = logging.getLogger("polarizer-test-safelog")
    logger.propagate = False
    handler = SafeLog(logging.WARNING)
    logger.addHandler(handler)
    try:
        logger.warning("dropping tool %r: %s", HOSTILE, HOSTILE)
        try:
            raise ValueError(HOSTILE)
        except ValueError:
            logger.exception("Failed to parse JSONRPC message from server")
        logger.info("below WARNING: %s", HOSTILE)
    finally:
        logger.removeHandler(handler)
    err = capsys.readouterr().err
    assert_stderr_clean(err)
    lines = err.splitlines()
    assert len(lines) == 2
    assert lines[0].startswith("polarizer: polarizer-test-safelog: dropping tool ")
    assert lines[1].startswith(
        "polarizer: polarizer-test-safelog: Failed to parse JSONRPC message from server ("
    )


# In memory: a raw lone surrogate, which no stdio upstream can deliver ---------------------


class _Dropped:
    """Stands in for an upstream's SDK client once its connection has dropped: the call fails
    with -32000 and HOSTILE as its message, and the ping finds the connection closed."""

    def __init__(self):
        self.session = self

    async def call_tool(self, *args, **kwargs):
        raise MCPError(code=CONNECTION_CLOSED, message=HOSTILE)

    async def send_ping(self):
        raise MCPError(code=CONNECTION_CLOSED, message="Connection closed")


def test_hostile_upstream_in_memory(tmp_path, capsys):
    """At connect, on a refresh and on a dropped connection, with a raw lone surrogate in the
    text: nothing raw reaches stderr or the ledger, and the client's transport-error line is
    the same safe line."""
    bad, flaky, dropping = FakeUpstream(), FakeUpstream(), FakeUpstream()
    bad.fail_list, bad.fail_message = True, HOSTILE
    ledger_dir = tmp_path / "ledger"

    async def scenario():
        specs = [rig.spec("bad", bad.server), rig.spec("flaky", flaky.server)]
        specs.append(rig.spec("drop", dropping.server))
        async with rig.proxied(ledger_dir, specs) as (client, gw):
            flaky.fail_list, flaky.fail_message = True, HOSTILE
            await client.list_tools()  # flaky's refresh fails
            gw.upstreams["drop"].client = _Dropped()
            result = await client.call_tool("drop__echo", {})
            return result.content[0].text

    reply = anyio.run(scenario)
    assert_clean(reply, "the client's reply")
    assert reply.startswith("polarizer: upstream drop failed: ")
    assert_stderr_clean(capsys.readouterr().err)
    entries = assert_ledger_clean(ledger_dir)
    (bad_entry,) = [
        e for e in entries if e["kind"] == "upstream.connected" and "error" in e["data"]
    ]
    assert bad_entry["data"]["prefix"] == "bad"
    assert BACKSLASH + "x1b" in bad_entry["data"]["error"]  # escaped, not dropped
    failed = {
        e["data"]["prefix"]: e["data"] for e in rig.kinds(ledger_dir, "upstream.refresh_failed")
    }
    assert failed["flaky"]["trigger"] == "client-list"
    assert failed["drop"]["trigger"] == "connection-lost"
    assert failed["drop"]["error"].endswith("...")
    (returned,) = [e for e in entries if e["kind"] == "call.returned"]
    assert returned["data"]["outcome"] == "transport-error"
    assert returned["data"]["error"] == reply


def test_hostile_protocol_error(tmp_path):
    """An upstream's protocol-error message (ESC, a newline, a lone surrogate, a bidi override,
    a C1 control and 5,000 characters) reaches the client unchanged, with its code, as
    PROXY-SPEC.md asks. The ledger's copy goes through safe(): escaped, on one line, cut to
    1 KiB."""
    message = (
        ESC + "[2J\nINJECTED polarizer: a line of its own\r" + LONE + chr(0x202E) + chr(0x9B)
        + "Z" * 5000
    )  # fmt: skip
    fake = FakeUpstream()
    fake.refuse_message = message
    ledger_dir = tmp_path / "ledger"

    async def scenario():
        async with rig.proxied(ledger_dir, [rig.spec("f", fake.server)]) as (client, _):
            with pytest.raises(MCPError) as refused:
                await client.call_tool("f__refuse", {})
            return refused.value

    error = anyio.run(scenario)
    assert (error.code, error.message) == (-32042, message)  # the client's copy is the original
    data = (ledger_dir / "ledger.jsonl").read_bytes()
    assert verify_bytes(data, (ledger_dir / "ledger.head").read_bytes()).status == "intact"
    (returned,) = [e["data"] for e in rig.kinds(ledger_dir, "call.returned")]
    assert (returned["outcome"], returned["code"]) == ("protocol-error", -32042)
    recorded = returned["error"]
    assert recorded == safe(message, 1024)
    assert all(" " <= c <= "~" for c in recorded), "a raw character in the ledger's copy"
    assert len(recorded) == 1024 and recorded.endswith("...")
    assert recorded.startswith(
        BACKSLASH + "x1b[2J INJECTED polarizer: a line of its own "
        + BACKSLASH + "ud800" + BACKSLASH + "u202e" + BACKSLASH + "x9bZZZ"
    )  # fmt: skip
    # Canonical JSON writes ESC as an escape on its own, but stores a C1 control as raw UTF-8.
    assert chr(0x9B).encode("utf-8") not in data


# Over stdio, through polarizer serve -----------------------------------------------------


def hostile_config(tmp_path):
    stub = rig.toml_str(rig.HELPERS / "hostile_server.py")
    tables = []
    for prefix, mode in [
        ("ce", "connect-error"),
        ("cg", "connect-garbage"),
        ("re", "refresh-error"),
        ("ri", "refresh-invalid"),
        ("dr", "drop"),
    ]:
        tables.append(
            f"[upstream.{prefix}]\ncommand = {rig.toml_str(sys.executable)}\nargs = [{stub}]\n"
            f'env = {{ HOSTILE_MODE = "{mode}" }}\nconnect_timeout_seconds = 1\n'
        )
    return rig.serve_config(tmp_path, "\n".join(tables), tmp_path / "ledger")


def test_hostile_upstream_over_stdio(tmp_path):
    """polarizer serve in front of five hostile stubs: two fail at connect (an error message,
    and lines the SDK can't parse, one with a lone surrogate), two fail a refresh (an error
    message, and a listing pydantic refuses), and one lists a hostile tool name and drops its
    connection during a call. serve's stderr, both runs, and the ledger hold nothing raw."""
    cfg = hostile_config(tmp_path)
    primed = rig.run_serve(cfg)  # stdin closed: connect, list, record, exit
    assert primed.returncode == 0
    ledger_dir = tmp_path / "ledger"
    assert rig.approve_all(ledger_dir) == 3  # re, ri and dr each list boom

    err_path = tmp_path / "serve.err"
    with open(err_path, "wb") as err_file:
        client = RawClient(
            [sys.executable, "-m", "polarizer", "serve", "--config", str(cfg)], stderr=err_file
        )
        try:
            client.initialize()
            listed = client.request("tools/list", {})["result"]["tools"]
            assert [t["name"] for t in listed] == ["dr__boom"]  # re and ri failed their refresh
            params = {"name": "dr__boom", "arguments": {}}
            reply = client.request("tools/call", params)["result"]["content"][0]["text"]
        finally:
            assert client.close() == 0

    assert_clean(reply, "the client's reply")
    assert reply.startswith("polarizer: upstream dr failed: ")
    err = primed.stderr.decode("utf-8", "replace") + err_path.read_text("utf-8", "replace")
    assert ESC not in err and BEL not in err
    assert_stderr_clean(err)
    assert "polarizer: upstream ce did not connect: " + BACKSLASH + "x1b[2J" in err
    assert "polarizer: upstream cg did not connect: timeout after 1 s" in err
    sdk = "polarizer: mcp.client.stdio: Failed to parse JSONRPC message from server ("
    assert sdk + SCHEMA_MISMATCH + ": json_invalid)" in err  # SafeLog, not a traceback
    assert "Traceback" not in err
    assert "polarizer: upstream re: listing failed (" + BACKSLASH + "x1b" in err
    ri_line = re.search(
        r"polarizer: upstream ri: listing failed \((.*)\); its tools are hidden", err
    )
    assert ri_line and ri_line[1].startswith(SCHEMA_MISMATCH)  # pydantic's text quotes HOSTILE
    assert ri_line[1].endswith(" at tools.0.inputSchema")
    assert 'polarizer: upstream dr: skipped tool "' + BACKSLASH + "x1b" in err
    assert "polarizer: upstream dr stopped: " + BACKSLASH + "x1b" in err

    entries = assert_ledger_clean(ledger_dir)
    failed = [e["data"] for e in entries if e["kind"] == "upstream.refresh_failed"]
    assert sorted((d["prefix"], d["trigger"]) for d in failed) == [
        ("dr", "connection-lost"),
        ("re", "client-list"),
        ("ri", "client-list"),
    ]
    connected = [e["data"] for e in entries if e["kind"] == "upstream.connected"]
    skipped = [d["skipped_tools"] for d in connected if d["prefix"] == "dr"]
    assert len(skipped) == 2 and all(
        len(names) == 1 and len(names[0]) == SAFE_LIMIT for names in skipped
    )
    (returned,) = [e["data"] for e in entries if e["kind"] == "call.returned"]
    assert (returned["outcome"], returned["error"]) == ("transport-error", reply)
