"""scripts/*.sh and scripts/dev/*.sh run on macOS too, where bash is 3.2 and find, sed, date,
stat and grep are the BSD ones. CI's macOS job would show a break there; this shows it on Linux first, by keeping out the
bash 4 and 5 features and the GNU-only options and commands that macOS lacks. It reads the
scripts as text and runs nothing, so it says nothing about how they behave: that is for the
tests that run them (test_guard.py, test_m1a_check.py, test_rugpull_check.py) and for CI on
macOS. Full-line comments are skipped, so a comment may name what it avoids."""

import re
from pathlib import Path

import pytest

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
SCRIPTS = sorted([*_SCRIPTS_DIR.glob("*.sh"), *_SCRIPTS_DIR.glob("dev/*.sh")])
OPTS = r"(?:\s+-[-\w]+)*"  # any options before the one that matters

NOT_IN_BASH_3_2 = {
    "shopt inherit_errexit (bash 4.4)": r"\binherit_errexit\b",
    "shopt lastpipe (bash 4.2)": r"\blastpipe\b",
    "shopt globstar (bash 4.0)": r"\bglobstar\b",
    "mapfile or readarray (bash 4.0)": r"\b(mapfile|readarray)\b",
    "associative array (bash 4.0)": r"\b(declare|local|typeset)" + OPTS + r"\s+-\w*A",
    "nameref (bash 4.3)": r"\b(declare|local|typeset)" + OPTS + r"\s+-\w*n\b",
    "declare -g (bash 4.2)": r"\b(declare|typeset)" + OPTS + r"\s+-\w*g\b",
    "local - (bash 4.4)": r"\blocal\s+-(\s|$)",
    "case conversion ${x,,} or ${x^^} (bash 4.0)": r"\$\{[^}]*(,|\^)\}",
    "${x@Q} and other transformations (bash 4.4)": r"\$\{[^}]*@[QEPAaUuLK]\}",
    "negative array index (bash 4.3)": r"\$\{\w+\[-\d",
    "&>> (bash 4.0)": r"&>>",
    "|& (bash 4.0)": r"\|&",
    "coproc (bash 4.0)": r"\bcoproc\b",
    "wait -n, -f or -p (bash 4.3 to 5.1)": r"\bwait\s+-\w*[nfp]\b",
    "{fd}> redirection (bash 4.1)": r"\{\w+\}[<>]",
    ";& or ;;& in case (bash 4.0)": r";;?&",
    "[[ -v ]] or test -v (bash 4.2)": r"(\[\[?|\btest)\s+-v\s",
    "read -i or -N (bash 4.0, 4.1)": r"\bread" + OPTS + r"\s+-\w*[iN]\b",
    "printf %(...)T (bash 4.2)": r"%\([^)]*\)T",
    "$BASHPID, $EPOCHSECONDS, $EPOCHREALTIME or $SRANDOM (bash 4 and 5)": (
        r"\$\{?(BASHPID|EPOCHSECONDS|EPOCHREALTIME|SRANDOM)\b"
    ),
}

GNU_ONLY = {
    "find -printf": r"\bfind\b.*\s-printf\b",
    "find -newerXY (BSD date parsing differs)": r"\bfind\b.*\s-newer[a-zA-Z]{2}\b",
    "find -regextype": r"\bfind\b.*\s-regextype\b",
    "stat -c or --format (BSD stat uses -f)": r"\bstat" + OPTS + r"\s+(-\w*c\b|--format|--printf)",
    "date -d or --date (BSD date uses -r or -j)": r"\bdate" + OPTS + r"\s+(-\w*d\b|--date)",
    "readlink -f (only macOS 12.3 and later)": r"\breadlink" + OPTS + r"\s+-\w*f\b",
    "grep -P": r"\bgrep" + OPTS + r"\s+-\w*P",
    "grep with \\| in a basic regex": r"\bgrep\b[^|]*'[^']*\\\|",
    "sed -i (BSD needs an extension argument)": r"\bsed" + OPTS + r"\s+-\w*i\b",
    "sed -r (BSD uses -E)": r"\bsed" + OPTS + r"\s+-\w*r\b",
    "\\t in a sed expression (BSD sed reads a plain t)": r"\bsed\b.*\\t",
    "xargs -r": r"\bxargs" + OPTS + r"\s+-\w*r\b",
    "sort -V": r"\bsort" + OPTS + r"\s+-\w*V",
    "head -n with a negative count": r"\bhead\s+-n\s*-\d",
    "base64 -w": r"\bbase64" + OPTS + r"\s+-\w*w",
    "a command macOS doesn't ship (tac, nproc, timeout, sha256sum, md5sum, getent)": (
        r"(^|[\s;|&(`$])(tac|nproc|timeout|sha256sum|md5sum|getent)(?=[\s);|&]|$)"
    ),
}


def code_lines(path):
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.lstrip().startswith("#"):
            yield number, line


# A `wait` for a process substitution: bash 3.2 can wait only for its own jobs, so there it
# returns at once, and a script that waited that way for its tee could exit before tee wrote
# (docs/dev/STAGE7-NOTES.md, Follow-up: macOS output). Read as text: a PID variable set from $!
# after a process substitution, with no job started with & in between, and any `wait` that
# names it, wherever it is (a function that waits may come earlier in the file); or `wait $!`
# straight after a process substitution.
PROCESS_SUBSTITUTION = re.compile(r"(?<![$<>])[<>]\(")
BACKGROUND = re.compile(r"(?<![&<>|])&(?![&>])")
PID_FROM_BANG = re.compile(r"\b(\w+)=\"?\$(?:!|\{!\})")
WAIT = re.compile(r"\bwait\b(.*)")


def procsub_waits(lines):
    """The (number, line) pairs of each `wait` on a process substitution's PID."""
    last, tainted, waits = None, set(), []
    for number, line in lines:
        events = sorted(
            [(m.start(), "procsub", None) for m in PROCESS_SUBSTITUTION.finditer(line)]
            + [(m.start(), "job", None) for m in BACKGROUND.finditer(line)]
            + [(m.start(), "pid", m.group(1)) for m in PID_FROM_BANG.finditer(line)]
            + [(m.start(), "wait", m.group(1)) for m in WAIT.finditer(line)]
        )
        for _, kind, text in events:
            if kind in ("procsub", "job"):
                last = kind
            elif kind == "pid" and last == "procsub":
                tainted.add(text)
            elif kind == "wait":
                waits.append((number, line, text, last == "procsub" and "$!" in text))
    return [
        (number, line)
        for number, line, args, bang in waits
        if bang or any(re.search(r"\$\{?" + name + r"\b", args) for name in tainted)
    ]


@pytest.mark.parametrize("path", SCRIPTS, ids=lambda p: p.name)
def test_no_bash_4_features_or_gnu_only_tools(path):
    found = [
        f"{path.name}:{number}: {what}: {line.strip()}"
        for number, line in code_lines(path)
        for rules in (NOT_IN_BASH_3_2, GNU_ONLY)
        for what, pattern in rules.items()
        if re.search(pattern, line)
    ]
    assert found == []


@pytest.mark.parametrize("path", SCRIPTS, ids=lambda p: p.name)
def test_no_wait_on_a_process_substitution(path):
    found = [f"{path.name}:{n}: {line.strip()}" for n, line in procsub_waits(code_lines(path))]
    assert found == []


@pytest.mark.parametrize(
    "script",
    [
        ["exec > >(tee -a log) 2>&1", "TEE_PID=$!", 'wait "$TEE_PID" 2> /dev/null || true'],
        ['end_output() { wait "${TEE_PID}"; }', "exec > >(tee log) 2>&1", "TEE_PID=$!"],
        ["exec 3> >(cat)", "wait $!"],
        ["diff <(ls a) <(ls b)", 'wait "$!"'],
    ],
)
def test_the_wait_rule_catches_a_process_substitution(script):
    assert len(procsub_waits(enumerate(script, 1))) == 1, script


@pytest.mark.parametrize(
    "script",
    [
        ['(trap "" INT; exec tee -a log) < "$dir/tee" &', "TEE_PID=$!", 'wait "$TEE_PID"'],
        ["exec > >(tee log) 2>&1", "sleep 1 &", "wait $!"],
        ["exec > >(tee log) 2>&1", 'echo "$x" 2>&1 >&2', "wait"],
    ],
)
def test_the_wait_rule_passes_a_job(script):
    assert procsub_waits(enumerate(script, 1)) == [], script


@pytest.mark.parametrize(
    "line",
    [
        "shopt -s inherit_errexit",
        "mapfile -t lines < file",
        "declare -A seen",
        'echo "${name,,}"',
        'echo "${arr[-1]}"',
        "cmd &>> log",
        "cmd |& tee log",
        "wait -n",
        "exec {fd}> file",
        "[[ -v name ]]",
        'find "$1" -printf "%P\\t%s\\n"',
        'find . -newermt "@$epoch" -print',
        'stat -c %s "$1"',
        'date -u -d "@$epoch" +%s',
        'readlink -f "$0"',
        "grep -oP 'x'",
        "grep -n 'a\\|b' file",
        "sed -i 's/a/b/' file",
        "sed -E -i 's/a/b/' file",
        "sed 's#^#x\\t#'",
        "xargs -r rm",
        "sort -V",
        "head -n -1",
        "x=$(nproc)",
        "timeout 5 cmd",
    ],
)
def test_the_rules_catch_each_feature(line):
    assert any(
        re.search(pattern, line)
        for rules in (NOT_IN_BASH_3_2, GNU_ONLY)
        for pattern in rules.values()
    ), line
