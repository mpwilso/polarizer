"""The policy serve runs with, and the rule function (docs/HOLD-SPEC.md, sections 2 and 4).

`evaluate` decides, for a call that routes to an approved tool, whether it runs at once or is
held for a person. It is pure apart from path resolution, which only reads the file system and
is bounded per path (polarizer.paths). The gateway runs it on a worker thread.

The tiers are deny, then hold, then allow. M2a has no deny rule; the cap on open holds acts
like one and lives in the gateway, after this function.
"""

import hashlib
import os
from dataclasses import dataclass, field
from pathlib import Path

import rfc8785

from polarizer import paths
from polarizer.config import CLASSES, Config, ConfigError, ToolRule
from polarizer.ledgerdir import is_inside
from polarizer.text import safe
from polarizer.upstream import clip

POLICY_PREFIX = b"POLARIZER-POLICY/1\n"
MAX_PATHS = 256  # a list argument with more paths than this is held
REASON_LIMIT = 1024
ANNOTATIONS_SUFFIX = " (class from annotations)"
RANK = {"local-read": 0, "open-world": 0, "local-write": 1, "destructive": 2, "egress": 2}

# The allow-tier rules, which call.sent records as allowed_by.
HOLDS_OFF = "holds-off"
INSIDE_ROOTS = "inside-roots"
LOCAL_READ = "local-read"
OPEN_WORLD = "open-world"


@dataclass(frozen=True)
class Verdict:
    action: str  # "allow" or "hold"
    rule: str
    reason: str | None  # None for every allow
    cls: str | None
    class_from: str | None  # "config", "annotations" or None


@dataclass(frozen=True)
class UpstreamPolicy:
    trust_annotations: bool = False
    tools: dict = field(default_factory=dict)  # upstream tool name -> config.ToolRule


@dataclass
class Policy:
    """A parsed policy with its roots resolved and its patterns compiled.

    `resolver` and `resolve_bound` are the path resolution and its bound per path; tests
    replace them. `fold` is the case rule for patterns (casefold and NFC on Windows and macOS).
    """

    holds: bool = True
    workspace_roots: tuple[str, ...] = ()  # resolved
    hold_timeout_seconds: int = 300
    write_hold_patterns: tuple[str, ...] = ()  # as configured
    read_hold_patterns: tuple[str, ...] = ()
    upstreams: dict = field(default_factory=dict)  # prefix -> UpstreamPolicy
    ledger_dir: str | None = None  # resolved
    config_path: str | None = None  # resolved
    fold: bool = paths.FOLD
    resolver: object = paths.resolve
    resolve_bound: float = paths.RESOLVE_BOUND
    stuck: paths.Stuck = paths.STUCK  # resolutions past their bound; one count per process
    home: tuple | None = None  # (drive, parts) to expand "~" with; None: the real home
    write_patterns: tuple = ()  # compiled: the built-in ones, then the configured ones
    read_patterns: tuple = ()

    def __post_init__(self):
        compile_ = lambda texts: tuple(paths.compile_pattern(t, self.home) for t in texts)  # noqa: E731
        self.write_patterns = compile_((*paths.BUILTIN_WRITE, *self.write_hold_patterns))
        self.read_patterns = compile_((*paths.BUILTIN_READ, *self.read_hold_patterns))

    @property
    def classified(self) -> int:
        """The number of configured tool entries, over every upstream."""
        return sum(len(u.tools) for u in self.upstreams.values())

    def form(self) -> dict:
        upstreams = {
            prefix: {
                "tools": {
                    name: {"class": rule.cls, "path_args": list(rule.path_args)}
                    for name, rule in u.tools.items()
                },
                "trust_annotations": u.trust_annotations,
            }
            for prefix, u in self.upstreams.items()
        }
        return {
            "builtin_patterns": paths.BUILTIN_PATTERNS,
            "hold_timeout_seconds": self.hold_timeout_seconds,
            "read_hold_patterns": list(self.read_hold_patterns),
            "upstreams": upstreams,
            "workspace_roots": list(self.workspace_roots),
            "write_hold_patterns": list(self.write_hold_patterns),
        }

    @property
    def sha256(self) -> str:
        return hashlib.sha256(POLICY_PREFIX + rfc8785.dumps(self.form())).hexdigest()

    def loaded(self, session: str) -> dict:
        """The data of policy.loaded for a serve process."""
        return {
            "session": session,
            "holds": "on" if self.holds else "off",
            "policy_sha256": self.sha256,
            "classified": self.classified,
            "workspace_roots": list(self.workspace_roots),
            "hold_timeout_seconds": self.hold_timeout_seconds,
        }

    def rule_for(self, prefix: str, tool: str) -> ToolRule | None:
        upstream = self.upstreams.get(prefix)
        return upstream.tools.get(tool) if upstream else None

    def has_class(self, prefix: str, tool: str) -> bool:
        """Whether a listed tool gets a class: configured, or from a trusted upstream's
        annotations (every annotation set suggests one)."""
        upstream = self.upstreams.get(prefix)
        return upstream is not None and (tool in upstream.tools or upstream.trust_annotations)

    def class_from_annotations(self, prefix: str, tool: str) -> bool:
        """Whether a listed tool takes its class from annotations: no configured entry, on an
        upstream with trust_annotations (HOLD-SPEC.md, section 2)."""
        upstream = self.upstreams.get(prefix)
        return upstream is not None and upstream.trust_annotations and tool not in upstream.tools


def build(cfg: Config, *, holds: bool = True) -> Policy:
    """serve's policy from a parsed config: each workspace root resolved with realpath, and
    refused if it doesn't exist (ConfigError, exit 2), "~" in patterns expanded once."""
    roots = []
    for root in cfg.policy.workspace_roots:
        if not os.path.exists(root):
            raise ConfigError(
                f"{cfg.path.name}: [policy] workspace root {safe(root)} does not exist"
            )
        roots.append(os.path.realpath(root))
    return Policy(
        holds=holds,
        workspace_roots=tuple(roots),
        hold_timeout_seconds=cfg.policy.hold_timeout_seconds,
        write_hold_patterns=cfg.policy.write_hold_patterns,
        read_hold_patterns=cfg.policy.read_hold_patterns,
        upstreams={
            u.prefix: UpstreamPolicy(u.trust_annotations, dict(u.tools)) for u in cfg.upstreams
        },
        ledger_dir=os.path.realpath(cfg.ledger_dir),
        config_path=os.path.realpath(cfg.path),
    )


# Annotations (section 4) -------------------------------------------------------------------


def _hint(annotations: dict, key: str, default: bool) -> bool:
    value = annotations.get(key)
    return value if isinstance(value, bool) else default


def suggested_class(annotations) -> str:
    """The class a definition's annotations suggest, with MCP's defaults for an absent hint
    (readOnlyHint false, destructiveHint true, openWorldHint true). A hint that isn't a boolean
    counts as absent, so a tool with no annotations suggests destructive."""
    a = annotations if isinstance(annotations, dict) else {}
    read_only = _hint(a, "readOnlyHint", False)
    destructive = _hint(a, "destructiveHint", True)
    open_world = _hint(a, "openWorldHint", True)
    if read_only:
        return "open-world" if open_world else "local-read"
    if destructive:
        return "destructive"
    return "egress" if open_world else "local-write"


def contradicts(configured: str, suggested: str) -> bool:
    """A configured class that holds less than the annotations suggest."""
    return RANK[suggested] > RANK[configured]


# The rule function -------------------------------------------------------------------------


def _hold(rule: str, reason: str, cls, class_from) -> Verdict:
    if class_from == "annotations":
        reason += ANNOTATIONS_SUFFIX
    return Verdict("hold", rule, clip(reason, REASON_LIMIT), cls, class_from)


def _arg(name: str) -> str:
    return f'argument "{safe(name, REASON_LIMIT)}"'


def _path_hold(policy: Policy, cls: str, arg: str, value: str) -> tuple[str, str] | None:
    """(rule, reason) if this one path holds the call, in section 4's order."""
    try:
        resolved = paths.resolve_bounded(value, policy.resolve_bound, policy.resolver, policy.stuck)
    except paths.Unresolvable as e:
        return "path-unresolvable", f"{_arg(arg)}: {e}"
    shown = safe(resolved, REASON_LIMIT)
    if policy.ledger_dir is not None and is_inside(Path(resolved), Path(policy.ledger_dir)):
        return "polarizer-files", f"{_arg(arg)}: {shown} is inside Polarizer's ledger directory"
    if cls == "local-write" and policy.config_path is not None and _same(resolved, policy):
        return "polarizer-files", f"{_arg(arg)}: {shown} is Polarizer's config file"
    patterns = policy.write_patterns if cls == "local-write" else policy.read_patterns
    for pattern in patterns:
        if paths.matches(pattern, resolved, policy.fold):
            rule = "write-pattern" if cls == "local-write" else "read-pattern"
            return rule, f"{_arg(arg)}: {shown} matches {safe(pattern.text, REASON_LIMIT)}"
    if cls == "local-write":
        roots = policy.workspace_roots
        if not any(is_inside(Path(resolved), Path(root)) for root in roots):
            return "outside-roots", f"{_arg(arg)}: {shown} is outside every workspace root"
    return None


def _same(resolved: str, policy: Policy) -> bool:
    if os.path.normcase(resolved) == os.path.normcase(policy.config_path):
        return True
    try:
        return os.path.samefile(resolved, policy.config_path)
    except (OSError, ValueError):
        return False


def _paths_hold(policy: Policy, cls: str, path_args, arguments) -> tuple[str, str] | None:
    """(rule, reason) of the first path rule that holds the call, judging each configured
    argument in order, and each of its paths in order, as class `cls`; None if none does."""
    for arg in path_args:
        if not isinstance(arguments, dict) or arg not in arguments:
            return "path-missing", f"{_arg(arg)} is missing"
        value = arguments[arg]
        if isinstance(value, str):
            values = [value]
        elif isinstance(value, list) and all(isinstance(v, str) for v in value):
            values = value
        else:
            return "path-not-string", f"{_arg(arg)} is not a string or a list of strings"
        if len(values) > MAX_PATHS:
            return "path-unresolvable", f"{_arg(arg)}: more than {MAX_PATHS} paths"
        for one in values:
            held = _path_hold(policy, cls, arg, one)
            if held:
                return held
    return None


def evaluate(policy: Policy, prefix: str, tool: str, arguments, definition) -> Verdict:
    """Section 4: allow or hold one call to an approved tool. `definition` is the approved
    stored copy (a dict), whose annotations count only for a trusted upstream."""
    if not policy.holds:
        return Verdict("allow", HOLDS_OFF, None, None, None)
    rule = policy.rule_for(prefix, tool)
    upstream = policy.upstreams.get(prefix)
    path_args: tuple[str, ...] = ()
    if rule is not None:
        cls, class_from, path_args = rule.cls, "config", rule.path_args
    elif upstream is not None and upstream.trust_annotations:
        annotations = definition.get("annotations") if isinstance(definition, dict) else None
        cls, class_from = suggested_class(annotations), "annotations"
    else:
        cls, class_from = None, None
    if cls is None:
        return _hold("unclassified", f"{prefix}__{tool} has no class in polarizer.toml", None, None)
    assert cls in CLASSES
    if cls in ("destructive", "egress"):
        # Held on every call; the paths only add to the reason (rows 3 and 4): a destructive
        # tool's are judged as local-write's, an egress tool's as local-read's.
        reason = f"class {cls} is held on every call"
        as_cls = "local-write" if cls == "destructive" else "local-read"
        held = _paths_hold(policy, as_cls, path_args, arguments)
        if held:
            reason += f"; {held[1]}"
        return _hold(cls, reason, cls, class_from)
    if cls == "local-write" and not path_args:
        reason = "class local-write has no path_args, so its paths cannot be checked"
        return _hold("write-unchecked", reason, cls, class_from)
    held = _paths_hold(policy, cls, path_args, arguments)
    if held:
        return _hold(*held, cls, class_from)
    allow = {"local-write": INSIDE_ROOTS, "local-read": LOCAL_READ, "open-world": OPEN_WORLD}
    return Verdict("allow", allow[cls], None, cls, class_from)


# Classes in `pending` and `approve` with --config (section 8) -------------------------------


def for_display(cfg: Config) -> Policy:
    """The classes of a parsed config, for `pending` and `approve`: no root is resolved or
    checked, and nothing is evaluated."""
    upstreams = {
        u.prefix: UpstreamPolicy(u.trust_annotations, dict(u.tools)) for u in cfg.upstreams
    }
    return Policy(upstreams=upstreams)


def _annotations(copy) -> dict | None:
    value = copy.get("annotations") if isinstance(copy, dict) else None
    return value if isinstance(value, dict) else None


def class_line(policy: Policy, prefix: str, tool: str, copy: dict) -> str:
    """The line after a `new` or `changed` block's header in `pending`, and after the printed
    definition in `approve`: the tool's class and what the stored copy's annotations suggest.
    A configured class that holds less than the suggestion says "but"."""
    suggested = suggested_class(_annotations(copy))
    rule = policy.rule_for(prefix, tool)
    if rule is not None:
        if contradicts(rule.cls, suggested):
            return f"class {rule.cls}, but annotations suggest {suggested}"
        return f"class {rule.cls}; annotations suggest {suggested}"
    if policy.class_from_annotations(prefix, tool):
        return f"class {suggested} (from annotations); annotations suggest {suggested}"
    return f"class none: every call is held; annotations suggest {suggested}"


def classes_section(policy: Policy, pin_state, ledger_dir: Path, upstream=None) -> list[str]:
    """`pending`'s classes section: every tool of a configured upstream whose latest decision
    approves a hash with no drift since, and which has no class or whose class contradicts its
    annotations, by prefix and tool name. [] when there is none. A tool whose approved copy
    fails its check is left out: it can't be served, and its annotations can't be read."""
    from polarizer import defhash
    from polarizer.text import printable

    lines, unclassified, contradicted = [], 0, 0
    for prefix, tool in pin_state.keys():
        if upstream is not None and prefix != upstream or prefix not in policy.upstreams:
            continue
        tp = pin_state.get(prefix, tool)
        if tp.decision != "approved" or tp.drifted:
            continue
        try:
            copy = defhash.read_copy(ledger_dir, tp.decided_hash)
        except defhash.CopyProblem:
            continue
        suggested = suggested_class(_annotations(copy))
        name = printable(f"{prefix}__{tool} {tp.decided_hash}")
        rule = policy.rule_for(prefix, tool)
        if rule is None and not policy.class_from_annotations(prefix, tool):
            unclassified += 1
            lines.append(f"no class {name}; annotations suggest {suggested}")
        elif rule is not None and contradicts(rule.cls, suggested):
            contradicted += 1
            lines.append(f"contradicts {name}: class {rule.cls}, annotations suggest {suggested}")
    if not lines:
        return []
    header = (
        f"classes: {unclassified} approved tools have no class, "
        f"{contradicted} contradict their annotations"
    )
    return [header, *lines]
