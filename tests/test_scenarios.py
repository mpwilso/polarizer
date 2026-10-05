"""The shipped drill scenario set passes the validation of docs/MEASURE-SPEC.md, section 6: its
schema, that every clean call is what its task asked and every planted call differs from its
task exactly as its shape says, that every name is invented, and the set's balance."""

import hashlib
import json
import re
import statistics
from collections import defaultdict
from datetime import UTC, datetime
from importlib import resources
from pathlib import Path

import pytest

from polarizer import drill, holds, lock, scenarios
from polarizer.scenarios import __main__ as helper
from polarizer.scenarios import audit
from polarizer.text import printable

SET = scenarios.newest()
ALL = list(SET.scenarios.values())
PLANTED = [s for s in ALL if s["answer"] == "planted"]
CLEAN = [s for s in ALL if s["answer"] == "clean"]

# sha256 of each shipped set file. Any edit to a shipped set fails here, so a change needs a new
# set version (section 6, Versioning). Set 1 is pinned unreviewed: until the owner's review at
# stage 8's stop it has not shipped, and the owner's corrections edit it and update this value.
PINNED = {1: "e2010c060409e8ba27147b4c9d7542cb33dd47f2556919ada5ff6b396692eb80"}
# sha256 of each set's plain words file (section 6, Plain words). A changed line, like a changed
# scenario, needs a new set version: a guided drill's set and set_sha256 then name its lines too.
PLAIN_PINNED = {1: "0d683e047477e0f1c306090fdf1ee7a42ae489261e40546e4701fe3c0edc9011"}
# The words a plain line never uses (the brief's list), and whole words that would compare the
# call with its task.
FORBIDDEN = ("planted", "clean", "wrong", "suspicious", "unexpected", "safe", "risky")
COMPARING = ("task", "asked", "instead", "also", "extra", "another", "different", "however",
             "actually", "but", "rather", "only")  # fmt: skip

# Look-alike characters, each mapped to the Latin letter it imitates (section 6, look-alike).
CONFUSABLES = {
    **{chr(c): v for c, v in [(0x0430, "a"), (0x0435, "e"), (0x043E, "o"), (0x0440, "p"),
                              (0x0441, "c"), (0x0445, "x"), (0x0443, "y"), (0x0456, "i"),
                              (0x03B1, "a"), (0x03BF, "o"), (0x03C1, "p"), (0x03BD, "v")]},
    "0": "o", "1": "l", "-": "_",
}  # fmt: skip
PAIRS = {"rn": "m", "vv": "w"}
AFFIXES = ("-js", "-py", "-dev", "-cli", "-official")
PREFIXES = ("py-", "node-")

# Well-known companies, products and package registries: no entry of `names` may be one, or
# hold one as a part. Written in lowercase.
DENY = set(
    "google alphabet amazon aws microsoft azure apple icloud meta facebook instagram whatsapp "
    "twitter slack discord stripe paypal openai anthropic claude chatgpt gemini copilot docker "
    "kubernetes jira atlassian confluence notion linear github gitlab bitbucket heroku vercel "
    "netlify cloudflare npm npmjs pypi pip cargo crates rubygems maven nuget homebrew react "
    "angular vue django flask rails lodash requests numpy pandas express netflix uber oracle "
    "ibm intel nvidia samsung adobe dropbox zoom outlook yahoo ubuntu debian redhat postgres "
    "postgresql mysql mongodb redis".split()
)
# Prefixes and headers of real credentials.
CREDENTIALS = [
    re.compile(p)
    for p in (
        r"sk-[A-Za-z0-9]{8,}", r"sk_live_", r"ghp_[A-Za-z0-9]", r"gho_", r"github_pat_",
        r"AKIA[0-9A-Z]{12,}", r"xox[abpr]-", r"-----BEGIN", r"AIza[0-9A-Za-z_-]{20,}",
        r"eyJ[A-Za-z0-9_-]{10,}\.", r"glpat-", r"AAAA[A-Za-z0-9+/]{20,}",
    )
]  # fmt: skip
RESERVED_LAST = {"example", "test", "invalid"}
RESERVED_DOMAINS = ("example.com", "example.net", "example.org")


def strings(value):
    """Every string in a JSON value, keys included."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for k, v in value.items():
            yield k
            yield from strings(v)
    elif isinstance(value, list):
        for v in value:
            yield from strings(v)


def contains(value, part) -> bool:
    """Substring for strings, element-of for lists (section 6, Validation)."""
    if isinstance(value, str):
        return isinstance(part, str) and part in value
    if isinstance(value, list):
        return part in value
    return False


def covers(call_value, intent_value) -> bool:
    if call_value == intent_value:
        return True
    if isinstance(call_value, str) and isinstance(intent_value, str):
        return intent_value in call_value
    if isinstance(call_value, list) and isinstance(intent_value, list):
        rest = iter(call_value)
        return all(any(x == y for y in rest) for x in intent_value)
    return False


def levenshtein(a: str, b: str) -> int:
    row = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        prev, row[0] = row[0], i
        for j, cb in enumerate(b, 1):
            prev, row[j] = row[j], min(row[j] + 1, row[j - 1] + 1, prev + (ca != cb))
    return row[-1]


def skeleton(text: str) -> str:
    for pair, letter in PAIRS.items():
        text = text.replace(pair, letter)
    return "".join(CONFUSABLES.get(c, c) for c in text)


def close(intended: str, planted: str) -> bool:
    return (
        levenshtein(intended, planted) <= 3
        or skeleton(intended) == skeleton(planted)
        or planted in {intended + a for a in AFFIXES}
        or planted in {p + intended for p in PREFIXES}
    )


def planted_value(s) -> str:
    return s["call"]["tool"] if s["shape"] == "different-tool" else s["plant"]["planted"]


def differing(intent: dict, call: dict) -> set:
    return {k for k in set(intent) | set(call) if intent.get(k, object()) != call.get(k)}


# --- the schema ---------------------------------------------------------------------------


def test_scenario_file_schema():
    files = scenarios.shipped()
    assert files and all(name == f"drill-set-{v}.json" for v, name in files.items())
    for version, name in files.items():
        data = resources.files(scenarios).joinpath(name).read_bytes()
        loaded = scenarios.parse(data, version)
        assert loaded.doc["format"] == "polarizer-drill-scenarios"
        assert loaded.doc["set"] == version
        assert data.isascii()
    assert SET.version == max(files)


def test_load_by_version():
    """Every shipped set loads by its version, so a ledger that names an older set can draw its
    plan again; a version not installed is a SetProblem."""
    for version in scenarios.shipped():
        assert scenarios.load(version).version == version
    assert scenarios.load(SET.version).sha256 == SET.sha256
    with pytest.raises(scenarios.SetProblem, match="scenario set 99 is not installed"):
        scenarios.load(99)


def test_scenario_set_hash_pinned():
    for version, name in scenarios.shipped().items():
        data = resources.files(scenarios).joinpath(name).read_bytes()
        assert hashlib.sha256(data).hexdigest() == PINNED[version], name


# --- the lines in plain words (section 6, Plain words) ----------------------------------------


def plain_bytes(version: int) -> bytes:
    return resources.files(scenarios).joinpath(scenarios.plain_name(version)).read_bytes()


def test_plain_words_hash_pinned():
    for version in scenarios.shipped():
        assert hashlib.sha256(plain_bytes(version)).hexdigest() == PLAIN_PINNED[version]


def test_every_scenario_has_one_plain_line():
    """The companion file names this set and its sha256, and has exactly one line per scenario
    of the set, no other ids; the loader gives them to the set."""
    doc = json.loads(plain_bytes(SET.version))
    assert doc["format"] == "polarizer-drill-plain-words" and doc["set"] == SET.version
    assert doc["set_sha256"] == SET.sha256 == PINNED[SET.version]
    assert sorted(doc["lines"]) == sorted(SET.scenarios) and len(doc["lines"]) == 150
    assert SET.plain == doc["lines"]
    assert plain_bytes(SET.version).isascii()


def test_plain_words_use_no_forbidden_word():
    """No line uses a word that judges the call or names its answer, in any case and inside any
    word, nor a word that compares it with the task."""
    for i, line in SET.plain.items():
        lowered = line.lower()
        assert not [w for w in FORBIDDEN if w in lowered], (i, line)
        assert not set(re.findall(r"[a-z]+", lowered)) & set(COMPARING), (i, line)
    assert set(FORBIDDEN) == set(scenarios.PLAIN_FORBIDDEN)
    assert set(COMPARING) == set(scenarios.PLAIN_COMPARING)


def test_plain_words_length():
    """About one sentence: 4 to 24 words (under 25), at most 200 characters, one line of
    printable ASCII, ending a sentence."""
    for i, line in SET.plain.items():
        assert 4 <= len(line.split()) <= 24 and len(line) <= 200, (i, line)
        assert scenarios.printable_line(line) and line.rstrip('"').endswith((".", "?")), i


def test_plain_words_name_the_call():
    """Each line names its call exactly where it matters: a planted call's line shares a word
    with the value that differs from its task (or, for a different tool, with the tool), and for
    a changed argument or a look-alike holds that value's last part as `holds` would print
    it."""
    texts = audit.focus(ALL)
    for s in PLANTED:
        line, text = SET.plain[s["id"]], texts[s["id"]]
        assert audit.words(text) & audit.words(line), (s["id"], text, line)
        if s["shape"] in ("changed-argument", "look-alike"):
            planted = printable(s["plant"]["planted"]).removeprefix("https://").rstrip("/")
            assert planted.split("/")[-1] in line, (s["id"], planted, line)


def test_plain_words_leak_nothing():
    """Section 6, Plain words: on the guided screen, with the plain line's length and whether
    it names the argument that matters added to the audit's features, no feature value has a
    tell, and the best single-feature rule still beats always allow by at most 10 points."""
    assert audit.tells(ALL, SET.plain) == []
    split = audit.split(ALL, SET.plain)
    assert set(split) >= set(audit.PLAIN_NAMES)
    rows = audit.experiment(SET, 1000, SET.plain)
    assert len(rows) == len(audit.NAMES) + len(audit.PLAIN_NAMES) + 2
    allow, best = rows[0], audit.best_single(rows)
    assert best[1] - allow[1] <= audit.MARGIN, best
    assert max(r[2] for r in rows[1:-1]) - allow[2] <= audit.MARGIN, rows


def test_plain_leak_check_catches_a_leak():
    """The leak check fails on lines that give the answer away: planted lines made longer than
    every clean one, and clean lines that never name their argument."""
    long = {s["id"]: SET.plain[s["id"]] + " " + "word " * 12 for s in PLANTED}
    vague = {s["id"]: "Xyzzy plugh." for s in CLEAN}
    for lines in ({**SET.plain, **long}, {**SET.plain, **vague}):
        assert audit.tells(ALL, lines) != []
    found = audit.tells(ALL, {**SET.plain, **vague})
    assert ("plain names the argument", "no", 0, 90) in found
    found = audit.tells(ALL, {**SET.plain, **long})
    assert ("plain length", "<70", 0, 26) in found and ("plain length", "70-99", 0, 28) in found


def test_plain_words_loader_checks():
    """The loader refuses a plain words file with a missing or an extra id, a line for another
    copy of the set, a forbidden or comparing word, or a line too long, each as a SetProblem,
    which the drill prints as its one refusal line."""
    data = resources.files(scenarios).joinpath("drill-set-1.json").read_bytes()
    good = json.loads(plain_bytes(1))
    assert scenarios.parse(data, 1, plain_bytes(1)).plain == good["lines"]

    def problem(change) -> str:
        doc = json.loads(json.dumps(good))
        change(doc)
        with pytest.raises(scenarios.SetProblem) as e:
            scenarios.parse(data, 1, json.dumps(doc).encode("ascii"))
        return str(e.value)

    assert problem(lambda d: d["lines"].pop("s042")) == "scenario s042 has no line in plain words"
    assert problem(lambda d: d["lines"].update(s151="Delete the file x.")) == (
        "the plain words file has a line for s151, which is not in the set"
    )
    assert "another copy of set 1" in problem(lambda d: d.update(set_sha256="0" * 64))
    assert problem(lambda d: d["lines"].update(s001="Write a risky file now.")) == (
        "scenario s001: the line in plain words uses the word 'risky'"
    )
    assert problem(lambda d: d["lines"].update(s001="Write it, but in a new place.")) == (
        "scenario s001: the line in plain words uses the word 'but'"
    )
    assert "4 to 24 words" in problem(lambda d: d["lines"].update(s001="word " * 25 + "end."))
    assert "not for set 1" in problem(lambda d: d.update(set=2))


def test_clean_calls_match_their_task():
    for s in CLEAN:
        assert s["call"]["tool"] == s["intent"]["tool"], s["id"]
        assert s["call"]["arguments"] == s["intent"]["arguments"], s["id"]


@pytest.mark.parametrize("s", PLANTED, ids=[s["id"] for s in PLANTED])
def test_planted_calls_differ_as_stated(s):
    intent, call, plant = s["intent"], s["call"], s["plant"]
    ia, ca = intent["arguments"], call["arguments"]
    shape = s["shape"]
    if shape == "different-tool":
        assert call["tool"] != intent["tool"]
        return
    assert call["tool"] == intent["tool"]
    changed = differing(ia, ca)
    if shape == "changed-argument":
        assert set(ia) == set(ca)
        assert changed == {plant["argument"]}
        assert ia[plant["argument"]] == plant["intended"]
        assert ca[plant["argument"]] == plant["planted"]
        assert plant["cue"] in plant["intended"]
    elif shape == "extra-effect":
        assert set(ia) <= set(ca)
        assert all(covers(ca[k], ia[k]) for k in ia)
        assert changed and plant["argument"] in changed
        assert contains(ca[plant["argument"]], plant["planted"])
        assert not contains(ia.get(plant["argument"], ""), plant["planted"])
    elif shape == "misleading-summary":
        summary, argument = plant["summary_argument"], plant["argument"]
        assert summary != argument
        assert ca[summary] == ia[summary]
        assert ca[argument] != ia[argument]
        assert contains(ca[argument], plant["planted"])
        assert not contains(ia[argument], plant["planted"])
    elif shape == "look-alike":
        argument = plant["argument"]
        assert changed == {argument}
        assert ia[argument].count(plant["intended"]) == 1
        assert ca[argument] == ia[argument].replace(plant["intended"], plant["planted"])
        assert close(plant["intended"], plant["planted"])
    else:
        pytest.fail(f"unknown shape {shape}")


def test_planted_value_not_in_task_and_named_in_why():
    for s in PLANTED:
        value = planted_value(s)
        assert isinstance(value, str), s["id"]
        assert value.lower() not in s["task"].lower(), s["id"]
        assert printable(value) in s["why"], s["id"]


def test_reasons_match_rule_templates():
    for s in ALL:
        call = s["call"]
        pattern = scenarios.reason_pattern(call["rule"], call["class_from"])
        assert pattern.fullmatch(call["reason"]), (s["id"], call["reason"])
    # The templates refuse what they should.
    write = scenarios.reason_pattern("write-pattern", "config")
    assert write.fullmatch('argument "path": /a/.git/hooks/x matches .git/hooks/**')
    assert not write.fullmatch('argument "path": /a/b is outside every workspace root')
    destructive = scenarios.reason_pattern("destructive", "annotations")
    assert destructive.fullmatch("class destructive is held on every call (class from annotations)")
    assert not destructive.fullmatch("class destructive is held on every call")


# --- invented names -----------------------------------------------------------------------

URL_HOST = re.compile(r"[a-z][a-z0-9+.-]*://([^/\s\"'@]+@)?([^/\s\"':]+)")
MAIL_HOST = re.compile(r"[A-Za-z0-9._+-]+@([A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+)")
BARE_DOMAIN = re.compile(r"\b[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.(?:com|net|org|io|dev|ai|co|app)\b")
HOME_USER = re.compile(r"/(?:home|Users)/([^/\s\"']+)")


def reserved(host: str) -> bool:
    host = host.lower().rstrip(".")
    labels = host.split(".")
    if labels[-1] in RESERVED_LAST:
        return True
    return any(host == d or host.endswith("." + d) for d in RESERVED_DOMAINS)


def hosts_in(s) -> set:
    found = set()
    for text in strings(s):
        found |= {m.group(2) for m in URL_HOST.finditer(text)}
        found |= {m.group(1) for m in MAIL_HOST.finditer(text)}
        found |= {m.group(0) for m in BARE_DOMAIN.finditer(text)}
    for side in (s["intent"]["arguments"], s["call"]["arguments"]):
        if isinstance(side.get("host"), str):
            found.add(side["host"])
    return found


def package_names(s) -> set:
    found = set()
    for side in (s["intent"]["arguments"], s["call"]["arguments"]):
        if isinstance(side.get("package"), str):
            found.add(side["package"])
        found |= {p for p in side.get("packages", []) if isinstance(p, str)}
    return found


def test_names_are_invented():
    names = set(SET.doc["names"])
    for name in names:
        parts = set(re.split(r"[^a-z0-9]+", name.lower())) | {name.lower()}
        assert not parts & DENY, name
    for s in ALL:
        for host in hosts_in(s):
            assert reserved(host), (s["id"], host)
        for text in strings(s):
            for user in HOME_USER.findall(text):
                assert user in names, (s["id"], user)
            for pattern in CREDENTIALS:
                assert not pattern.search(text), (s["id"], text)
        for package in package_names(s):
            assert package in names, (s["id"], package)


def test_name_checks_catch_real_looking_names():
    """The checks above refuse what they are for."""
    assert not reserved("files.examp1e.net") and not reserved("example.com.evil.net")
    assert reserved("docs.example.com") and reserved("relay.invalid")
    assert hosts_in({"intent": {"arguments": {}}, "call": {"arguments": {}},
                     "why": "see https://real-site.io/x and ops@corp.com"}) == {
        "real-site.io", "corp.com"}  # fmt: skip
    assert any(p.search("-----BEGIN OPENSSH PRIVATE KEY-----") for p in CREDENTIALS)
    assert HOME_USER.findall("/home/someone/.ssh") == ["someone"]


# --- balance ------------------------------------------------------------------------------


def block_lines(s) -> list[str]:
    call = s["call"]
    hold = holds.Hold(
        hold="0" * 16, seq=0, ts="2026-10-06T18:02:11.425Z", session="1" * 16,
        tool=call["tool"], args_commit="2" * 64, cls=call["class"], class_from=call["class_from"],
        rule=call["rule"], reason=call["reason"], timeout_seconds=SET.timeout_seconds,
    )  # fmt: skip
    size = len(json.dumps(call["arguments"], separators=(",", ":"), ensure_ascii=False).encode())
    now = datetime(2026, 10, 6, 18, 2, 11, 425000, tzinfo=UTC)
    return holds.render_block(hold, "2026-10-06T18:00:00.000Z", lock.RUNNING, now,
                              call["arguments"], size)  # fmt: skip


def test_set_balance():
    assert len(ALL) == 150 and len(CLEAN) == 90 and len(PLANTED) == 60
    shapes = defaultdict(int)
    for s in PLANTED:
        shapes[s["shape"]] += 1
    assert dict(shapes) == {shape: 12 for shape in scenarios.SHAPES}
    for key in ("rule", "class", "tool"):
        counts = defaultdict(lambda: [0, 0])
        for s in ALL:
            counts[s["call"][key]][s["answer"] == "planted"] += 1
        for value, (clean, planted) in counts.items():
            if planted:
                assert clean / (clean + planted) >= 0.4, (key, value, clean, planted)
    tools = defaultdict(set)
    for s in PLANTED:
        tools[s["shape"]].add(s["call"]["tool"])
    assert all(len(t) >= 3 for t in tools.values()), dict(tools)
    planted_median = statistics.median(len(block_lines(s)) for s in PLANTED)
    clean_median = statistics.median(len(block_lines(s)) for s in CLEAN)
    assert abs(planted_median - clean_median) <= 0.2 * clean_median
    # Alarming holds that are right, and routine-looking holds that are planted.
    assert any(s["call"]["arguments"].get("path", "").endswith(".env.example")
               and s["call"]["tool"] == "fs__write_file" for s in CLEAN)  # fmt: skip
    assert any(s["call"]["tool"] == "fs__move_file" for s in CLEAN)
    assert any(s["call"]["rule"] == "outside-roots" for s in PLANTED)


def test_no_shortcut():
    """Section 6, Shortcuts: over the shipped set, no feature value a drill shows before the
    answer, with at least 6 scenarios, has 90% or more of one answer, and the best single-
    feature rule, learned leave-one-out, beats always answering allow by at most 10 points, over
    the scenarios and over 1,000 seeded 20-call drills."""
    assert audit.tells(ALL) == []
    rows = audit.experiment(SET, 1000)
    allow, best = rows[0], audit.best_single(rows)
    assert allow[0] == "always allow" and len(rows) == len(audit.NAMES) + 2
    assert best[1] - allow[1] <= audit.MARGIN, best
    assert max(r[2] for r in rows[1:-1]) - allow[2] <= audit.MARGIN, rows


def test_shortcut_checks_catch_a_tell():
    """The checks above fail on a set with a tell: the shipped set with ".." added to every
    planted call's arguments, and six more arguments on six clean calls of two."""
    doc = json.loads(json.dumps(SET.doc))
    for s in doc["scenarios"]:
        if s["answer"] == "planted":
            s["call"]["arguments"]["note"] = "../x"
    two = [
        s for s in doc["scenarios"] if s["answer"] == "clean" and len(s["call"]["arguments"]) == 2
    ]
    for s in two[:6]:
        s["call"]["arguments"].update({"a1": 1, "a2": 2, "a3": 3, "a4": 4, "a5": 5, "a6": 6})
    the_set = scenarios.parse(json.dumps(doc).encode("ascii"), SET.version)
    found = audit.tells(list(the_set.scenarios.values()))
    assert ('".." in arguments', "yes", 60, 0) in found
    assert ("arguments", "8", 0, 6) in found
    rows = audit.experiment(the_set, 50)
    best = audit.best_single(rows)
    assert best[0] == '".." in arguments' and best[1] == 1.0 and best[2] == 1.0
    assert best[1] - rows[0][1] > audit.MARGIN


def test_audit_features_and_buckets():
    """The features are what the call screen shows before the answer, nothing from the reveal,
    the intent or the plant; buckets have fixed edges."""
    s = {"task": "x" * 75, "why": "planted", "answer": "planted", "plant": {"cue": "x"},
         "call": {"tool": "fs__read_file", "class": "local-read", "class_from": "config",
                  "rule": "read-pattern", "reason": "r", "arguments": {"path": "/a/.ssh/k"}}}  # fmt: skip
    assert audit.features(s) == {
        "tool": "fs__read_file", "class": "local-read", "class_from": "config",
        "rule": "read-pattern", "arguments": "1", "argument bytes": "<60", "task length": "75-89",
        '".." in arguments': "no", '".git" in arguments': "no", '".ssh" in arguments': "yes",
        '".env" in arguments': "no", '"http" in arguments': "no", '"@" in arguments': "no",
    }  # fmt: skip
    assert list(audit.features(s)) == list(audit.NAMES)
    edges = audit.ARGS_EDGES
    assert [audit.bucket(n, edges) for n in (0, 59, 60, 99, 100, 139, 140)] == [
        "<60", "<60", "60-99", "60-99", "100-139", "100-139", "140+"]  # fmt: skip


def test_every_scenario_renders():
    for s in ALL:
        lines = block_lines(s)
        assert len(lines) <= 60, s["id"]
        assert all(line.isascii() for line in lines), s["id"]


def test_ids_carry_no_hint():
    """Ids are given in task-hash order, so neither answer nor shape follows from them."""
    ids = [s["id"] for s in ALL]
    assert ids == [f"s{i:03d}" for i in range(1, 151)]
    first_half = {s["answer"] for s in ALL[:75]}
    assert first_half == {"clean", "planted"}


# --- the review sheet (python -m polarizer.scenarios sheet) ---------------------------------


def test_review_sheet(tmp_path, capsys):
    """`sheet --out <path>` writes every scenario of the shipped set, in file order, with its
    id, answer, shape, task, the block a drill shows and its reveal, then the summary counts;
    the shipped set has nothing to look at."""
    out = tmp_path / "review" / "scenarios.md"
    assert helper.main(["sheet", "--out", str(out)]) == 0
    assert capsys.readouterr().out == f"wrote {out}: 150 scenarios of set 1\n"
    text = out.read_bytes().decode("ascii")
    assert [line[3:] for line in text.splitlines() if line.startswith("## s")] == list(
        SET.scenarios
    )
    for s in ALL:
        section = text.split(f"\n## {s['id']}\n", 1)[1].split("\n## ", 1)[0]
        answer = f"planted, shape {s['shape']}" if s["answer"] == "planted" else "clean"
        block = drill.call_block(s, "0" * 16, "0" * 64, "1" * 16, helper.TS, helper.TS, 300)
        block.append(drill.PLAIN_PREFIX + SET.plain[s["id"]])
        assert section.startswith(f"\n{answer}\n\n```text\ntask: {s['task']}\n\n"), s["id"]
        assert "\n".join(block) + "\n```\n\nReveal:\n" in section, s["id"]
        assert section.endswith(f"\n```text\nwhy: {s['why']}\n```\n"), s["id"]
    summary = text.split("\n## Summary\n", 1)[1]
    for shape in scenarios.SHAPES:
        assert f"\n| {shape} | 12 |\n" in summary
        assert f"\n| {shape} | 12 | " in summary  # planted versus clean
    assert "\n| clean | 90 |\n| planted | 60 |\n" in summary
    assert "\n| fs__write_file | 15 |\n" in summary and "\n| egress | 56 |\n" in summary
    assert "\n### Look at these\n" in summary and "\nNothing.\n" in summary
    audit_part = summary.split("\n### Shortcut audit, set 1\n", 1)[1]
    assert "\n#### tool\n" in audit_part and "\n#### Leave-one-out accuracy\n" in audit_part
    assert "\nTells: none.\n" in audit_part
    guided = audit_part.split("\n### Shortcut audit, set 1, guided screen\n", 1)[1]
    assert "\n#### plain length\n" in guided and "\n#### plain names the argument\n" in guided
    assert "\n#### tool\n" not in guided.split("\n### Planted count")[0]
    assert "\nTells: none.\n" in guided
    assert audit_part.split("\n### Planted count, set 1\n", 1)[1].count("| 100.0% | 0 |") == 2
    assert text.endswith("|\n")
    assert helper.main(["sheet"]) == 2 and helper.main(["sheet", "--out"]) == 2
    capsys.readouterr()
    assert helper.main(["audit"]) == 0
    printed = capsys.readouterr().out
    assert (
        printed.startswith("# Shortcut audit, set 1\n") and "\n# Planted count, set 1\n" in printed
    )
    assert "\n# Shortcut audit, set 1, guided screen\n" in printed
    assert printed.endswith("|\n") and helper.main(["audit", "x"]) == 2


def test_review_sheet_lists_what_to_look_at():
    """On the small test set, changed to have a repeated task line, a near-duplicate one, a short
    reveal and backticks in a reveal: each is listed, as are its shapes with fewer than 8
    scenarios and its tools held in fewer than 3; a reveal holding a fence gets a longer one."""
    doc = json.loads((Path(__file__).parent / "helpers" / "drill_scenarios.json").read_bytes())
    # Clean scenarios first, so a changed task line never loses a planted call's cue.
    doc["scenarios"].sort(key=lambda s: s["answer"] != "clean")
    items = doc["scenarios"]
    items[1]["task"] = items[0]["task"]
    items[3]["task"] = items[2]["task"][:-1] + "?"
    items[4]["why"] = "Too short to say why."
    items[5]["why"] = "A fence ``` inside a reveal, and four ```` more, in one line of text."
    the_set = scenarios.parse(json.dumps(doc).encode("ascii"), 1)
    found = helper.look_at(the_set)
    ids = [s["id"] for s in items]
    assert f"{ids[0]} and {ids[1]}: the same task line" in found
    assert any(line.startswith(f"{ids[2]} and {ids[3]}: task lines 0.") for line in found)
    assert f"{ids[4]}: the reveal has 5 words" in found
    shapes = {s["shape"] for s in items if s["answer"] == "planted"}
    assert {line for line in found if line.startswith("shape ")} == {
        f"shape {x}: {sum(s.get('shape') == x for s in items)} scenarios" for x in shapes
    }
    tools = [s["call"]["tool"] for s in items]
    rare = [s["id"] for s in items if tools.count(s["call"]["tool"]) < 3]
    assert rare and all(any(line.startswith(f"{i}: tool ") for line in found) for i in rare)
    text = helper.sheet(the_set)
    assert f"\n`````text\nwhy: {items[5]['why']}\n`````\n" in text
    assert "\n- " + found[0] + "\n" in text
