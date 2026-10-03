"""polarizer.toml: parse and validate only (docs/PROXY-SPEC.md, Configuration).

Nothing here starts or connects to an upstream. Every error is one line, starting with the
file's name, and the command that read the file exits 2.
"""

import hashlib
import math
import os
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

from polarizer.ledgerdir import always_protected, default_ledger_dir

PREFIX = re.compile(r"(?!_)(?!.*__)[A-Za-z0-9_-]{1,32}(?<!_)")
WHOLE_VAR = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
TOP_KEYS = ("ledger_dir", "protected_paths", "upstream")
UPSTREAM_KEYS = ("command", "args", "env", "connect_timeout_seconds")
_TOML_WHERE = re.compile(r"^(.*) \(at line (\d+), column \d+\)$", re.S)


class ConfigError(Exception):
    """One line for stderr; exit 2."""


@dataclass(frozen=True)
class Upstream:
    prefix: str
    command: str
    args: tuple[str, ...]
    env: dict  # name -> value; "${NAME}" values are expanded only when require_env is set
    connect_timeout_seconds: float


@dataclass(frozen=True)
class Config:
    path: Path
    sha256: str
    ledger_dir: Path
    protected_paths: tuple[Path, ...]  # always includes Parallax's two directories
    upstreams: tuple[Upstream, ...]


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

    protected = list(always_protected())
    if "protected_paths" in doc:
        given = doc["protected_paths"]
        if not isinstance(given, list) or not all(isinstance(p, str) for p in given):
            fail("protected_paths must be a list of absolute paths")
        for p in given:
            expanded = Path(os.path.expanduser(p))
            if not expanded.is_absolute():
                fail("protected_paths must be a list of absolute paths")
            if expanded not in protected:
                protected.append(expanded)

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
        protected_paths=tuple(protected),
        upstreams=tuple(upstreams),
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
    return Upstream(prefix, command, tuple(args), resolved, float(timeout))
