"""Paths in tool arguments (docs/HOLD-SPEC.md, section 3): resolution, roots, hold patterns
and the case rule, through the rule function where the claim is about a verdict."""

import os
import sys
import time
from pathlib import Path

import pytest

from polarizer import config, paths, policy

POSIX = pytest.mark.skipif(sys.platform == "win32", reason="POSIX permissions and paths")
WINDOWS_ONLY = pytest.mark.skipif(sys.platform != "win32", reason="Windows path names")


def symlink(target: Path, link: Path) -> None:
    """os.symlink, or skip where Windows refuses it (no symlink privilege)."""
    try:
        os.symlink(target, link)
    except OSError as e:
        pytest.skip(f"os.symlink is refused here: {e}")


def write_policy(root: Path | None = None, **options) -> policy.Policy:
    """fs/write (local-write, path_args ["path"]) and fs/read (local-read, ["path"]), with
    `root` as the one workspace root."""
    tools = {
        "write": config.ToolRule("local-write", ("path",)),
        "read": config.ToolRule("local-read", ("path",)),
        "web": config.ToolRule("open-world", ("url",)),
    }
    roots = (os.path.realpath(root),) if root is not None else ()
    upstreams = {"fs": policy.UpstreamPolicy(False, tools)}
    return policy.Policy(workspace_roots=roots, upstreams=upstreams, **options)


def verdict(p: policy.Policy, tool: str, path) -> policy.Verdict:
    key = "url" if tool == "web" else "path"
    return policy.evaluate(p, "fs", tool, {key: path}, {})


@pytest.fixture
def root(tmp_path):
    r = tmp_path / "root"
    r.mkdir()
    return Path(os.path.realpath(r))


def p(path: Path) -> str:
    return str(path)


def test_symlink_escape_is_held(root, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    symlink(outside, root / "link")
    v = verdict(write_policy(root), "write", p(root / "link" / "x"))
    assert (v.action, v.rule) == ("hold", "outside-roots")
    assert v.reason == (
        f'argument "path": {os.path.realpath(outside / "x")} is outside every workspace root'
    )


def test_dotdot_escape_is_held(root, tmp_path):
    (tmp_path / "outside").mkdir()
    v = verdict(write_policy(root), "write", p(root) + "/../outside/x")
    assert (v.action, v.rule) == ("hold", "outside-roots")
    (root / "a").mkdir()
    v = verdict(write_policy(root), "write", p(root) + "/a/../b")
    assert (v.action, v.rule, v.reason) == ("allow", "inside-roots", None)


def test_dotdot_after_missing_part_is_held(root):
    v = verdict(write_policy(root), "write", p(root) + "/new/../../x")
    assert (v.action, v.rule) == ("hold", "path-unresolvable")
    assert v.reason == 'argument "path": ".." after a part that does not exist'


def test_symlink_into_a_pattern_is_held(root):
    (root / ".git" / "hooks").mkdir(parents=True)
    symlink(root / ".git" / "hooks", root / "h")
    v = verdict(write_policy(root), "write", p(root / "h" / "pre-commit"))
    assert (v.action, v.rule) == ("hold", "write-pattern")
    target = os.path.realpath(root / ".git" / "hooks" / "pre-commit")
    assert v.reason == f'argument "path": {target} matches .git/hooks/**'


def test_dangling_link_resolves_to_target(root, tmp_path):
    symlink(tmp_path / "nowhere" / "file", root / "dangling")
    v = verdict(write_policy(root), "write", p(root / "dangling"))
    assert (v.action, v.rule) == ("hold", "outside-roots")
    assert v.reason == (
        f'argument "path": {os.path.join(os.path.realpath(tmp_path), "nowhere", "file")} '
        "is outside every workspace root"
    )
    symlink(root / "later", root / "inside")
    assert verdict(write_policy(root), "write", p(root / "inside")).rule == "inside-roots"


def test_link_loop_is_held(root):
    symlink(root / "b", root / "a")
    symlink(root / "a", root / "b")
    v = verdict(write_policy(root), "write", p(root / "a" / "x"))
    assert (v.rule, v.reason) == ("path-unresolvable", 'argument "path": too many symbolic links')


@POSIX
def test_unreadable_part_is_held(root):
    locked = root / "locked"
    (locked / "inner").mkdir(parents=True)
    os.chmod(locked, 0o000)
    try:
        if os.access(locked, os.X_OK):
            pytest.skip("this user can search a directory with no permissions (root)")
        v = verdict(write_policy(root), "write", p(locked / "inner" / "x"))
    finally:
        os.chmod(locked, 0o700)
    assert v.rule == "path-unresolvable"
    assert v.reason == 'argument "path": cannot be examined: Permission denied'


def test_relative_tilde_nul_are_held(root):
    cases = [
        ("a/b", "not an absolute path"),
        ("./a", "not an absolute path"),
        ("~/x", "starts with ~, which the server may expand"),
        (p(root) + "/a" + chr(0) + "b", "contains a NUL character"),
    ]
    for value, reason in cases:
        v = verdict(write_policy(root), "write", value)
        assert (v.action, v.rule, v.reason) == (
            "hold",
            "path-unresolvable",
            f'argument "path": {reason}',
        ), value
        assert verdict(write_policy(root), "read", value).rule == "path-unresolvable"


def test_slow_resolution_is_held(root):
    def slow(path):
        time.sleep(1.0)
        return path

    started = time.monotonic()
    v = verdict(write_policy(root, resolver=slow, resolve_bound=0.05), "write", p(root / "x"))
    assert time.monotonic() - started < 1.0  # the bound, not the resolver, ended it
    assert (v.rule, v.reason) == (
        "path-unresolvable",
        'argument "path": took longer than 2 s to resolve',
    )


def test_path_arg_not_a_string_is_held(root):
    pol = write_policy(root)
    for value in (5, None, {"p": "/x"}, ["/a", 5]):
        v = verdict(pol, "write", value)
        assert (v.rule, v.reason) == (
            "path-not-string",
            'argument "path" is not a string or a list of strings',
        ), value
    for arguments in ({}, None, [1]):
        v = policy.evaluate(pol, "fs", "write", arguments, {})
        assert (v.rule, v.reason) == ("path-missing", 'argument "path" is missing')
    assert verdict(pol, "write", []).rule == "inside-roots"


def test_more_than_256_paths_is_held(root):
    pol = write_policy(root)
    assert verdict(pol, "write", [p(root / f"f{i}") for i in range(256)]).action == "allow"
    v = verdict(pol, "write", [p(root / f"f{i}") for i in range(257)])
    assert (v.rule, v.reason) == ("path-unresolvable", 'argument "path": more than 256 paths')


HOME = (None, ("home", "me"))
PATTERN_CASES = [
    # (pattern, path, exact match, folded match)
    ("/etc/**", "/etc/passwd", True, True),
    ("/etc/**", "/etc", True, True),
    ("/etc/**", "/x/etc/passwd", False, False),
    ("~/.ssh/**", "/home/me/.ssh/id_ed25519", True, True),
    ("~/.ssh/**", "/home/other/.ssh/id", False, False),
    (".git/hooks/**", "/x/y/.git/hooks/pre-commit", True, True),
    (".git/hooks/**", "/x/y/.git/hooks", True, True),
    (".git/hooks/**", "/x/y/.git/hooksx", False, False),
    (".env*", "/x/.env.local", True, True),
    (".env*", "/x/.env", True, True),
    (".env*", "/x/my.env", False, False),
    ("a/*.txt", "/q/a/b.txt", True, True),
    ("a/*.txt", "/q/a/b/c.txt", False, False),
    ("a/?.txt", "/q/a/b.txt", True, True),
    ("a/?.txt", "/q/a/bb.txt", False, False),
    ("a/**/z", "/a/z", True, True),
    ("a/**/z", "/q/a/b/c/z", True, True),
    ("a/[b]", "/a/[b]", True, True),
    ("a/[b]", "/a/b", False, False),
    (".GIT/hooks/**", "/r/.git/hooks/x", False, True),
    ("caf" + chr(0xE9), "/x/cafe" + chr(0x301), False, True),
    ("STRASSE", "/x/stra" + chr(0xDF) + "e", False, True),
]


@pytest.mark.parametrize("pattern, path, exact, folded", PATTERN_CASES)
def test_pattern_matching(pattern, path, exact, folded, monkeypatch):
    monkeypatch.setattr(paths, "WINDOWS", False)
    assert paths.pattern_problem(pattern) is None
    compiled = paths.compile_pattern(pattern, HOME)
    assert paths.matches(compiled, path, fold=False) is exact
    assert paths.matches(compiled, path, fold=True) is folded


def test_case_rule_on_this_platform(root):
    """A write to R/.GIT/hooks/x inside a root: held on Windows and macOS (casefold), runs on
    Linux, where .GIT is another directory."""
    (root / ".GIT" / "hooks").mkdir(parents=True)
    v = verdict(write_policy(root), "write", p(root / ".GIT" / "hooks" / "x"))
    if sys.platform in ("win32", "darwin"):
        assert v.rule == "write-pattern"
    else:
        assert (v.action, v.rule) == ("allow", "inside-roots")


def test_roots_compare_like_forbidden_paths(tmp_path):
    proj = tmp_path / "proj"
    proj2 = tmp_path / "proj2"
    proj.mkdir()
    proj2.mkdir()
    v = verdict(write_policy(proj), "write", p(proj2 / "x"))
    assert v.rule == "outside-roots"
    assert verdict(write_policy(proj), "write", p(proj / "x")).rule == "inside-roots"
    cased = Path(str(proj).upper()) if sys.platform == "win32" else proj.parent / "PROJ"
    v = verdict(write_policy(proj), "write", p(cased / "x"))
    case_insensitive = sys.platform == "win32" or (
        sys.platform == "darwin" and (proj.parent / "PROJ").exists()
    )
    assert v.rule == ("inside-roots" if case_insensitive else "outside-roots")


@WINDOWS_ONLY
def test_windows_names_are_held(root):
    pol = write_policy(root)
    base = str(root)
    for value in (
        base + "\\file.txt:stream",
        base + "\\name.",
        base + "\\name ",
        base + "\\CON",
        base + "\\nul.txt",
        "\\\\?\\" + base + "\\x",
        "\\\\.\\" + base + "\\x",
    ):
        v = verdict(pol, "write", value)
        assert v.reason == 'argument "path": a Windows name Polarizer does not resolve', value
    for value in ("C:a", "\\a"):
        v = verdict(pol, "write", value)
        assert v.reason == 'argument "path": not an absolute path', value


def test_polarizer_files_are_held(root, tmp_path):
    ledger = tmp_path / "ledger"
    ledger.mkdir()
    cfg = root / "polarizer.toml"
    cfg.write_text("", encoding="utf-8")
    pol = write_policy(root, ledger_dir=os.path.realpath(ledger), config_path=os.path.realpath(cfg))
    real_ledger = os.path.realpath(ledger)
    for tool in ("write", "read"):
        v = verdict(pol, tool, p(ledger / "args" / "x.bin"))
        assert (v.rule, v.reason) == (
            "polarizer-files",
            f'argument "path": {real_ledger}{os.sep}args{os.sep}x.bin '
            "is inside Polarizer's ledger directory",
        )
    v = verdict(pol, "web", p(ledger / "ledger.jsonl"))
    assert v.rule == "polarizer-files"
    v = verdict(pol, "write", p(cfg))
    assert (v.rule, v.reason) == (
        "polarizer-files",
        f'argument "path": {os.path.realpath(cfg)} is Polarizer\'s config file',
    )
    assert verdict(pol, "read", p(cfg)).rule == "local-read"  # reading the config is fine
    symlink(ledger, root / "via")
    assert verdict(pol, "read", p(root / "via" / "ledger.jsonl")).rule == "polarizer-files"
    symlink(cfg, root / "cfg-link")
    assert verdict(pol, "write", p(root / "cfg-link")).rule == "polarizer-files"
