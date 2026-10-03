"""The polarizer command line: verify [--args] and repair (docs/PROXY-SPEC.md, Command line).

Results go to stdout. Usage errors, config errors, unreadable files, protected-path refusals
and the git-tree warning go to stderr, one line each; every error among them exits 2.
"""

import argparse
import os
import sys
from pathlib import Path

from polarizer import config, ledgerdir, sidefiles, writer
from polarizer.ledger import LEDGER, LOCKED_LINE, Locked, read_files, verify_bytes


class UsageError(Exception):
    pass


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise UsageError(message)


def _parser() -> argparse.ArgumentParser:
    parser = _Parser(prog="polarizer", description="Polarizer's ledger tools.")
    commands = parser.add_subparsers(dest="command", required=True, parser_class=_Parser)
    for name, text in [
        ("verify", "check the ledger and report its status"),
        ("repair", "remove a torn tail, and nothing else"),
    ]:
        sub = commands.add_parser(name, help=text, description=text)
        sub.add_argument("--config", metavar="PATH", help="absolute path to polarizer.toml")
        sub.add_argument("--ledger-dir", metavar="DIR", help="absolute path to the ledger dir")
        if name == "verify":
            sub.add_argument("--args", action="store_true", help="also check side files")
    return parser


def _err(line: str) -> int:
    print(line, file=sys.stderr)
    return 2


def main(argv=None) -> int:
    try:
        args = _parser().parse_args(argv)
        if (args.config is None) == (args.ledger_dir is None):
            raise UsageError(
                f"{args.command} needs exactly one of --config <absolute path> "
                "or --ledger-dir <absolute path>"
            )
        given = args.config if args.config is not None else args.ledger_dir
        if not os.path.isabs(given):
            flag = "--config" if args.config is not None else "--ledger-dir"
            raise UsageError(f"{flag} must be an absolute path, got {given}")
    except UsageError as e:
        return _err(f"polarizer: {e}")
    if args.config is not None:
        try:
            cfg = config.load(Path(args.config), require_env=False)
        except config.ConfigError as e:
            return _err(str(e))
        ledger_dir, protected = cfg.ledger_dir, list(cfg.protected_paths)
    else:
        ledger_dir, protected = Path(args.ledger_dir), ledgerdir.always_protected()
    if args.command == "verify":
        return verify(ledger_dir, args.args)
    return repair(ledger_dir, protected)


def verify(ledger_dir: Path, with_args: bool) -> int:
    """Read-only: creates, deletes and modifies nothing."""
    try:
        data, head = read_files(ledger_dir)
    except Locked:
        print(LOCKED_LINE)
        return 7
    except OSError as e:
        return _err(f"polarizer: cannot read {e.filename or ledger_dir / LEDGER}: {e.strerror}")
    if not data:
        print(f"no ledger at {ledger_dir}")
        return 2
    result = verify_bytes(data, head)
    for line in result.output_lines():
        print(line)
    code = result.exit_code
    if with_args and result.status == "intact":
        try:
            report = sidefiles.check_args(ledger_dir, result.state.calls_sent)
        except OSError as e:
            return _err(f"polarizer: cannot read {e.filename}: {e.strerror}")
        for line in report.lines:
            print(line)
        if report.tampered:
            code = 8
    return code


def repair(ledger_dir: Path, protected: list[Path]) -> int:
    try:
        warning = ledgerdir.check_location(ledger_dir, protected)
    except ledgerdir.ProtectedPath as e:
        return _err(str(e))
    if warning:
        print(warning, file=sys.stderr)
    try:
        outcome = writer.repair(ledger_dir)
    except OSError as e:
        return _err(f"polarizer: cannot repair {e.filename or ledger_dir}: {e.strerror}")
    for line in outcome.lines:
        print(line)
    return outcome.exit_code
