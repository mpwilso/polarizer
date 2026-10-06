"""docs/DRILL-GUIDE.md, checked against the code, as tests/test_readme.py checks the README
(docs/MEASURE-SPEC.md, section 13).

Every command in the guide's sh blocks is run or parsed. The polarizer commands parse with
Polarizer's own parser and are run in the guide's order on a temporary home: the drill on a
pseudo-terminal, answered by the test (POSIX; Windows has no pty module), then the report and
the export in a working directory of their own. The two uv lines that need no network, the
package file's install line and the uninstall line, are run as written, with uv offline, into
a tool directory of the test's own, when uv's cache holds every package the install needs; an
offline dry run of the same install decides, and the test is skipped, naming the package,
only when that dry run fails for want of a cached package. A fresh machine's cache is empty
(CI run 37361705150). The three lines that fetch the project from GitHub are not run: the
release line's URL is parsed, and must name this version's tag and the wheel `uv build` makes,
and the polarizer command in the uvx line parses. The install check, `uv --version`, is run, and the
line the guide says the install ends with is checked against what uv printed. `uv tool
update-shell` edits shell startup files (or, on Windows, the user's PATH), so only its help is
run. The lines that delete the drill records folder, for macOS and Linux and for PowerShell,
are never run: each must name the folder drills write to. The screen excerpts are compared with
what a drill prints, and the closing lines with what Polarizer prints."""

import base64
import hashlib
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
import tomllib
import zipfile
from urllib.parse import urlsplit

import pytest
from conftest import ROOT
from helpers.ptyread import finish, read_some
from packaging.utils import parse_wheel_filename

import polarizer
from polarizer import cli, drill

GUIDE = (ROOT / "docs" / "DRILL-GUIDE.md").read_text(encoding="utf-8")
GOLDEN = ROOT / "tests" / "golden"
PYPROJECT = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
REPOSITORY = PYPROJECT["project"]["urls"]["Source"]
WHEEL = f"polarizer-{polarizer.__version__}-py3-none-any.whl"
RELEASE_WHEEL = f"{REPOSITORY}/releases/download/v{polarizer.__version__}/{WHEEL}"
# The guide's uv lines. Those that fetch the project from GitHub need the network and are not
# run; the others are run by test_guide_install_lines_run_offline. The release line is first.
NETWORK = [f"uv tool install {RELEASE_WHEEL}", f"uv tool install git+{REPOSITORY}",
           f"uvx --from git+{REPOSITORY} polarizer drill"]  # fmt: skip
OFFLINE = [f"uv tool install ./{WHEEL}", "uv tool uninstall polarizer"]
# The other lines: the install check, run; the PATH fix, whose help is run; and the delete,
# never run, checked against the drill directory.
CHECK = "uv --version"
PATH_FIX = "uv tool update-shell"
DELETE = "rm -r ~/.local/share/polarizer-drills"
POWERSHELL_DELETE = r'Remove-Item -Recurse "$env:USERPROFILE\.local\share\polarizer-drills"'
# What uv prints last when the package file's install worked; the guide quotes it.
INSTALLED = "Installed 1 executable: polarizer"
# What uv prints when an offline run needs a package its cache does not hold.
CACHE_MISS = "Packages were unavailable because the network was disabled"


def blocks(language: str) -> list[str]:
    return re.findall(rf"```{language}\n(.*?)```", GUIDE, re.S)


def commands() -> list[str]:
    return [line.strip() for block in blocks("sh") for line in block.splitlines() if line.strip()]


def test_guide_commands_exist():
    """Every polarizer command line parses with Polarizer's own parser, as does the polarizer
    command the uvx line runs; every other line is one of the lines above, so each is run or
    checked by a test here, or needs the network."""
    found = commands()
    assert [c for c in found if c.startswith("polarizer ")] == [
        "polarizer drill --calls 10",
        "polarizer drill report",
        "polarizer drill report --export drill-summary.json",
    ]
    for command in found:
        if command.startswith("polarizer "):
            cli._parser().parse_args(shlex.split(command)[1:])
    others = sorted(c for c in found if not c.startswith("polarizer "))
    assert others == sorted([*NETWORK, *OFFLINE, CHECK, PATH_FIX, DELETE])
    assert [line.strip() for line in blocks("powershell")[0].splitlines()] == [POWERSHELL_DELETE]
    uvx = shlex.split(NETWORK[2])
    assert uvx[:3] == ["uvx", "--from", f"git+{REPOSITORY}"] and uvx[3] == "polarizer"
    cli._parser().parse_args(uvx[4:])


def test_guide_release_line():
    """Step 2's first line installs the release wheel, and needs only uv. Parsed, never fetched:
    an https URL to this version's tag on the repository's releases, ending in the wheel name
    `uv build` makes (test_guide_install_lines_run_offline builds it), which the wheel filename
    parser reads as this package and version, for any Python 3. The git and uvx lines come
    after it, and the guide says both need git."""
    installs = [c for c in commands() if c.startswith(("uv tool install ", "uvx "))]
    assert installs[:2] == NETWORK[:2] and installs[-1] == NETWORK[2]
    argv = shlex.split(NETWORK[0])
    assert argv[:3] == ["uv", "tool", "install"] and len(argv) == 4
    url = urlsplit(argv[3])
    assert url.scheme == "https" and not url.query and not url.fragment
    assert f"https://{url.netloc}" + url.path.removesuffix(f"/{WHEEL}") == (
        f"{REPOSITORY}/releases/download/v{polarizer.__version__}"
    )
    name, version, build, tags = parse_wheel_filename(url.path.rsplit("/", 1)[1])
    assert (name, str(version), build) == ("polarizer", polarizer.__version__, ())
    assert {str(tag) for tag in tags} == {"py3-none-any"}
    install = GUIDE[GUIDE.index("2. Install Polarizer") : GUIDE.index("## Run your first drill")]
    assert "This line needs only uv" in install.split("```")[0]
    assert "If you have git and want the latest code" in install
    assert "so it needs git too" in install


def offline(argv, cwd, env) -> str | None:
    """Run argv with uv offline. None if it succeeded; if it failed only because uv's cache
    lacks a package it needs, uv's line naming that package. Any other failure fails the test,
    so a skip that follows a cache miss never hides a real failure."""
    done = subprocess.run(argv, cwd=cwd, env={**env, "UV_OFFLINE": "1"}, capture_output=True,
                          text=True, timeout=120)  # fmt: skip
    if done.returncode == 0:
        return None
    assert CACHE_MISS in done.stderr, done.stdout + done.stderr
    named = [line.strip() for line in done.stderr.splitlines() if "not found in the cache" in line]
    return named[0].removeprefix("cause: ") if named else CACHE_MISS


def dry_run(target):
    """The guide's package file install, resolved offline and installed nowhere: it needs the
    same packages from uv's cache as `uv tool install` does. `uv tool install` has no dry run."""
    return ["uv", "pip", "install", "--dry-run", "--target", str(target), f"./{WHEEL}"]


@pytest.mark.skipif(shutil.which("uv") is None, reason="the guide's uv lines need uv on PATH")
def test_guide_install_lines_run_offline(tmp_path):
    """The package file's install line and the uninstall line, run as the guide writes them,
    with UV_OFFLINE=1 so Polarizer's parts come from uv's cache, into a tool directory and a bin
    directory of the test's own. The package file is built offline first, as the owner builds
    the one a friend is sent. The installed polarizer then prints the report's first-run line,
    and the uninstall line removes it. Skipped, naming the missing package, when uv's cache
    can't supply the build or the install (an offline dry run of it decides); a fresh machine's
    cache is empty. Each step has 120 s against about a second needed here."""
    home = tmp_path / "home"
    home.mkdir()
    bin_dir = tmp_path / "bin"
    env = {**os.environ, "UV_OFFLINE": "1", "UV_TOOL_DIR": str(tmp_path / "tools"),
           "UV_TOOL_BIN_DIR": str(bin_dir)}  # fmt: skip

    def run(argv, cwd, extra=None):
        done = subprocess.run(argv, cwd=cwd, env={**env, **(extra or {})}, capture_output=True,
                              text=True, timeout=120)  # fmt: skip
        assert done.returncode == 0, done.stdout + done.stderr
        return done

    for step, argv, cwd in (
        ("build the package file", ["uv", "build", "--wheel", "--out-dir", str(home)], ROOT),
        (f"run {OFFLINE[0]}", dry_run(tmp_path / "dry-run"), home),
    ):
        missing = offline(argv, cwd, env)
        if missing is not None:
            pytest.skip(f"uv's cache can't {step} offline, as on a fresh machine: {missing}")
    assert (home / WHEEL).exists()
    installed_out = run(shlex.split(OFFLINE[0]), home)
    assert INSTALLED in installed_out.stdout + installed_out.stderr
    installed = shutil.which("polarizer", path=str(bin_dir))
    assert installed is not None
    report = run([installed, "drill", "report"], home, {"HOME": str(home),
                                                        "USERPROFILE": str(home)})  # fmt: skip
    drills = home / ".local" / "share" / "polarizer-drills"
    assert report.stdout == f"drills: none yet in {drills}; run polarizer drill\n"
    run(shlex.split(OFFLINE[1]), home)
    assert shutil.which("polarizer", path=str(bin_dir)) is None


@pytest.mark.skipif(shutil.which("uv") is None, reason="the guide's uv lines need uv on PATH")
def test_guide_check_lines():
    """Step 1's check prints uv and a version number, in the form of the guide's example; the
    PATH fix exists (its help, since running it edits shell startup files); and the guide
    quotes uv's last install line as the install test sees it."""
    version = subprocess.run(shlex.split(CHECK), capture_output=True, text=True, timeout=60)
    assert version.returncode == 0 and re.match(r"uv \d+\.\d+\.\d+", version.stdout), version
    assert re.search(r"such as `uv \d+\.\d+\.\d+`", GUIDE)
    fix = subprocess.run([*shlex.split(PATH_FIX), "--help"], capture_output=True, text=True,
                         timeout=60)  # fmt: skip
    assert fix.returncode == 0, fix.stderr
    assert f"ends with `{INSTALLED}`" in GUIDE


def test_guide_delete_lines_name_the_drill_directory(fake_home):
    """The delete lines are never run. Each names, relative to the home folder, the directory
    drills write to, and the guide says deleting it is permanent."""
    rm = shlex.split(DELETE)
    assert rm[:2] == ["rm", "-r"] and rm[2].startswith("~/")
    assert fake_home / rm[2][2:] == drill.default_dir()
    prefix = 'Remove-Item -Recurse "$env:USERPROFILE\\'
    assert POWERSHELL_DELETE.startswith(prefix) and POWERSHELL_DELETE.endswith('"')
    parts = POWERSHELL_DELETE[len(prefix) : -1].split("\\")
    assert fake_home.joinpath(*parts) == drill.default_dir()
    assert "Deleting it is permanent" in GUIDE


def wheel(where, name, requires=()):
    """A minimal pure-Python wheel, written by hand, so making it needs nothing from a cache."""
    info = f"{name}-1.0.dist-info"
    files = {
        f"{name}/__init__.py": "",
        f"{info}/METADATA": f"Metadata-Version: 2.1\nName: {name}\nVersion: 1.0\n"
        + "".join(f"Requires-Dist: {r}\n" for r in requires),
        f"{info}/WHEEL": "Wheel-Version: 1.0\nGenerator: tests\nRoot-Is-Purelib: true\n"
        "Tag: py3-none-any\n",
    }
    record = ""
    for path, text in files.items():
        digest = base64.urlsafe_b64encode(hashlib.sha256(text.encode()).digest()).rstrip(b"=")
        record += f"{path},sha256={digest.decode()},{len(text.encode())}\n"
    files[f"{info}/RECORD"] = record + f"{info}/RECORD,,\n"
    path = where / f"{name}-1.0-py3-none-any.whl"
    with zipfile.ZipFile(path, "w") as zf:
        for name_in_zip, text in files.items():
            zf.writestr(name_in_zip, text)
    return path


@pytest.mark.skipif(shutil.which("uv") is None, reason="the guide's uv lines need uv on PATH")
def test_offline_skips_only_for_a_cache_miss(tmp_path):
    """The decision behind the install test's skip, on an empty cache of the test's own, so it
    is the same on every machine: a wheel whose packages the cache holds (none) is ready to
    install, so the install test would run it; one that needs a package the cache lacks gives
    uv's line naming it; and a failure for any other reason fails, never skips."""
    env = {**os.environ, "UV_CACHE_DIR": str(tmp_path / "cache")}
    alone = wheel(tmp_path, "pzalone")
    needy = wheel(tmp_path, "pzneedy", ["pz-not-in-any-cache==1.0"])
    probe = ["uv", "pip", "install", "--dry-run", "--target", str(tmp_path / "dry-run")]
    assert offline([*probe, str(alone)], tmp_path, env) is None
    missing = offline([*probe, str(needy)], tmp_path, env)
    assert missing is not None and "pz-not-in-any-cache was not found in the cache" in missing
    with pytest.raises(AssertionError):
        offline([*probe, str(tmp_path / "absent.whl")], tmp_path, env)
    assert dry_run(tmp_path)[-1] == shlex.split(OFFLINE[0])[-1]


def test_guide_quotes_the_closing_lines():
    quoted = [line[2:] for line in GUIDE.splitlines() if line.startswith("> ")]
    assert quoted == drill.CLOSING


def test_guide_excerpts_are_what_a_drill_prints():
    """The call and reveal excerpts are lines of the golden screens (a guided call of a first
    drill's 10), and the export's example is in the golden export."""
    call, reveal = blocks("text")
    printed = (GOLDEN / "drill_call_guided.txt").read_text(encoding="utf-8").splitlines()
    for line in call.splitlines():
        if line not in ("...", "allow or deny?"):
            assert line in printed, line
    assert "allow or deny? " in printed
    revealed = (GOLDEN / "drill_reveal_right.txt").read_text(encoding="utf-8")
    assert revealed.startswith("\nClean call. You allowed it.\nwhy: ")
    from helpers.drillkit import TEST_SET

    readme = [s for s in TEST_SET.scenarios.values() if "Running the tests" in s["task"]][0]
    assert reveal == f"Clean call. You allowed it.\nwhy: {readme['why']}\n"
    assert '"answered":116' in (GOLDEN / "drill_export.json").read_text(encoding="utf-8")


def test_guide_paths_are_the_drill_directory(fake_home):
    assert "`~/.local/share/polarizer-drills`" in GUIDE
    assert drill.default_dir() == fake_home / ".local" / "share" / "polarizer-drills"


@pytest.mark.skipif(sys.platform == "win32", reason="Windows has no pty module")
def test_guide_commands_run(tmp_path):
    """The guide's polarizer commands, in order, as a person runs them, with a temporary home.
    Each wait is for text to appear, up to 60 s, against well under a second needed here."""
    import pty

    home = tmp_path / "home"
    home.mkdir()
    work = tmp_path / "work"
    work.mkdir()
    env = {**os.environ, "HOME": str(home), "USERPROFILE": str(home)}
    polarizer_cmd = [sys.executable, "-m", "polarizer"]
    run_order = [c for c in commands() if c.startswith("polarizer ")]

    # polarizer drill --calls 10, on a pseudo-terminal: Enter, then each call denied, then Enter.
    # A first drill in a new home, so it is guided.
    controller, terminal = pty.openpty()
    argv = polarizer_cmd + shlex.split(run_order[0])[1:]
    proc = subprocess.Popen(argv, stdin=terminal, stdout=terminal, stderr=terminal, env=env,
                            cwd=work)  # fmt: skip
    os.close(terminal)
    replies = {
        b"Press Enter to start.": b"\n",
        drill.PREDICT_PROMPT.encode().replace(b"\n", b"\r\n"): b"it does what the task says\n",
        b"allow or deny? ": b"d\n",
        b"Press Enter for the next call.": b"\n",
        b"Press Enter to see the results.": b"\n",
    }
    output, seen = b"", b""
    deadline = time.monotonic() + 300
    try:
        # Answer each prompt as it appears, whichever condition the drill drew; each prompt
        # appears within a second here, against 300 s for the whole drill.
        while time.monotonic() < deadline:
            chunk = read_some(controller, 0.05)
            if chunk is None:
                break
            output += chunk
            seen += chunk
            for prompt, reply in replies.items():
                if seen.endswith(prompt):
                    os.write(controller, reply)
                    seen = b""
                    break
        else:
            pytest.fail(f"the drill did not end:\n{output.decode()}")
        code, rest = finish(controller, proc)
        output += rest
    finally:
        if proc.poll() is None:
            proc.kill()
        os.close(controller)
    assert code == 0, output.decode()
    assert b"Drill finished: 10 of 10 calls answered (guided)." in output
    assert output.count(drill.PLAIN_PREFIX.encode()) == 10
    assert (home / ".local" / "share" / "polarizer-drills" / "ledger.jsonl").exists()

    report = subprocess.run(polarizer_cmd + shlex.split(run_order[1])[1:], env=env, cwd=work,
                            capture_output=True, text=True, timeout=120)  # fmt: skip
    assert report.returncode == 0 and report.stdout.startswith("drills: 1 finished"), report
    export = subprocess.run(polarizer_cmd + shlex.split(run_order[2])[1:], env=env, cwd=work,
                            capture_output=True, text=True, timeout=120)  # fmt: skip
    assert export.returncode == 0, export.stderr
    assert export.stdout == report.stdout
    assert (work / "drill-summary.json").read_text(encoding="utf-8").startswith('{"answered":10,')
