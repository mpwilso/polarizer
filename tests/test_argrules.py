"""Argument rules (docs/TAINT-SPEC.md, sections 2 to 5): domains, url_args and deny_values in
polarizer.toml, their error lines, the policy hash, Polarizer's own reading of a URL's host, the
domain match, the egress rows of the rule function, and the deny tier in a running gateway."""

import json
import os
import re
import sys
import tomllib
from pathlib import Path

import anyio
import pytest
from conftest import ROOT
from helpers import rig
from helpers.fakes import FakeUpstream

from polarizer import argrules, cli, config, policy
from polarizer import paths as paths_mod
from polarizer.config import ToolRule
from polarizer.text import safe

FAST = {"watch_interval": 0.05, "hold_watch_interval": 0.05, "hold_timeout": 0.2}
NOT_ALLOWED = "polarizer: f__echo was not allowed"
WHERE = '[upstream.fs.tools] "post"'


def write(tmp_path: Path, text: str, name: str = "polarizer.toml") -> Path:
    path = tmp_path / name
    path.write_text(f'ledger_dir = "{(tmp_path / "ledger").as_posix()}"\n{text}', encoding="utf-8")
    return path


def probe_table(log: Path) -> str:
    return (
        f"[upstream.fs]\ncommand = {rig.toml_str(sys.executable)}\n"
        f"args = [{rig.toml_str(rig.PROBE)}]\nenv = {{ PROBE_LOG = {rig.toml_str(log)} }}\n"
        "[upstream.fs.tools]\n"
    )


def regex_message(pattern: str) -> str:
    try:
        re.compile(pattern)
    except re.error as e:
        return str(e)
    raise AssertionError(f"{pattern!r} compiles")


def _errors():
    """(the tools entry for "post", the error line after the file's name)."""
    egress = 'class = "egress", url_args = ["url"]'
    cases = [
        ('post = { class = "egress", domains = ["a.example"], url_args = "url" }',
         f"{WHERE} url_args must be a list of at most 16 argument names"),
        (f'post = {{ class = "egress", url_args = {json.dumps(["a"] * 17)}, domains = ["a.b"] }}',
         f"{WHERE} url_args must be a list of at most 16 argument names"),
        ('post = { class = "egress", url_args = [""], domains = ["a.b"] }',
         f"{WHERE} url_args must be a list of at most 16 argument names"),
        (f'post = {{ {egress}, domains = "a.example" }}',
         f"{WHERE} domains must be a list of at most 64 host names"),
        (f"post = {{ {egress}, domains = {json.dumps(['a.example'] * 65)} }}",
         f"{WHERE} domains must be a list of at most 64 host names"),
        (f"post = {{ {egress}, domains = [1] }}",
         f"{WHERE} domains must be a list of at most 64 host names"),
        ('post = { class = "egress", domains = ["a.example"] }',
         f"{WHERE} domains and url_args go together"),
        ('post = { class = "egress", url_args = ["url"] }',
         f"{WHERE} domains and url_args go together"),
        ('post = { class = "open-world", url_args = ["url"], domains = ["a.example"] }',
         f"{WHERE} domains and url_args go only with class egress"),
        ('post = { class = "local-read", url_args = ["url"], domains = ["a.example"] }',
         f"{WHERE} domains and url_args go only with class egress"),
        ('post = { class = "egress", deny_values = ["x"] }',
         f"{WHERE} deny_values must be a table of at most 16 argument names, each with a list of "
         "patterns"),
        ("post = { class = \"egress\", deny_values = { "
         + ", ".join(f'a{i} = ["x"]' for i in range(17)) + " } }",
         f"{WHERE} deny_values must be a table of at most 16 argument names, each with a list of "
         "patterns"),
        ('post = { class = "egress", deny_values = { "" = ["x"] } }',
         f"{WHERE} deny_values must be a table of at most 16 argument names, each with a list of "
         "patterns"),
    ]  # fmt: skip
    for bad in ('"x"', "[]", json.dumps(["x"] * 33), "[1]", '[""]', json.dumps(["x" * 1025])):
        cases.append(
            (
                f'post = {{ class = "egress", deny_values = {{ body = {bad} }} }}',
                f'{WHERE} deny_values "body" must be a list of 1 to 32 patterns of 1 to 1024 '
                "characters",
            )
        )
    for pattern in ("(", "a{2,1}", "[z-a]", "(?P<x>a)(?P<x>b)"):
        cases.append(
            (
                f'post = {{ class = "egress", deny_values = {{ body = [{json.dumps(pattern)}] }} }}',
                f'{WHERE} deny_values "body" pattern "{safe(pattern)}" is not a valid regular '
                f"expression: {safe(regex_message(pattern))}",
            )
        )
    for entry, why in [
        ("", "empty"),
        (".".join(["a" * 60] * 4) + "." + "b" * 20, None),
        ("b" + chr(0xFC) + "cher.example", "not ASCII; write it in its xn-- form"),
        ("*", 'a wildcard only as a leading "*."'),
        ("*.", 'a wildcard only as a leading "*."'),
        ("a.*.example", 'a wildcard only as a leading "*."'),
        ("*a.example", 'a wildcard only as a leading "*."'),
        ("**.example", 'a wildcard only as a leading "*."'),
        ("10.0.0.1", "an IP address; list host names only"),
        ("[::1]", "an IP address; list host names only"),
        ("::1", "an IP address; list host names only"),
        ("2130706433", "an IP address; list host names only"),
        ("a.0x7f", "an IP address; list host names only"),
        ("a..example", "an empty label"),
        (".example", "an empty label"),
        ("example.", "an empty label"),
        ("a" * 64 + ".example", "a label longer than 63 characters"),
        ("a_b.example", 'a character other than a letter, a digit or "-"'),
        ("a b.example", 'a character other than a letter, a digit or "-"'),
        ("a.example:443", 'a character other than a letter, a digit or "-"'),
        ("a/b.example", 'a character other than a letter, a digit or "-"'),
        ("-a.example", 'a label that starts or ends with "-"'),
        ("a-.example", 'a label that starts or ends with "-"'),
    ]:
        if why is None:
            assert len(entry) > 253
            why = "longer than 253 characters"
        cases.append(
            (
                f"post = {{ {egress}, domains = [{json.dumps(entry, ensure_ascii=False)}] }}",
                f'{WHERE} domains entry "{safe(entry)}" is not a host name: {why}',
            )
        )
    return cases


def test_argrule_errors(tmp_path, capsys, fake_home):
    """Each error line of section 2, exactly, exit 2, and serve starts no upstream."""
    log = tmp_path / "probe.log"
    for i, (entry, line) in enumerate(_errors()):
        path = write(tmp_path, probe_table(log) + entry + "\n", f"polarizer{i}.toml")
        code = cli.main(["serve", "--config", str(path)])
        out, err = capsys.readouterr()
        assert (code, out, err) == (2, "", f"polarizer{i}.toml: {line}\n"), entry
    assert not log.exists()


def test_valid_keys_load(tmp_path):
    """The three keys load into the tool's rule: domains lowercased, deny_values in order."""
    text = (
        '[upstream.fs]\ncommand = "x"\n[upstream.fs.tools]\n'
        'post = { class = "egress", url_args = ["url", "urls"], '
        'domains = ["API.Example.COM", "*.cdn.example"], '
        'deny_values = { body = ["secret", "(?i)password"], to = ["@evil\\\\.example$"] } }\n'
        'fetch = { class = "open-world", deny_values = { url = ["file:"] } }\n'
    )
    cfg = config.load(write(tmp_path, text))
    post = cfg.upstreams[0].tools["post"]
    assert post.url_args == ("url", "urls")
    assert post.domains == ("api.example.com", "*.cdn.example")
    assert post.deny_values == (("body", ("secret", "(?i)password")), ("to", ("@evil\\.example$",)))
    fetch = cfg.upstreams[0].tools["fetch"]
    assert (fetch.url_args, fetch.domains, fetch.deny_values) == ((), (), (("url", ("file:",)),))


def test_policy_hash_unchanged_without_the_keys(tmp_path):
    """No key, no change: test_policy_config.py's fixed hash stays. With them, the form gains
    exactly the three, domains lowercased."""
    from test_policy_config import FIXED, FIXED_SHA

    built = policy.build(config.load(write(tmp_path, FIXED)))
    assert built.sha256 == FIXED_SHA
    text = FIXED + (
        "[upstream.web.tools]\n"
        'post = { class = "egress", url_args = ["url"], domains = ["A.Example"], '
        'deny_values = { body = ["x"] } }\n'
        'plain = { class = "egress" }\n'
    )
    form = policy.build(config.load(write(tmp_path, text, "keys.toml"))).form()
    assert form["upstreams"]["web"]["tools"] == {
        "post": {
            "class": "egress",
            "path_args": [],
            "url_args": ["url"],
            "domains": ["a.example"],
            "deny_values": {"body": ["x"]},
        },
        "plain": {"class": "egress", "path_args": []},
    }


# Reading the host (section 3) -------------------------------------------------------------


IP = "an IP address, which never matches a domain"


@pytest.mark.parametrize(
    "url, why",
    [
        ("https://a.example/" + "x" * 8200, "longer than 8192 characters"),
        ("https://a.example/a b", "a space or control character"),
        ("https://a.example/" + chr(9), "a space or control character"),
        ("https://a.example/" + chr(0x7F), "a space or control character"),
        ("https://a.example" + chr(0x5C) + "@evil.example/", "a backslash"),
        ("a.example/path", "not an absolute URL with a host"),
        ("//a.example/", "not an absolute URL with a host"),
        ("mailto:bob@a.example", "not an absolute URL with a host"),
        ("1http://a.example/", "not an absolute URL with a host"),
        ("https://a@b@a.example/", 'more than one "@" before the host'),
        ("https://[::1]/", IP),
        ("https://user@[::1]:443/", IP),
        ("https://a.example:99999/", "a port that is not a number from 0 to 65535"),
        ("https://a.example:/", "a port that is not a number from 0 to 65535"),
        ("https://a.example:12a/", "a port that is not a number from 0 to 65535"),
        ("https://a.example:1:2/", "a port that is not a number from 0 to 65535"),
        ("https://a.example:123456/", "a port that is not a number from 0 to 65535"),
        ("https:///path", "no host"),
        ("https://user@/path", "no host"),
        ("https://:443/", "no host"),
        ("https://b" + chr(0xFC) + "cher.example/", "a host that is not ASCII; send its xn-- form"),
        ("https://a" + chr(0x3002) + "example/", "a host that is not ASCII; send its xn-- form"),
        ("https://a%2eexample/", "a host that is not a host name"),
        ("https://a..example/", "a host that is not a host name"),
        ("https://a.example../", "a host that is not a host name"),
        ("https://a!b.example/", "a host that is not a host name"),
        ("https://.a.example/", "a host that is not a host name"),
        ("http://127.0.0.1/", IP),
        ("http://2130706433/", IP),
        ("http://0x7f.1/", IP),
        ("http://a.example.0x1F/", IP),
        ("http://a.123/", IP),
    ],
)
def test_host_reading_holds(url, why):
    with pytest.raises(argrules.NotChecked) as caught:
        argrules.host_of(url)
    assert str(caught.value) == why


@pytest.mark.parametrize(
    "url, host",
    [
        ("https://a.example/", "a.example"),
        ("https://a.example", "a.example"),
        ("HTTPS://A.EXAMPLE/X", "a.example"),
        ("wss://a.example/socket", "a.example"),
        ("ftp://a.example/f", "a.example"),
        ("git+ssh://a.example/r", "a.example"),
        ("https://user:pass@a.example/", "a.example"),
        ("https://user@a.example:8443/x", "a.example"),
        ("https://a.example:0/", "a.example"),
        ("https://a.example:65535/", "a.example"),
        ("https://a.example./", "a.example"),
        ("https://a.example?u=https://b.example", "a.example"),
        ("https://a.example#@b.example", "a.example"),
        ("https://a.example/@b.example", "a.example"),
        ("https://xn--bcher-kva.example/", "xn--bcher-kva.example"),
        ("https://under_score.example/", "under_score.example"),
        ("https://localhost/", "localhost"),
        ("https://0x.example/", "0x.example"),
    ],
)
def test_host_reading_accepts(url, host):
    assert argrules.host_of(url) == host


def test_domain_matching():
    domains = ("a.example", "*.b.example", "xn--bcher-kva.example")
    assert argrules.listed("a.example", domains)
    assert not argrules.listed("api.a.example", domains)
    assert not argrules.listed("a.example.evil.net", domains)
    assert not argrules.listed("aa.example", domains)
    assert not argrules.listed("b.example", domains)
    assert argrules.listed("x.b.example", domains)
    assert argrules.listed("x.y.b.example", domains)
    assert not argrules.listed("xb.example", domains)
    assert argrules.listed("xn--bcher-kva.example", domains)
    assert not argrules.listed("xn--bcher-kva.example.net", domains)


def test_domain_entry_problem():
    assert argrules.domain_problem("api.github.com") is None
    assert argrules.domain_problem("*.example.com") is None
    assert argrules.domain_problem("localhost") is None
    assert argrules.domain_problem("xn--bcher-kva.example") is None
    assert argrules.domain_problem("0x.example") is None
    assert argrules.domain_problem("a.0x7f") == "an IP address; list host names only"


# The rule function (section 5) ------------------------------------------------------------


def web_policy(**rule) -> policy.Policy:
    """Upstream "f": echo is egress with the given rule (url_args ["url"] and the domains
    a.example and *.b.example unless given), wait is open-world."""
    rule.setdefault("url_args", ("url",))
    rule.setdefault("domains", ("a.example", "*.b.example"))
    tools = {"echo": ToolRule("egress", **rule), "wait": ToolRule("open-world")}
    return policy.Policy(upstreams={"f": policy.UpstreamPolicy(False, tools)})


def verdict(pol, arguments, taint=None):
    return policy.evaluate(pol, "f", "echo", arguments, {}, taint=taint)


def test_egress_rows(tmp_path):
    pol = web_policy()
    v = verdict(pol, {"url": "https://a.example/x"})
    assert (v.action, v.rule, v.reason, v.cls, v.class_from) == (
        "allow", "domains", None, "egress", "config"
    )  # fmt: skip
    v = verdict(pol, {"url": ["https://a.example/", "https://c.b.example/"]})
    assert (v.action, v.rule) == ("allow", "domains")
    v = verdict(pol, {"url": ["https://a.example/", "https://evil.example/"]})
    assert (v.action, v.rule, v.reason) == (
        "hold", "egress", 'argument "url": host evil.example is not in this tool\'s domains'
    )  # fmt: skip
    v = verdict(pol, {"body": "x"})
    assert (v.rule, v.reason) == ("egress", 'argument "url" is missing')
    v = verdict(pol, {"url": 7})
    assert v.reason == 'argument "url" is not a string or a list of strings'
    v = verdict(pol, {"url": ["https://a.example/"] * 257})
    assert v.reason == 'argument "url": more than 256 URLs'
    v = verdict(pol, {"url": []})
    assert (v.action, v.rule, v.reason) == ("hold", "egress", "no URL in url_args to check")
    v = verdict(pol, {"url": "http://127.0.0.1/"})
    assert v.reason == 'argument "url": an IP address, which never matches a domain'
    v = verdict(pol, None)
    assert v.reason == 'argument "url" is missing'

    # With path_args, judged as local-read: appended to a URL reason (4a), alone when every URL
    # is listed (4b), and allowed when no path rule holds (4d).
    real = Path(os.path.realpath(tmp_path))
    secret = str(real / "home" / ".ssh" / "id_ed25519")
    paths = policy.Policy(
        upstreams=web_policy(path_args=("file",)).upstreams,
        home=paths_mod.split(str(real / "home")),
    )
    v = verdict(paths, {"url": "https://a.example/", "file": secret})
    assert (v.action, v.rule) == ("hold", "egress")
    assert v.reason.startswith('argument "file": ') and v.reason.endswith(" matches ~/.ssh/**")
    v = verdict(paths, {"url": "https://evil.example/", "file": secret})
    assert v.reason.startswith(
        'argument "url": host evil.example is not in this tool\'s domains; argument "file": '
    )
    fine = str(real / "notes.txt")
    v = verdict(paths, {"url": "https://a.example/", "file": fine})
    assert (v.action, v.rule) == ("allow", "domains")

    # Two url_args, checked in order: the first problem is the reason.
    two = web_policy(url_args=("to", "cc"))
    v = verdict(two, {"to": "https://a.example/", "cc": "https://x.example/"})
    assert v.reason == 'argument "cc": host x.example is not in this tool\'s domains'
    v = verdict(two, {"to": [], "cc": "https://a.example/"})
    assert v.rule == "domains"

    # No domains: HOLD-SPEC.md's row 4, unchanged.
    plain = web_policy(url_args=(), domains=())
    v = verdict(plain, {"url": "https://a.example/"})
    assert (v.rule, v.reason) == ("egress", "class egress is held on every call")

    # A class from annotations has no entry: row 4 with row 19's suffix.
    trusted = policy.Policy(upstreams={"t": policy.UpstreamPolicy(True, {})})
    v = policy.evaluate(trusted, "t", "send", {"url": "https://a.example/"},
                        {"annotations": {"destructiveHint": False}})  # fmt: skip
    assert (v.rule, v.reason, v.class_from) == (
        "egress", "class egress is held on every call (class from annotations)", "annotations"
    )  # fmt: skip


def test_deny_tier_in_the_rule_function():
    deny = (("body", ("secret", "(?i)pass(word)?")), ("to", ("@evil\\.example$",)))
    pol = web_policy(deny_values=deny)
    good = {"url": "https://a.example/"}
    v = verdict(pol, {**good, "body": "my Password is"})
    assert (v.action, v.rule, v.reason, v.cls) == (
        "deny", "deny-value", 'argument "body" matches the deny_values pattern (?i)pass(word)?',
        "egress",
    )  # fmt: skip
    # Config order of arguments first, then patterns in order.
    v = verdict(pol, {**good, "to": "x@evil.example", "body": "secret"})
    assert v.reason == 'argument "body" matches the deny_values pattern secret'
    v = verdict(pol, {**good, "to": "x@evil.example"})
    assert v.reason == 'argument "to" matches the deny_values pattern @evil\\.example$'
    # Nested strings, object keys, and numbers as their JSON text.
    assert verdict(pol, {**good, "body": {"a": ["x", {"secret": 1}]}}).action == "deny"
    assert verdict(pol, {**good, "body": [{"k": "no secret here"}]}).action == "deny"
    deep = "secret"
    for _ in range(5000):  # deeper than Python's recursion limit: walked with a stack
        deep = [deep]
    assert verdict(pol, {**good, "body": deep}).action == "deny"
    nums = web_policy(deny_values=(("n", ("^12$", "^true$", "^null$", "^1.5$")),))
    for value in (12, True, None, 1.5, [1, [12]], {"k": None}):
        assert verdict(nums, {**good, "n": value}).action == "deny", value
    assert verdict(nums, {**good, "n": 13}).action == "allow"
    # A missing argument never matches; no normalization.
    assert verdict(pol, good).action == "allow"
    assert verdict(pol, {**good, "body": "sec" + chr(0x200B) + "ret"}).action == "allow"
    # The deny tier comes before the holds, and applies to any class.
    held = web_policy(deny_values=deny)
    v = verdict(held, {"url": "https://evil.example/", "body": "secret"})
    assert v.action == "deny"
    reads = policy.Policy(
        upstreams={"f": policy.UpstreamPolicy(False, {
            "read": ToolRule("local-read", deny_values=(("q", ("x",)),))
        })}
    )  # fmt: skip
    assert policy.evaluate(reads, "f", "read", {"q": "x"}, {}).action == "deny"
    assert policy.evaluate(reads, "f", "read", {"q": "y"}, {}).action == "allow"
    # --no-holds skips it, as every rule.
    off = web_policy(deny_values=deny)
    off.holds = False
    assert verdict(off, {**good, "body": "secret"}).rule == "holds-off"


# In a running gateway ---------------------------------------------------------------------


def setup(**rule):
    fake = FakeUpstream()
    specs = [rig.spec("f", fake.server)]
    pol = rig.classified(specs)
    rule.setdefault("url_args", ("url",))
    rule.setdefault("domains", ("a.example", "*.b.example"))
    pol.upstreams["f"].tools["echo"] = ToolRule("egress", **rule)
    pol.upstreams["f"].tools["wait"] = ToolRule("open-world")
    return fake, specs, pol


def test_allowlisted_egress_runs(tmp_path):
    fake, specs, pol = setup()
    ld = tmp_path / "ledger"

    async def main():
        async with rig.proxied(ld, specs, policy=pol, **FAST) as (client, _):
            result = await client.call_tool("f__echo", {"url": "https://A.example/x", "n": 1})
            assert not result.is_error
            assert json.loads(result.content[0].text) == {"url": "https://A.example/x", "n": 1}

    anyio.run(main)
    assert [c[0] for c in fake.calls] == ["echo"]
    [sent] = rig.kinds(ld, "call.sent")
    assert sent["data"]["allowed_by"] == "domains" and "hold" not in sent["data"]
    assert rig.kinds(ld, "hold.created") == []


TRICKY = [
    ("https://api.a.example/", 'argument "url": host api.a.example is not in this tool\'s domains'),
    ("https://b.example/", 'argument "url": host b.example is not in this tool\'s domains'),
    ("https://a.example@evil.example/", 'argument "url": host evil.example is not in this tool\'s domains'),
    ("https://a@b@a.example/", 'argument "url": more than one "@" before the host'),
    ("https://a.example:99999/", 'argument "url": a port that is not a number from 0 to 65535'),
    ("http://127.0.0.1/", f'argument "url": {IP}'),
    ("http://[::1]:8080/", f'argument "url": {IP}'),
    ("https://a" + chr(0x3002) + "example/", 'argument "url": a host that is not ASCII; send its xn-- form'),
    ("https://a.example.evil.example/", 'argument "url": host a.example.evil.example is not in this tool\'s domains'),
    ("https://EVIL.EXAMPLE/", 'argument "url": host evil.example is not in this tool\'s domains'),
    ("a.example/x", 'argument "url": not an absolute URL with a host'),
]  # fmt: skip
RUNS = [
    "HTTPS://A.EXAMPLE/X",
    "https://user:pw@a.example/",
    "https://a.example:8443/x",
    "https://a.example./",
    "https://c.b.example/",
]


def test_tricky_hosts_are_held(tmp_path):
    """Each tricky URL is held with its reason and never reaches the upstream; the accepted
    forms of a listed host run."""
    fake, specs, pol = setup()
    ld = tmp_path / "ledger"

    async def main():
        async with rig.proxied(ld, specs, policy=pol, **FAST) as (client, _):
            for url, _ in TRICKY:
                result = await client.call_tool("f__echo", {"url": url})
                assert result.content[0].text == NOT_ALLOWED, url
            for url in RUNS:
                result = await client.call_tool("f__echo", {"url": url})
                assert not result.is_error, url

    anyio.run(main)
    created = rig.kinds(ld, "hold.created")
    assert [(c["data"]["rule"], c["data"]["reason"]) for c in created] == [
        ("egress", reason) for _, reason in TRICKY
    ]
    assert [c[1]["url"] for c in fake.calls] == RUNS
    assert [e["data"]["allowed_by"] for e in rig.kinds(ld, "call.sent")] == ["domains"] * len(RUNS)


def test_deny_values_refuse_and_record(tmp_path, capsys):
    fake, specs, pol = setup(deny_values=(("body", ("BEGIN [A-Z ]*PRIVATE KEY",)),))
    ld = tmp_path / "ledger"
    body = "-----BEGIN OPENSSH PRIVATE KEY-----"

    async def main():
        async with rig.proxied(ld, specs, policy=pol, **FAST) as (client, _):
            capsys.readouterr()
            result = await client.call_tool("f__echo", {"url": "https://a.example/", "body": body})
            assert result.is_error and result.content[0].text == NOT_ALLOWED
            ok = await client.call_tool("f__echo", {"url": "https://a.example/", "body": "hi"})
            assert not ok.is_error

    anyio.run(main)
    reason = 'argument "body" matches the deny_values pattern BEGIN [A-Z ]*PRIVATE KEY'
    [refused] = rig.kinds(ld, "call.refused")
    assert refused["data"] == {
        "session": refused["data"]["session"],
        "tool": "f__echo",
        "reason": reason,
        "rule": "deny-value",
    }
    assert rig.kinds(ld, "hold.created") == []
    assert [c[1]["body"] for c in fake.calls] == ["hi"]
    sent = rig.kinds(ld, "call.sent")
    assert len(sent) == 1
    side_files = sorted((ld / "args").iterdir())
    assert [p.name for p in side_files] == [f"{sent[0]['data']['args_commit']}.bin"]
    err = capsys.readouterr().err
    assert f"polarizer: refused f__echo (deny-value): {reason}\n" in err


def test_example_config_loads(tmp_path):
    """polarizer.example.toml's commented M2b example, uncommented, loads, and gives the
    rules its comments describe."""
    text = (ROOT / "polarizer.example.toml").read_text(encoding="utf-8")
    block = re.search(r"^# \[upstream\.web\]\n(.*?)(?:\n\n|\Z)", text, re.M | re.S)
    assert block, "polarizer.example.toml has no commented [upstream.web] example"
    lines = ["[upstream.web]"]
    for line in block.group(1).split("\n"):
        assert line.startswith("#"), line
        stripped = line[1:].lstrip(" ") if not line.startswith("# #") else ""
        if stripped and not stripped.startswith("#"):
            lines.append(stripped)
    uncommented = "\n".join(lines) + "\n"
    tomllib.loads(uncommented)
    cfg = config.load(write(tmp_path, uncommented), require_env=False)
    [web] = cfg.upstreams
    assert web.tools["fetch"].cls == "open-world"
    post = [r for r in web.tools.values() if r.cls == "egress" and r.domains]
    assert post and post[0].url_args and post[0].deny_values
