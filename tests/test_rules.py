"""The rule function (docs/HOLD-SPEC.md, section 4): every row of its table, the first-match
order, the built-in patterns, and the classes annotations suggest."""

import os
from pathlib import Path

import pytest

from polarizer import config, paths, policy, proxy

R = config.ToolRule


@pytest.fixture
def where(tmp_path):
    """A workspace root, a place outside it, a ledger_dir, a home directory for "~" and a
    config file, all resolved. The home is the policy's own (Policy.home), never the real one,
    so every path below is absolute and under the expanded "~" on every platform."""
    real = Path(os.path.realpath(tmp_path))
    for name in ("root", "outside", "ledger", "home"):
        (real / name).mkdir()
    (real / "polarizer.toml").write_text("", encoding="utf-8")
    return real


def home(where: Path) -> tuple:
    """The policy's home, as Policy.home takes it: (drive or None, parts)."""
    return paths.split(str(where / "home"))


def make(where: Path, *, holds=True, roots=True, **tools) -> policy.Policy:
    upstreams = {
        "fs": policy.UpstreamPolicy(False, tools),
        "t": policy.UpstreamPolicy(True, {}),
    }
    return policy.Policy(
        holds=holds,
        workspace_roots=(str(where / "root"),) if roots else (),
        upstreams=upstreams,
        ledger_dir=str(where / "ledger"),
        config_path=str(where / "polarizer.toml"),
        home=home(where),
    )


def rows(where: Path):
    """(row, policy, prefix, tool, arguments, definition, action, rule, reason)."""
    root, outside, ssh = where / "root", where / "outside", where / "home" / ".ssh"
    aws = where / "home" / ".aws" / "credentials"
    w = R("local-write", ("path",))
    rd = R("local-read", ("path",))
    ow = R("open-world", ("url",))
    a = 'argument "path"'
    yield 1, make(where, holds=False), "fs", "x", {}, {}, "allow", "holds-off", None
    yield (
        2,
        make(where),
        "fs",
        "x",
        {},
        {},
        "hold",
        "unclassified",
        "fs__x has no class in polarizer.toml",
    )
    yield (
        3,
        make(where, x=R("destructive")),
        "fs",
        "x",
        {},
        {},
        "hold",
        "destructive",
        "class destructive is held on every call",
    )
    move = R("destructive", ("source", "destination"))
    moved = {"source": str(root / "a"), "destination": str(ssh / "authorized_keys")}
    yield (
        3,
        make(where, x=move),
        "fs",
        "x",
        moved,
        {},
        "hold",
        "destructive",
        "class destructive is held on every call; "
        f'argument "destination": {ssh / "authorized_keys"} matches ~/.ssh/**',
    )
    inside = {"source": str(root / "a"), "destination": str(root / "b")}
    yield 3, make(where, x=move), "fs", "x", inside, {}, "hold", "destructive", "class destructive is held on every call"  # fmt: skip
    yield (
        4,
        make(where, x=R("egress")),
        "fs",
        "x",
        {},
        {},
        "hold",
        "egress",
        "class egress is held on every call",
    )
    yield (
        4,
        make(where, x=R("egress", ("path",))),
        "fs",
        "x",
        {"path": str(root / ".env")},
        {},
        "hold",
        "egress",
        f"class egress is held on every call; {a}: {root / '.env'} matches .env*",
    )
    yield (
        5,
        make(where, x=R("local-write")),
        "fs",
        "x",
        {"path": str(root / "f")},
        {},
        "hold",
        "write-unchecked",
        "class local-write has no path_args, so its paths cannot be checked",
    )
    yield 6, make(where, x=w), "fs", "x", {}, {}, "hold", "path-missing", f"{a} is missing"
    yield (
        7,
        make(where, x=rd),
        "fs",
        "x",
        {"path": 7},
        {},
        "hold",
        "path-not-string",
        f"{a} is not a string or a list of strings",
    )
    yield (
        8,
        make(where, x=ow),
        "fs",
        "x",
        {"url": "rel"},
        {},
        "hold",
        "path-unresolvable",
        'argument "url": not an absolute path',
    )
    yield (
        9,
        make(where, x=rd),
        "fs",
        "x",
        {"path": str(where / "ledger" / "l")},
        {},
        "hold",
        "polarizer-files",
        f"{a}: {where / 'ledger' / 'l'} is inside Polarizer's ledger directory",
    )
    yield (
        10,
        make(where, x=w),
        "fs",
        "x",
        {"path": str(where / "polarizer.toml")},
        {},
        "hold",
        "polarizer-files",
        f"{a}: {where / 'polarizer.toml'} is Polarizer's config file",
    )
    yield (
        11,
        make(where, x=w),
        "fs",
        "x",
        {"path": str(root / ".env")},
        {},
        "hold",
        "write-pattern",
        f"{a}: {root / '.env'} matches .env*",
    )
    yield (
        12,
        make(where, x=w),
        "fs",
        "x",
        {"path": str(outside / "f")},
        {},
        "hold",
        "outside-roots",
        f"{a}: {outside / 'f'} is outside every workspace root",
    )
    yield (
        12,
        make(where, roots=False, x=w),
        "fs",
        "x",
        {"path": str(root / "f")},
        {},
        "hold",
        "outside-roots",
        f"{a}: {root / 'f'} is outside every workspace root",
    )
    yield (
        13,
        make(where, x=w),
        "fs",
        "x",
        {"path": str(root / "f")},
        {},
        "allow",
        "inside-roots",
        None,
    )
    yield (
        14,
        make(where, x=rd),
        "fs",
        "x",
        {"path": str(root / ".env.local")},
        {},
        "hold",
        "read-pattern",
        f"{a}: {root / '.env.local'} matches .env*",
    )
    yield (
        14,
        make(where, x=ow),
        "fs",
        "x",
        {"url": str(aws)},
        {},
        "hold",
        "read-pattern",
        f'argument "url": {aws} matches ~/.aws/**',
    )
    yield (
        15,
        make(where, x=rd),
        "fs",
        "x",
        {"path": str(outside / "f")},
        {},
        "allow",
        "local-read",
        None,
    )
    yield (
        15,
        make(where, x=rd),
        "fs",
        "x",
        {"path": str(root / "f")},
        {},
        "allow",
        "local-read",
        None,
    )
    yield 16, make(where, x=R("local-read")), "fs", "x", {}, {}, "allow", "local-read", None
    yield (
        17,
        make(where, x=ow),
        "fs",
        "x",
        {"url": str(outside / "f")},
        {},
        "allow",
        "open-world",
        None,
    )
    yield (
        18,
        make(where, x=R("open-world")),
        "fs",
        "x",
        {"url": "https://example.com"},
        {},
        "allow",
        "open-world",
        None,
    )
    yield (
        19,
        make(where),
        "t",
        "x",
        {},
        {},
        "hold",
        "destructive",
        "class destructive is held on every call (class from annotations)",
    )
    ro = {"annotations": {"readOnlyHint": True, "openWorldHint": False}}
    yield 19, make(where), "t", "x", {}, ro, "allow", "local-read", None
    lw = {"annotations": {"readOnlyHint": False, "destructiveHint": False, "openWorldHint": False}}
    yield 19, make(where), "t", "x", {}, lw, "hold", "write-unchecked", "class local-write has no path_args, so its paths cannot be checked (class from annotations)"  # fmt: skip


def test_rule_table(where):
    """One case per row of section 4's table, each with its action, rule and reason exactly.
    Row 20 acts after the rule function, in the gateway (test_holds.py::test_too_many_holds),
    and its reason is checked here."""
    seen = set()
    for row, pol, prefix, tool, arguments, definition, action, rule, reason in rows(where):
        v = policy.evaluate(pol, prefix, tool, arguments, definition)
        assert (v.action, v.rule, v.reason) == (action, rule, reason), row
        seen.add(row)
    assert proxy.TOO_MANY == "too many held calls: 16 already wait in this session"
    assert proxy.MAX_OPEN_HOLDS == 16
    seen.add(20)
    assert seen == set(range(1, 21))


def test_verdict_records_the_class(where):
    pol = make(where, x=R("local-read"))
    v = policy.evaluate(pol, "fs", "x", {}, {})
    assert (v.cls, v.class_from) == ("local-read", "config")
    v = policy.evaluate(pol, "t", "y", {}, {})
    assert (v.cls, v.class_from) == ("destructive", "annotations")
    v = policy.evaluate(pol, "fs", "none", {}, {})
    assert (v.cls, v.class_from) == (None, None)


def test_first_match_order(where):
    """Two path arguments, the first outside the roots and the second on a pattern: the first
    argument's rule; reversed config order reverses it."""
    args = {"a": str(where / "outside" / "f"), "b": str(where / "root" / ".git" / "config")}
    v = policy.evaluate(make(where, x=R("local-write", ("a", "b"))), "fs", "x", args, {})
    assert (v.rule, v.reason.startswith('argument "a"')) == ("outside-roots", True)
    v = policy.evaluate(make(where, x=R("local-write", ("b", "a"))), "fs", "x", args, {})
    assert (v.rule, v.reason.startswith('argument "b"')) == ("write-pattern", True)


WRITE_PATHS = {
    ".git/hooks/**": "<root>/.git/hooks/pre-commit",
    ".git/config": "<root>/.git/config",
    ".github/workflows/**": "<root>/.github/workflows/ci.yml",
    "~/.ssh/**": "<home>/.ssh/authorized_keys",
    ".env*": "<root>/.env",
    "~/.bashrc": "<home>/.bashrc",
    "~/.bash_profile": "<home>/.bash_profile",
    "~/.bash_login": "<home>/.bash_login",
    "~/.bash_logout": "<home>/.bash_logout",
    "~/.profile": "<home>/.profile",
    "~/.zshrc": "<home>/.zshrc",
    "~/.zshenv": "<home>/.zshenv",
    "~/.zprofile": "<home>/.zprofile",
    "~/.zlogin": "<home>/.zlogin",
    "~/.config/fish/**": "<home>/.config/fish/config.fish",
    "~/Documents/PowerShell/**": "<home>/Documents/PowerShell/profile.ps1",
    "~/Documents/WindowsPowerShell/**": "<home>/Documents/WindowsPowerShell/profile.ps1",
    ".claude/**": "<root>/.claude/settings.json",
    ".mcp.json": "<root>/.mcp.json",
    "~/.claude.json": "<home>/.claude.json",
    "~/.claude/**": "<home>/.claude/settings.json",
}
READ_PATHS = {
    "~/.ssh/**": "<home>/.ssh/id_ed25519",
    "~/.gnupg/**": "<home>/.gnupg/private-keys-v1.d/k.key",
    "~/.aws/**": "<home>/.aws/credentials",
    "~/.config/gh/**": "<home>/.config/gh/hosts.yml",
    "~/.netrc": "<home>/.netrc",
    "~/.git-credentials": "<home>/.git-credentials",
    "~/.claude/.credentials.json": "<home>/.claude/.credentials.json",
    ".env*": "<root>/.env.production",
}


def place(where: Path, listed: str) -> str:
    """A listed path with <root> or <home> replaced, joined with the platform's separator."""
    base, _, rest = listed.partition("/")
    return str(
        {"<root>": where / "root", "<home>": where / "home"}[base].joinpath(*rest.split("/"))
    )


def test_builtin_patterns_cannot_be_removed(where):
    """Each built-in pattern holds with a config whose own lists are empty, with no way to
    take one out. The resolver is replaced, and "~" is the test's own home, so no real home
    directory's files are touched."""
    assert set(WRITE_PATHS) >= set(paths.BUILTIN_WRITE)
    assert set(READ_PATHS) == set(paths.BUILTIN_READ)
    for builtin, listing, cls, rule in [
        (paths.BUILTIN_WRITE, WRITE_PATHS, "local-write", "write-pattern"),
        (paths.BUILTIN_READ, READ_PATHS, "local-read", "read-pattern"),
    ]:
        pol = make(where, x=R(cls, ("path",)))
        pol.resolver = lambda p: p  # already resolved, by construction
        for pattern in builtin:
            path = place(where, listing[pattern])
            assert paths.matches(paths.compile_pattern(pattern, home(where)), path, False), path
            # The reason names the first pattern that matches, in the built-in order:
            # ".claude/**" floats, so it comes before "~/.claude/**" for a home path.
            first = next(
                b
                for b in builtin
                if paths.matches(paths.compile_pattern(b, home(where)), path, False)
            )
            v = policy.evaluate(pol, "fs", "x", {"path": path}, {})
            assert (v.rule, v.reason) == (rule, f'argument "path": {path} matches {first}'), path


def test_destructive_and_egress_name_the_path(where):
    """Destructive and egress calls are held on every call, and the rule stays the class; when
    the tool has path_args, the first path rule that would hold is appended to the reason: a
    destructive tool's paths judged as local-write's, an egress tool's as local-read's."""
    root, outside, ledger = where / "root", where / "outside", where / "ledger"
    keys = where / "home" / ".ssh" / "authorized_keys"
    move = make(where, x=R("destructive", ("source", "destination")))
    send = make(where, x=R("egress", ("path",)))
    plain_d, plain_e = (
        "class destructive is held on every call",
        "class egress is held on every call",
    )
    src, dst = 'argument "source"', 'argument "destination"'
    cases = [
        (move, {"source": str(root / "a"), "destination": str(keys)},
         f"{plain_d}; {dst}: {keys} matches ~/.ssh/**"),
        (move, {"source": str(root / "a"), "destination": str(outside / "b")},
         f"{plain_d}; {dst}: {outside / 'b'} is outside every workspace root"),
        (move, {"source": str(root / "a"), "destination": str(root / ".git" / "hooks" / "x")},
         f"{plain_d}; {dst}: {root / '.git' / 'hooks' / 'x'} matches .git/hooks/**"),
        (move, {"source": "rel", "destination": str(keys)},
         f"{plain_d}; {src}: not an absolute path"),
        (move, {"source": str(root / "a")}, f"{plain_d}; {dst} is missing"),
        (move, {"source": 5, "destination": str(root / "b")},
         f"{plain_d}; {src} is not a string or a list of strings"),
        (move, {"source": str(root / "a"), "destination": str(where / "polarizer.toml")},
         f"{plain_d}; {dst}: {where / 'polarizer.toml'} is Polarizer's config file"),
        (move, {"source": str(root / "a"), "destination": str(root / "b")}, plain_d),
        (send, {"path": str(ledger / "l")},
         f"{plain_e}; argument \"path\": {ledger / 'l'} is inside Polarizer's ledger directory"),
        (send, {"path": str(where / "home" / ".aws" / "credentials")},
         f"{plain_e}; argument \"path\": {where / 'home' / '.aws' / 'credentials'} matches ~/.aws/**"),
        # Judged as a read: outside the roots, or on a write pattern only, adds nothing.
        (send, {"path": str(outside / "f")}, plain_e),
        (send, {"path": str(root / ".git" / "hooks" / "x")}, plain_e),
    ]  # fmt: skip
    for pol, arguments, reason in cases:
        v = policy.evaluate(pol, "fs", "x", arguments, {})
        cls = "destructive" if pol is move else "egress"
        assert (v.action, v.rule, v.reason, v.cls) == ("hold", cls, reason, cls), arguments


SUGGESTIONS = [
    ({"readOnlyHint": True, "openWorldHint": False}, "local-read"),
    ({"readOnlyHint": True, "destructiveHint": True, "openWorldHint": False}, "local-read"),
    ({"readOnlyHint": True, "openWorldHint": True}, "open-world"),
    ({"readOnlyHint": True}, "open-world"),
    ({"readOnlyHint": False, "destructiveHint": True}, "destructive"),
    ({"readOnlyHint": False}, "destructive"),
    ({}, "destructive"),
    ({"destructiveHint": False, "openWorldHint": False}, "local-write"),
    ({"readOnlyHint": False, "destructiveHint": False, "openWorldHint": True}, "egress"),
    ({"destructiveHint": False}, "egress"),
    (None, "destructive"),
    ({"readOnlyHint": "yes", "openWorldHint": False}, "destructive"),
    ({"readOnlyHint": True, "openWorldHint": 0}, "open-world"),
]


@pytest.mark.parametrize("annotations, cls", SUGGESTIONS)
def test_annotation_suggestions(annotations, cls):
    assert policy.suggested_class(annotations) == cls


def test_contradiction_rank():
    assert policy.contradicts("local-read", "destructive")
    assert not policy.contradicts("destructive", "local-read")
    assert not policy.contradicts("local-read", "open-world")
    assert not policy.contradicts("open-world", "local-read")
    assert policy.contradicts("local-write", "egress")
    assert not policy.contradicts("egress", "destructive")


def test_trusted_annotations_classify(where):
    """No class on a trust_annotations upstream: the suggested class, recorded as from
    annotations; a configured class wins."""
    readonly = {"annotations": {"readOnlyHint": True, "openWorldHint": False}}
    pol = make(where)
    pol.upstreams["t"] = policy.UpstreamPolicy(True, {"cfg": R("egress")})
    v = policy.evaluate(pol, "t", "free", {}, readonly)
    assert (v.action, v.rule, v.cls, v.class_from) == (
        "allow",
        "local-read",
        "local-read",
        "annotations",
    )
    v = policy.evaluate(pol, "t", "cfg", {}, readonly)
    assert (v.action, v.rule, v.cls, v.class_from) == ("hold", "egress", "egress", "config")
    assert v.reason == "class egress is held on every call"
    untrusted = make(where)
    v = policy.evaluate(untrusted, "fs", "free", {}, readonly)
    assert (v.rule, v.cls) == ("unclassified", None)
