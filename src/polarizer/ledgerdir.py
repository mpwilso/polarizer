"""Where the ledger may live (docs/LEDGER-SPEC.md, Location and permissions).

serve and repair refuse a ledger_dir inside a forbidden path, and warn inside any other git
working tree. Paths are compared as resolved real paths, component by component, never as
string prefixes. The installed tool never reads .guard-paths; that is scripts/dev/guard.sh's file.
"""

import os
import subprocess
from collections.abc import Iterable
from pathlib import Path


class ForbiddenPath(Exception):
    """ledger_dir is inside a forbidden path. The message is the one stderr line; exit 2."""


def default_ledger_dir() -> Path:
    return Path.home() / ".local" / "share" / "polarizer"


def always_forbidden() -> list[Path]:
    """Parallax's runtime and config directories. A config can add to these, never remove."""
    home = Path.home()
    return [home / ".local" / "share" / "parallax", home / ".config" / "parallax"]


def _parts(path: Path) -> tuple[str, ...]:
    return tuple(os.path.normcase(part) for part in Path(os.path.realpath(path)).parts)


def _nearest_existing(path: Path) -> Path | None:
    for candidate in [path, *path.parents]:
        if candidate.exists():
            return candidate
    return None


def is_inside(child: Path, parent: Path) -> bool:
    """True if child is parent or below it, by resolved real path components. Where both
    exist, the operating system's own idea of "same directory" also counts, which covers
    case-insensitive file systems."""
    child_parts, parent_parts = _parts(child), _parts(parent)
    if child_parts[: len(parent_parts)] == parent_parts:
        return True
    if not parent.exists():
        return False
    real_child = Path(os.path.realpath(child))
    for ancestor in [real_child, *real_child.parents]:
        try:
            if ancestor.exists() and os.path.samefile(ancestor, parent):
                return True
        except OSError:
            continue
    return False


def check_location(ledger_dir: Path, forbidden: Iterable[Path], find_tree=None) -> str | None:
    """Raise ForbiddenPath if ledger_dir is inside any forbidden path. Otherwise return the
    git-tree warning line, or None. `find_tree` finds the working tree; by default it asks git
    (git_toplevel), and a drill passes git_tree_by_files, which starts no process."""
    for path in forbidden:
        if is_inside(ledger_dir, path):
            raise ForbiddenPath(
                f"polarizer: ledger_dir {ledger_dir} is inside {path}, "
                "which Polarizer must not write to"
            )
    top = (find_tree or git_toplevel)(ledger_dir)
    if top:
        return f"polarizer: warning: ledger_dir {ledger_dir} is inside the git working tree {top}"
    return None


def git_toplevel(path: Path) -> str | None:
    """The git working tree that contains path (or its nearest existing parent), or None.
    Only reads: `git rev-parse --show-toplevel`, with optional locks off."""
    start = _nearest_existing(Path(os.path.realpath(path)))
    if start is None or not start.is_dir():
        start = start.parent if start is not None else None
    if start is None:
        return None
    try:
        done = subprocess.run(
            ["git", "-C", str(start), "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            timeout=10,
            env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"},
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    top = done.stdout.strip()
    return top if done.returncode == 0 and top else None


def git_tree_by_files(path: Path) -> str | None:
    """The git working tree that contains path (or its nearest existing parent), found by looking
    for a .git directory or file there and in each parent, with no process started. A tree found
    only through GIT_DIR is missed (docs/MEASURE-SPEC.md, section 17, stage 8's step 0)."""
    start = _nearest_existing(Path(os.path.realpath(path)))
    if start is not None and not start.is_dir():
        start = start.parent
    for directory in [start, *start.parents] if start is not None else []:
        if (directory / ".git").exists():
            return str(directory)
    return None
