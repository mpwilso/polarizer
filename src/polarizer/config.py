"""polarizer.toml: parse and validate only (docs/PROXY-SPEC.md, Configuration, and
docs/HOLD-SPEC.md, section 2, for the policy).

Nothing here starts or connects to an upstream, and nothing checks that a workspace root exists:
only serve does that, when it builds its policy (polarizer.policy). Every error is one line,
starting with the file's name, and the command that read the file exits 2. Text from the file in
the policy's error lines goes through polarizer.text.safe.
"""

import hashlib
import math
import os
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from polarizer.argrules import domain_problem
from polarizer.ledgerdir import always_forbidden, default_ledger_dir
from polarizer.paths import pattern_problem
from polarizer.text import safe

PREFIX = re.compile(r"(?!_)(?!.*__)[A-Za-z0-9_-]{1,32}(?<!_)")
WHOLE_VAR = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
TOP_KEYS = ("ledger_dir", "ledger_forbidden_paths", "upstream", "policy")
UPSTREAM_KEYS = (
    "command",
    "args",
    "env",
    "connect_timeout_seconds",
    "trust_annotations",
    "tools",
    "meta_allowlist",
)
MAX_META_KEYS = 32  # meta_allowlist entries per upstream (docs/SCAN-SPEC.md, section 8)
POLICY_KEYS = (
    "workspace_roots",
    "hold_timeout_seconds",
    "write_hold_patterns",
    "read_hold_patterns",
    "planted_calls_per_session",
)
MAX_PLANTED = 3
TOOL_KEYS = ("class", "path_args", "url_args", "domains", "deny_values")
CLASSES = ("local-read", "local-write", "destructive", "open-world", "egress")
EXPOSED_NAME = re.compile(r"[A-Za-z0-9._-]{1,128}")
MAX_ROOTS = 32
MAX_PATTERNS = 64
MAX_PATH_ARGS = 16
MAX_DOMAINS = 64  # docs/TAINT-SPEC.md, section 2
MAX_DENY_ARGS = 16
MAX_DENY_PATTERNS = 32
MAX_PATTERN = 1024
_TOML_WHERE = re.compile(r"^(.*) \(at line (\d+), column \d+\)$", re.S)


class ConfigError(Exception):
    """One line for stderr; exit 2."""


@dataclass(frozen=True)
class ToolRule:
    """One [upstream.<p>.tools] entry: the tool's class, the arguments that hold paths, and M2b's
    argument rules (docs/TAINT-SPEC.md, section 2): the arguments that hold URLs, the hosts they
    may name (lowercased), and deny_values as (argument name, patterns) pairs in config order."""

    cls: str
    path_args: tuple[str, ...] = ()
    url_args: tuple[str, ...] = ()
    domains: tuple[str, ...] = ()
    deny_values: tuple[tuple[str, tuple[str, ...]], ...] = ()


@dataclass(frozen=True)
class Upstream:
    prefix: str
    command: str
    args: tuple[str, ...]
    env: dict  # name -> value; "${NAME}" values are expanded only when require_env is set
    connect_timeout_seconds: float
    trust_annotations: bool = False
    tools: dict = field(default_factory=dict)  # upstream tool name -> ToolRule
    meta_allowlist: tuple[str, ...] = ()  # the tools' _meta keys served (SCAN-SPEC.md, 8)


@dataclass(frozen=True)
class PolicyConfig:
    """[policy] as written: roots with "~" expanded but not resolved, patterns as configured."""

    workspace_roots: tuple[Path, ...] = ()
    hold_timeout_seconds: int = 300
    write_hold_patterns: tuple[str, ...] = ()
    read_hold_patterns: tuple[str, ...] = ()
    planted_calls_per_session: int = 0  # live planted calls (MEASURE-SPEC.md, section 11)


@dataclass(frozen=True)
class Config:
    path: Path
    sha256: str
    ledger_dir: Path
    ledger_forbidden_paths: tuple[Path, ...]  # always includes Parallax's two directories
    upstreams: tuple[Upstream, ...]
    policy: PolicyConfig = PolicyConfig()


def load(path: Path, *, require_env: bool = True, environ: dict | None = None) -> Config:
    """Read and validate a config file. require_env=False is for verify and repair, which
    don't need upstream secrets: "${NAME}" values are then checked for form but not looked up."""
    path = Path(path)
    name = path.name
    environ = os.environ if environ is None else environ

    def fail(message: str):
        raise ConfigError(f"{name}: {message}")

    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        fail(f"not found at {path}")
    except OSError as e:
        fail(f"cannot read {path}: {e.strerror or e}")
    try:
        doc = tomllib.loads(raw.decode("utf-8"))
    except UnicodeDecodeError:
        fail("not valid UTF-8")
    except tomllib.TOMLDecodeError as e:
        where = _TOML_WHERE.match(str(e))
        fail(f"line {where.group(2)}: {where.group(1)}" if where else str(e))

    for key in doc:
        if key not in TOP_KEYS:
            fail(f'unknown top-level key "{key}"')

    ledger_dir = default_ledger_dir()
    if "ledger_dir" in doc:
        if not isinstance(doc["ledger_dir"], str):
            fail("ledger_dir must be a string")
        ledger_dir = Path(os.path.expanduser(doc["ledger_dir"]))
        if not ledger_dir.is_absolute():
            fail(f"ledger_dir must be an absolute path, got {doc['ledger_dir']}")

    forbidden = list(always_forbidden())
    if "ledger_forbidden_paths" in doc:
        given = doc["ledger_forbidden_paths"]
        if not isinstance(given, list) or not all(isinstance(p, str) for p in given):
            fail("ledger_forbidden_paths must be a list of absolute paths")
        for p in given:
            expanded = Path(os.path.expanduser(p))
            if not expanded.is_absolute():
                fail("ledger_forbidden_paths must be a list of absolute paths")
            if expanded not in forbidden:
                forbidden.append(expanded)

    tables = doc.get("upstream", {})
    if not isinstance(tables, dict):
        fail('"upstream" must be a table of [upstream.<prefix>] tables')
    if not tables:
        fail("no upstreams configured")
    upstreams = [
        _upstream(prefix, table, fail, require_env, environ) for prefix, table in tables.items()
    ]
    return Config(
        path=path,
        sha256=hashlib.sha256(raw).hexdigest(),
        ledger_dir=ledger_dir,
        ledger_forbidden_paths=tuple(forbidden),
        upstreams=tuple(upstreams),
        policy=_policy(doc, fail),
    )


def _policy(doc: dict, fail) -> PolicyConfig:
    """The [policy] table (HOLD-SPEC.md, section 2). With none, every default applies."""
    if "policy" not in doc:
        return PolicyConfig()
    table = doc["policy"]
    if not isinstance(table, dict):
        fail('"policy" must be a table')
    for key in table:
        if key not in POLICY_KEYS:
            fail(f'[policy] unknown key "{safe(key)}"')
    roots_given = table.get("workspace_roots", [])
    if not isinstance(roots_given, list) or not all(isinstance(r, str) for r in roots_given):
        fail("[policy] workspace_roots must be a list of absolute paths")
    if len(roots_given) > MAX_ROOTS:
        fail(f"[policy] workspace_roots has more than {MAX_ROOTS} entries")
    roots = tuple(Path(os.path.expanduser(r)) for r in roots_given)
    if not all(r.is_absolute() for r in roots):
        fail("[policy] workspace_roots must be a list of absolute paths")
    timeout = table.get("hold_timeout_seconds", 300)
    if type(timeout) is not int or not 1 <= timeout <= 1200:
        fail("[policy] hold_timeout_seconds must be an integer from 1 to 1200")
    lists = {}
    for key in ("write_hold_patterns", "read_hold_patterns"):
        given = table.get(key, [])
        if (
            not isinstance(given, list)
            or len(given) > MAX_PATTERNS
            or not all(isinstance(p, str) for p in given)
        ):
            fail(f"[policy] {key} must be a list of at most {MAX_PATTERNS} patterns")
        for pattern in given:
            problem = pattern_problem(pattern)
            if problem:
                fail(f'[policy] pattern "{safe(pattern)}" is not valid: {problem}')
        lists[key] = tuple(given)
    planted = table.get("planted_calls_per_session", 0)
    if type(planted) is not int or not 0 <= planted <= MAX_PLANTED:
        fail(f"[policy] planted_calls_per_session must be an integer from 0 to {MAX_PLANTED}")
    return PolicyConfig(
        roots, timeout, lists["write_hold_patterns"], lists["read_hold_patterns"], planted
    )


def _upstream(prefix, table, fail, require_env, environ) -> Upstream:
    if not PREFIX.fullmatch(prefix):
        fail(
            f'upstream prefix "{prefix}" must be 1 to 32 characters of letters, digits, "-" '
            'and "_", with no "__" and no "_" at either end'
        )
    here = f"[upstream.{prefix}]"
    if not isinstance(table, dict):
        fail(f"{here} must be a table")
    for key in table:
        if key not in UPSTREAM_KEYS:
            fail(f'{here} unknown key "{key}"')
    if "command" not in table:
        fail(f'{here} is missing "command"')
    command = table["command"]
    if not isinstance(command, str):
        fail(f'{here} "command" must be a string')
    args = table.get("args", [])
    if not isinstance(args, list) or not all(isinstance(a, str) for a in args):
        fail(f'{here} "args" must be a list of strings')
    env = table.get("env", {})
    if not isinstance(env, dict) or not all(isinstance(v, str) for v in env.values()):
        fail(f'{here} "env" must be a table of strings')
    resolved = {}
    for key, value in env.items():
        whole = WHOLE_VAR.fullmatch(value)
        if whole:
            if require_env:
                if whole.group(1) not in environ:
                    fail(f'{here} env {key}: "{value}" is not set in Polarizer\'s environment')
                value = environ[whole.group(1)]
        elif "${" in value:
            fail(f'{here} env {key}: only a whole "${{NAME}}" value is expanded')
        resolved[key] = value
    timeout = table.get("connect_timeout_seconds", 10)
    number = isinstance(timeout, int | float) and not isinstance(timeout, bool)
    if not number or math.isnan(timeout) or not 1 <= timeout <= 20:
        fail(f'{here} "connect_timeout_seconds" must be a number from 1 to 20')
    trust = table.get("trust_annotations", False)
    if not isinstance(trust, bool):
        fail(f'{here} "trust_annotations" must be true or false')
    tools = _tools(prefix, table.get("tools", {}), fail)
    meta = table.get("meta_allowlist", [])
    if (
        not isinstance(meta, list)
        or len(meta) > MAX_META_KEYS
        or not all(isinstance(k, str) and 1 <= len(k) <= 256 for k in meta)
    ):
        fail(
            f'{here} "meta_allowlist" must be a list of at most {MAX_META_KEYS} strings of 1 to '
            "256 characters"
        )
    return Upstream(
        prefix, command, tuple(args), resolved, float(timeout), trust, tools, tuple(meta)
    )


def _tools(prefix: str, given, fail) -> dict:
    """[upstream.<p>.tools]: each upstream tool name with its class and path arguments. A tool
    the upstream doesn't list is not an error; it has no effect until listed."""
    where = f"[upstream.{prefix}.tools]"
    if not isinstance(given, dict):
        fail(f'[upstream.{prefix}] "tools" must be a table of {where} entries')
    tools = {}
    for name, entry in given.items():
        shown = safe(name)
        if not EXPOSED_NAME.fullmatch(f"{prefix}__{name}"):
            fail(f'{where} "{shown}" is not a tool name Polarizer can expose')
        if not isinstance(entry, dict):
            fail(f'{where} "{shown}" must be a table')
        for key in entry:
            if key not in TOOL_KEYS:
                fail(f'{where} "{shown}" unknown key "{safe(key)}"')
        if "class" not in entry:
            fail(f'{where} "{shown}" is missing "class"')
        if entry["class"] not in CLASSES:
            fail(f'{where} "{shown}" class must be one of {", ".join(CLASSES)}')
        path_args = entry.get("path_args", [])
        if not _names(path_args):
            fail(
                f'{where} "{shown}" path_args must be a list of at most {MAX_PATH_ARGS} '
                "argument names"
            )
        url_args, domains = _domains(entry, f'{where} "{shown}"', fail)
        deny = _deny_values(entry.get("deny_values", {}), f'{where} "{shown}"', fail)
        tools[name] = ToolRule(entry["class"], tuple(path_args), url_args, domains, deny)
    return tools


def _names(given) -> bool:
    """A list of at most 16 argument names, each 1 to 128 characters (path_args, url_args)."""
    return (
        isinstance(given, list)
        and len(given) <= MAX_PATH_ARGS
        and all(isinstance(a, str) and 1 <= len(a) <= 128 for a in given)
    )


def _domains(entry: dict, here: str, fail) -> tuple[tuple, tuple]:
    """url_args and domains (docs/TAINT-SPEC.md, section 2): their form, each entry, that they
    go together, and only with class egress. Entries are returned lowercased."""
    url_args = entry.get("url_args", [])
    if not _names(url_args):
        fail(f"{here} url_args must be a list of at most {MAX_PATH_ARGS} argument names")
    domains = entry.get("domains", [])
    if (
        not isinstance(domains, list)
        or len(domains) > MAX_DOMAINS
        or not all(isinstance(d, str) for d in domains)
    ):
        fail(f"{here} domains must be a list of at most {MAX_DOMAINS} host names")
    for domain in domains:
        problem = domain_problem(domain)
        if problem:
            fail(f'{here} domains entry "{safe(domain)}" is not a host name: {problem}')
    if bool(url_args) != bool(domains):
        fail(f"{here} domains and url_args go together")
    if domains and entry["class"] != "egress":
        fail(f"{here} domains and url_args go only with class egress")
    return tuple(url_args), tuple(d.lower() for d in domains)


def _deny_values(given, here: str, fail) -> tuple:
    """deny_values (docs/TAINT-SPEC.md, section 2): a table of at most 16 argument names, each
    with 1 to 32 patterns of 1 to 1024 characters that compile as Python regular expressions."""
    if (
        not isinstance(given, dict)
        or len(given) > MAX_DENY_ARGS
        or not all(1 <= len(a) <= 128 for a in given)
    ):
        fail(
            f"{here} deny_values must be a table of at most {MAX_DENY_ARGS} argument names, "
            "each with a list of patterns"
        )
    out = []
    for arg, patterns in given.items():
        if (
            not isinstance(patterns, list)
            or not 1 <= len(patterns) <= MAX_DENY_PATTERNS
            or not all(isinstance(p, str) and 1 <= len(p) <= MAX_PATTERN for p in patterns)
        ):
            fail(
                f'{here} deny_values "{safe(arg)}" must be a list of 1 to {MAX_DENY_PATTERNS} '
                f"patterns of 1 to {MAX_PATTERN} characters"
            )
        for pattern in patterns:
            try:
                re.compile(pattern)
            except re.error as e:
                fail(
                    f'{here} deny_values "{safe(arg)}" pattern "{safe(pattern)}" is not a valid '
                    f"regular expression: {safe(str(e))}"
                )
        out.append((arg, tuple(patterns)))
    return tuple(out)
