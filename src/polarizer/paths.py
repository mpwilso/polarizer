"""Paths in tool arguments: resolution and hold patterns (docs/HOLD-SPEC.md, section 3).

A path is resolved before any comparison, the way the operating system would, and never by
asking the upstream. Whatever Polarizer can't resolve that way is held, with one of a few fixed
reasons. Resolution only reads: lstat and readlink on POSIX, os.path.realpath on Windows. It runs
in a worker thread (the gateway's evaluation), and each path is bounded by RESOLVE_BOUND seconds
measured there, so a hung network mount holds the call instead of hanging it.

Hold patterns are "/"-separated lists of parts on every platform: "*" and "?" inside a part,
"**" as a whole part for any number of parts. Anchored patterns start at the root ("/", "~/",
or on Windows a drive); every other pattern floats, as if it began with "**/".
"""

import os
import re
import stat
import sys
import threading
import unicodedata
from dataclasses import dataclass

from polarizer.text import safe

RESOLVE_BOUND = 2.0  # seconds per path
MAX_LINKS = 40
MAX_PATTERN = 1024
WINDOWS = sys.platform == "win32"
# Case: Windows and macOS compare patterns and paths after casefold() and NFC; Linux exactly.
FOLD = sys.platform in ("win32", "darwin")

NOT_ABSOLUTE = "not an absolute path"
TILDE = "starts with ~, which the server may expand"
NUL = "contains a NUL character"
DOTDOT_AFTER_MISSING = '".." after a part that does not exist'
TOO_MANY_LINKS = "too many symbolic links"
TOO_SLOW = "took longer than 2 s to resolve"
WINDOWS_NAME = "a Windows name Polarizer does not resolve"
CANNOT_EXAMINE = "cannot be examined: {why}"

_DRIVE = re.compile(r"[A-Za-z]:/")
_RESERVED = re.compile(r"(con|prn|aux|nul|com[0-9]|lpt[0-9])(\..*)?", re.IGNORECASE)


class Unresolvable(Exception):
    """A path Polarizer does not resolve; str() is the fixed reason for the record."""


# Resolution --------------------------------------------------------------------------------


def resolve(path: str) -> str:
    """The resolved path, or Unresolvable with its reason (HOLD-SPEC.md, section 3)."""
    if "\0" in path:
        raise Unresolvable(NUL)
    if path.startswith("~"):
        raise Unresolvable(TILDE)
    if WINDOWS:
        return _resolve_windows(path)
    if not path.startswith("/"):
        raise Unresolvable(NOT_ABSOLUTE)
    return _resolve_posix(path)


def _examine_failed(error: BaseException) -> Unresolvable:
    why = getattr(error, "strerror", None) or str(error) or type(error).__name__
    return Unresolvable(CANNOT_EXAMINE.format(why=safe(why)))


def _resolve_posix(path: str) -> str:
    """POSIX realpath, part by part: lstat each part that exists, replace a symbolic link by
    its target (a relative one against the link's directory) and go on from there, apply ".."
    to what is resolved so far. Once a part doesn't exist, the rest is appended as written,
    except that a ".." then can't be resolved. "." parts and repeated slashes are dropped."""
    todo = [p for p in path.split("/") if p not in ("", ".")]
    todo.reverse()  # a stack: the next part is at the end
    done: list[str] = []
    links = 0
    missing = False
    while todo:
        part = todo.pop()
        if part in ("", "."):
            continue
        if missing:
            if part == "..":
                raise Unresolvable(DOTDOT_AFTER_MISSING)
            done.append(part)
            continue
        if part == "..":
            if done:
                done.pop()
            continue
        here = "/" + "/".join([*done, part])
        try:
            info = os.lstat(here)
        except (FileNotFoundError, NotADirectoryError):
            missing = True
            done.append(part)
            continue
        except (OSError, ValueError) as e:
            raise _examine_failed(e) from None
        if not stat.S_ISLNK(info.st_mode):
            done.append(part)
            continue
        links += 1
        if links > MAX_LINKS:
            raise Unresolvable(TOO_MANY_LINKS)
        try:
            target = os.readlink(here)
        except (OSError, ValueError) as e:
            raise _examine_failed(e) from None
        if target.startswith("/"):
            done = []
        todo.extend(reversed([p for p in target.split("/") if p not in ("", ".")]))
    return "/" + "/".join(done)


def _windows_name_problem(path: str) -> bool:
    """True for the Windows names Polarizer does not resolve: a \\\\?\\ or \\\\.\\ prefix, a
    ":" after the drive, a part ending in a dot or a space, or a reserved device name."""
    slashed = path.replace("\\", "/")
    if slashed.startswith(("//?/", "//./")):
        return True
    body = slashed[2:] if re.match(r"[A-Za-z]:", slashed) else slashed
    if ":" in body:
        return True
    for part in body.split("/"):
        if part in ("", ".", ".."):
            continue
        if part.endswith((".", " ")) or _RESERVED.fullmatch(part):
            return True
    return False


def _resolve_windows(path: str) -> str:
    """Windows: the names above are held; a path without a drive and a root, or a UNC root,
    isn't absolute; a ".." after a part that doesn't exist is held as on POSIX; the rest is
    os.path.realpath, which follows links and junctions and returns long names."""
    if _windows_name_problem(path):
        raise Unresolvable(WINDOWS_NAME)
    slashed = path.replace("\\", "/")
    unc = slashed.startswith("//") and not slashed.startswith("///")
    if not (_DRIVE.match(slashed[:3]) or unc):
        raise Unresolvable(NOT_ABSOLUTE)
    drive, rest = os.path.splitdrive(path)
    parts = [p for p in rest.replace("\\", "/").split("/") if p not in ("", ".")]
    current = drive + "\\"
    missing = False
    for part in parts:
        if part == "..":
            if missing:
                raise Unresolvable(DOTDOT_AFTER_MISSING)
            current = os.path.dirname(current.rstrip("\\")) or current
            continue
        current = os.path.join(current, part)
        if not missing:
            try:
                os.lstat(current)
            except (FileNotFoundError, NotADirectoryError):
                missing = True
            except (OSError, ValueError) as e:
                raise _examine_failed(e) from None
    # strict=True first: the non-strict form walks past a part it can't follow, which hides a
    # loop (ERROR_CANT_RESOLVE_FILENAME, 1921). A path that doesn't exist yet is resolved
    # non-strictly, as POSIX resolution appends what doesn't exist.
    try:
        return os.path.realpath(path, strict=True)
    except (FileNotFoundError, NotADirectoryError):
        pass
    except OSError as e:
        if getattr(e, "winerror", None) == 1921:
            raise Unresolvable(TOO_MANY_LINKS) from None
        raise _examine_failed(e) from None
    except ValueError as e:
        raise _examine_failed(e) from None
    try:
        return os.path.realpath(path)
    except (OSError, ValueError) as e:
        raise _examine_failed(e) from None


def resolve_bounded(path: str, bound: float = RESOLVE_BOUND, resolver=resolve) -> str:
    """resolver(path) on a daemon thread of its own, waiting at most `bound` seconds. A path
    that takes longer is Unresolvable (TOO_SLOW); its thread is left to finish on its own."""
    box: dict = {}
    done = threading.Event()

    def run():
        try:
            box["path"] = resolver(path)
        except Unresolvable as e:
            box["error"] = e
        except Exception as e:  # a resolver must never take the call down; hold it instead
            box["error"] = _examine_failed(e)
        finally:
            done.set()

    threading.Thread(target=run, name="polarizer-resolve", daemon=True).start()
    if not done.wait(bound):
        raise Unresolvable(TOO_SLOW)
    if "error" in box:
        raise box["error"]
    return box["path"]


# Patterns ----------------------------------------------------------------------------------


def pattern_problem(text: str) -> str | None:
    """Why a configured pattern is not valid, as the config error's reason; None if it is."""
    if not isinstance(text, str) or text == "":
        return "empty"
    if len(text) > MAX_PATTERN:
        return f"longer than {MAX_PATTERN} characters"
    if "\\" in text:
        return 'a backslash; use "/"'
    if text.startswith("~") and not text.startswith("~/"):
        return '"~" only as the first part, followed by "/"'
    for part in _body(text).split("/"):
        if part == "":
            return "an empty part"
        if part in (".", ".."):
            return '"." or ".." as a part'
        if "**" in part and part != "**":
            return '"**" must be a whole part'
        if part == "~":
            return '"~" only as the first part, followed by "/"'
    return None


def _body(text: str) -> str:
    """A pattern without its anchor: "/", "~/" or (Windows) a drive and "/"."""
    if text.startswith("/"):
        return text[1:]
    if text.startswith("~/"):
        return text[2:]
    if WINDOWS and _DRIVE.match(text[:3]):
        return text[3:]
    return text


def _norm(text: str, fold: bool) -> str:
    return unicodedata.normalize("NFC", text).casefold() if fold else text


@dataclass(frozen=True)
class Pattern:
    """A compiled hold pattern. `text` is as configured, `~` unexpanded, for the record."""

    text: str
    anchored: bool
    drive: str | None  # Windows: the drive of an anchored pattern ("c:"), None for "/" or "~"
    parts: tuple[str, ...]  # after the anchor; floating patterns start with "**"


def _home_parts() -> tuple[str | None, tuple[str, ...]]:
    """The home directory, resolved, as (Windows drive or None, parts)."""
    return split(os.path.realpath(os.path.expanduser("~")))


def compile_pattern(text: str, home=None) -> Pattern:
    """A valid pattern (pattern_problem returned None), compiled. "~" is expanded to the
    resolved home directory, once."""
    if text.startswith("~/"):
        drive, base = home if home is not None else _home_parts()
        return Pattern(text, True, drive, (*base, *_body(text).split("/")))
    if text.startswith("/"):
        return Pattern(text, True, None, tuple(_body(text).split("/")))
    if WINDOWS and _DRIVE.match(text[:3]):
        return Pattern(text, True, text[:2], tuple(_body(text).split("/")))
    return Pattern(text, False, None, ("**", *text.split("/")))


def split(resolved: str) -> tuple[str | None, tuple[str, ...]]:
    """A resolved path as (Windows drive or UNC root, or None; its parts). A Windows "\\" is
    read as "/"."""
    slashed = resolved.replace("\\", "/") if WINDOWS else resolved
    drive = None
    if WINDOWS:
        if _DRIVE.match(slashed[:3]) or re.match(r"[A-Za-z]:\Z", slashed):
            drive, slashed = slashed[:2], slashed[2:]
        elif slashed.startswith("//"):
            pieces = slashed[2:].split("/")
            drive = "//" + "/".join(pieces[:2])
            slashed = "/" + "/".join(pieces[2:])
    return drive, tuple(p for p in slashed.split("/") if p)


def _part_regex(part: str, fold: bool) -> re.Pattern:
    out = []
    for ch in _norm(part, fold):
        if ch == "*":
            out.append(".*")
        elif ch == "?":
            out.append(".")
        else:
            out.append(re.escape(ch))
    return re.compile("".join(out), re.DOTALL)


def _match_parts(pattern: tuple[str, ...], path: tuple[str, ...], fold: bool) -> bool:
    """Whether pattern parts match path parts wholly; "**" matches zero or more parts."""
    regexes = [None if p == "**" else _part_regex(p, fold) for p in pattern]
    names = [_norm(p, fold) for p in path]
    # reach[j]: the first i pattern parts can match the first j path parts.
    reach = [True] + [False] * len(names)
    for regex in regexes:
        if regex is None:
            seen = False
            nxt = []
            for ok in reach:
                seen = seen or ok
                nxt.append(seen)
        else:
            nxt = [False] + [
                reach[j] and bool(regex.fullmatch(names[j])) for j in range(len(names))
            ]
        reach = nxt
    return reach[-1]


def matches(pattern: Pattern, resolved: str, fold: bool = FOLD) -> bool:
    """Whether a resolved path matches a compiled pattern, with the case rule `fold`."""
    drive, parts = split(resolved)
    if pattern.anchored and pattern.drive is not None:
        if drive is None or _norm(drive, True) != _norm(pattern.drive, True):
            return False
    return _match_parts(pattern.parts, parts, fold)


# The built-in lists (section 3). BUILTIN_PATTERNS is the version number in the policy hash,
# raised whenever these lists change.
BUILTIN_PATTERNS = 1
BUILTIN_WRITE = (
    ".git/hooks/**",
    ".git/config",
    ".github/workflows/**",
    "~/.ssh/**",
    ".env*",
    "~/.bashrc",
    "~/.bash_profile",
    "~/.bash_login",
    "~/.bash_logout",
    "~/.profile",
    "~/.zshrc",
    "~/.zshenv",
    "~/.zprofile",
    "~/.zlogin",
    "~/.config/fish/**",
    *(("~/Documents/PowerShell/**", "~/Documents/WindowsPowerShell/**") if WINDOWS else ()),
    ".claude/**",
    ".mcp.json",
    "~/.claude.json",
    "~/.claude/**",
)
BUILTIN_READ = (
    "~/.ssh/**",
    "~/.gnupg/**",
    "~/.aws/**",
    "~/.config/gh/**",
    "~/.netrc",
    "~/.git-credentials",
    "~/.claude/.credentials.json",
    ".env*",
)
