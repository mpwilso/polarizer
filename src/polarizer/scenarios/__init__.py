"""The drill scenario set: versioned data in this package, loaded and checked at the start of
every drill (docs/MEASURE-SPEC.md, section 6).

The check here is the part of the validation that needs no test-only data: keys, types,
lengths, the consistency of answer and shape, and each reason against its rule's template
(docs/HOLD-SPEC.md, section 4). tests/test_scenarios.py runs the full validation: that each
planted call differs from its task as its shape says, that every name is invented, and the
balance of the set.
"""

import hashlib
import json
import re
from dataclasses import dataclass
from importlib import resources

FORMAT = "polarizer-drill-scenarios"
_FILE = re.compile(r"drill-set-([1-9][0-9]*)\.json")
SHAPES = ("changed-argument", "different-tool", "extra-effect", "misleading-summary", "look-alike")
SHAPE_WORDS = {
    "changed-argument": "changed argument",
    "different-tool": "different tool",
    "extra-effect": "extra effect",
    "misleading-summary": "misleading summary",
    "look-alike": "look-alike",
}
CLASSES = ("local-read", "local-write", "destructive", "egress", "open-world")
EXPOSED = re.compile(r"[A-Za-z0-9._-]{1,128}")
ID = re.compile(r"s[0-9]{3}")
TASK_LENGTH = (20, 160)
WHY_LENGTH = 300
ARGUMENTS_LIMIT = 4096  # bytes of compact JSON

PLAN_KEYS = {
    "changed-argument": {"argument", "intended", "planted", "cue"},
    "different-tool": {"cue"},
    "extra-effect": {"argument", "planted", "cue"},
    "misleading-summary": {"summary_argument", "argument", "planted", "cue"},
    "look-alike": {"argument", "intended", "planted", "cue"},
}
SCENARIO_KEYS = {"id", "answer", "task", "intent", "call", "why"}
CALL_KEYS = {"tool", "class", "class_from", "rule", "reason", "arguments"}

# The reasons of HOLD-SPEC.md, section 4, one regular expression per row.
_ARG = r'argument "[A-Za-z0-9_.-]+"'
_FIXED = (
    r"(not an absolute path|starts with ~, which the server may expand|contains a NUL character"
    r'|".." after a part that does not exist|too many symbolic links|cannot be examined: .+'
    r"|took longer than 2 s to resolve|too many path resolutions are stuck"
    r"|a Windows name Polarizer does not resolve)"
)
ROWS = {
    6: rf"{_ARG} is missing",
    7: rf"{_ARG} is not a string or a list of strings",
    8: rf"{_ARG}: {_FIXED}",
    9: rf"{_ARG}: \S+ is inside Polarizer's ledger directory",
    10: rf"{_ARG}: \S+ is Polarizer's config file",
    11: rf"{_ARG}: \S+ matches \S+",
    12: rf"{_ARG}: \S+ is outside every workspace root",
    14: rf"{_ARG}: \S+ matches \S+",
}
_ANNOTATIONS = r" \(class from annotations\)"


def _any(rows) -> str:
    return "(" + "|".join(ROWS[r] for r in rows) + ")"


# rule -> (the classes it goes with, the reason's pattern without the annotations suffix)
RULES = {
    "unclassified": ((None,), r"[A-Za-z0-9._-]+__[A-Za-z0-9._-]+ has no class in polarizer\.toml"),
    "destructive": (
        ("destructive",),
        rf"class destructive is held on every call(; {_any(range(6, 13))})?",
    ),
    "egress": (("egress",), rf"class egress is held on every call(; {_any((6, 7, 8, 9, 14))})?"),
    "write-unchecked": (
        ("local-write",),
        r"class local-write has no path_args, so its paths cannot be checked",
    ),
    "path-missing": (("local-write", "local-read", "open-world"), ROWS[6]),
    "path-not-string": (("local-write", "local-read", "open-world"), ROWS[7]),
    "path-unresolvable": (("local-write", "local-read", "open-world"), ROWS[8]),
    "polarizer-files": (("local-write", "local-read", "open-world"), f"({ROWS[9]}|{ROWS[10]})"),
    "write-pattern": (("local-write",), ROWS[11]),
    "outside-roots": (("local-write",), ROWS[12]),
    "read-pattern": (("local-read", "open-world"), ROWS[14]),
}


class SetProblem(Exception):
    """The set fails its check; the message is the problem, for the one refusal line."""


@dataclass(frozen=True)
class ScenarioSet:
    version: int
    sha256: str
    timeout_seconds: int
    scenarios: dict  # id -> scenario, in file order
    doc: dict

    def ids(self, answer: str) -> list[str]:
        return sorted(i for i, s in self.scenarios.items() if s["answer"] == answer)


def printable_line(text) -> bool:
    return isinstance(text, str) and all(0x20 <= ord(c) <= 0x7E for c in text)


def reason_pattern(rule: str, class_from) -> re.Pattern:
    suffix = _ANNOTATIONS if class_from == "annotations" else ""
    return re.compile(RULES[rule][1] + suffix)


def _check_call(where: str, call, servers: list) -> None:
    if not isinstance(call, dict) or set(call) != CALL_KEYS:
        raise SetProblem(f"{where}: call must have exactly the keys {sorted(CALL_KEYS)}")
    _check_tool(where, call["tool"], servers)
    cls, origin, rule = call["class"], call["class_from"], call["rule"]
    if cls is not None and cls not in CLASSES:
        raise SetProblem(f"{where}: call.class {cls!r} is not a class")
    if (cls is None) != (origin is None) or origin not in (None, "config", "annotations"):
        raise SetProblem(f"{where}: call.class_from does not go with call.class")
    if rule not in RULES:
        raise SetProblem(f"{where}: call.rule {rule!r} is not a rule that holds a call")
    if cls not in RULES[rule][0]:
        raise SetProblem(f"{where}: rule {rule} does not go with class {cls}")
    if not printable_line(call["reason"]):
        raise SetProblem(f"{where}: call.reason must be one line of printable ASCII")
    if not reason_pattern(rule, origin).fullmatch(call["reason"]):
        raise SetProblem(f"{where}: call.reason does not match the template of rule {rule}")
    _check_arguments(where, call["arguments"])


def _check_tool(where: str, tool, servers: list) -> None:
    if not isinstance(tool, str) or not EXPOSED.fullmatch(tool) or "__" not in tool:
        raise SetProblem(f"{where}: {tool!r} is not an exposed tool name")
    if tool.split("__", 1)[0] not in servers:
        raise SetProblem(f"{where}: {tool} has a prefix not listed in servers")


def _check_arguments(where: str, arguments) -> None:
    if not isinstance(arguments, dict):
        raise SetProblem(f"{where}: arguments must be a JSON object")
    compact = json.dumps(arguments, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    if len(compact) > ARGUMENTS_LIMIT:
        raise SetProblem(f"{where}: arguments are longer than {ARGUMENTS_LIMIT} bytes")


def check_scenario(s, servers: list) -> None:
    """Raise SetProblem for the first schema rule a scenario breaks."""
    if not isinstance(s, dict):
        raise SetProblem("a scenario is not a JSON object")
    where = f"scenario {s['id']}" if printable_line(s.get("id")) else "a scenario"
    answer = s.get("answer")
    if answer not in ("clean", "planted"):
        raise SetProblem(f"{where}: answer must be clean or planted")
    keys = SCENARIO_KEYS | ({"shape", "plant"} if answer == "planted" else set())
    if set(s) != keys:
        raise SetProblem(f"{where}: keys must be exactly {sorted(keys)}")
    if not isinstance(s["id"], str) or not ID.fullmatch(s["id"]):
        raise SetProblem(f"{where}: id must be s and three digits")
    task, why = s["task"], s["why"]
    if not printable_line(task) or not TASK_LENGTH[0] <= len(task) <= TASK_LENGTH[1]:
        raise SetProblem(f"{where}: task must be 20 to 160 printable ASCII characters")
    if not printable_line(why) or not 1 <= len(why) <= WHY_LENGTH:
        raise SetProblem(f"{where}: why must be 1 to 300 printable ASCII characters")
    intent = s["intent"]
    if not isinstance(intent, dict) or set(intent) != {"tool", "arguments"}:
        raise SetProblem(f"{where}: intent must have exactly the keys tool and arguments")
    _check_tool(where, intent["tool"], servers)
    _check_arguments(where, intent["arguments"])
    _check_call(where, s["call"], servers)
    if answer == "planted":
        shape, plant = s["shape"], s["plant"]
        if shape not in SHAPES:
            raise SetProblem(f"{where}: shape {shape!r} is not one of the five")
        if not isinstance(plant, dict) or set(plant) != PLAN_KEYS[shape]:
            raise SetProblem(f"{where}: plant must have exactly {sorted(PLAN_KEYS[shape])}")
        cue = plant["cue"]
        if not isinstance(cue, str) or len(cue) < 4 or cue not in task:
            raise SetProblem(f"{where}: plant.cue must be a part of task, 4 characters or more")


def check(doc, version: int) -> None:
    """Raise SetProblem for the first schema rule the set breaks."""
    if not isinstance(doc, dict):
        raise SetProblem("the set is not a JSON object")
    keys = {"format", "set", "timeout_seconds", "servers", "names", "scenarios"}
    if set(doc) != keys:
        raise SetProblem(f"the set must have exactly the keys {sorted(keys)}")
    if doc["format"] != FORMAT:
        raise SetProblem(f"format is not {FORMAT}")
    if type(doc["set"]) is not int or doc["set"] != version:
        raise SetProblem(f"set is not {version}, the version in the file name")
    if type(doc["timeout_seconds"]) is not int or doc["timeout_seconds"] <= 0:
        raise SetProblem("timeout_seconds is not a positive integer")
    for key in ("servers", "names"):
        if not isinstance(doc[key], list) or not all(isinstance(x, str) for x in doc[key]):
            raise SetProblem(f"{key} is not a list of strings")
    scenarios = doc["scenarios"]
    if not isinstance(scenarios, list) or not scenarios:
        raise SetProblem("scenarios is not a non-empty list")
    seen = set()
    for s in scenarios:
        check_scenario(s, doc["servers"])
        if s["id"] in seen:
            raise SetProblem(f"scenario {s['id']}: id is used twice")
        seen.add(s["id"])
    for answer in ("clean", "planted"):
        if not any(s["answer"] == answer for s in scenarios):
            raise SetProblem(f"the set has no {answer} scenario")


def parse(data: bytes, version: int) -> ScenarioSet:
    """A set from its file's bytes, checked. Raises SetProblem."""
    try:
        doc = json.loads(data.decode("ascii"))
    except (UnicodeDecodeError, ValueError) as e:
        raise SetProblem(f"not ASCII JSON: {e}") from None
    check(doc, version)
    return ScenarioSet(
        version=version,
        sha256=hashlib.sha256(data).hexdigest(),
        timeout_seconds=doc["timeout_seconds"],
        scenarios={s["id"]: s for s in doc["scenarios"]},
        doc=doc,
    )


def shipped() -> dict[int, str]:
    """{version: file name} of every set file in this package."""
    found = {}
    for entry in resources.files(__name__).iterdir():
        match = _FILE.fullmatch(entry.name)
        if match:
            found[int(match.group(1))] = entry.name
    return found


def newest() -> ScenarioSet:
    """The newest set in the package, checked. Raises SetProblem."""
    files = shipped()
    if not files:
        raise SetProblem("no scenario set file is installed")
    version = max(files)
    return parse(resources.files(__name__).joinpath(files[version]).read_bytes(), version)
