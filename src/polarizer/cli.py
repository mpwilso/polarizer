"""The polarizer command line: serve, verify [--args] and repair (docs/PROXY-SPEC.md), and
pending, approve and reject (docs/PIN-SPEC.md, section 7).

serve's stdout is the protocol channel: everything it says to the person goes to stderr.
The other commands print results to stdout. Usage errors, config errors, unreadable files,
refusals, forbidden-path refusals and the git-tree warning go to stderr, one line each; every
error among them exits 2, and ledger statuses keep their own codes.
"""

import argparse
import os
import sys
from pathlib import Path

import anyio

from polarizer import config, defhash, ledgerdir, pins, sidefiles, writer
from polarizer.decisions import Decider, Refusal, fold_reason
from polarizer.ledger import LEDGER, LOCKED_LINE, Locked, read_files, verify_bytes


class UsageError(Exception):
    pass


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise UsageError(message)


def _parser() -> argparse.ArgumentParser:
    parser = _Parser(prog="polarizer", description="Polarizer's ledger tools.")
    commands = parser.add_subparsers(dest="command", required=True, parser_class=_Parser)
    text = "run the proxy over stdio (started by the MCP client)"
    serve = commands.add_parser("serve", help=text, description=text)
    serve.add_argument("--config", metavar="PATH", help="absolute path to polarizer.toml")
    for name, text in [
        ("verify", "check the ledger and report its status"),
        ("repair", "remove a torn tail, and nothing else"),
        ("pending", "list the tool definitions that wait for a decision"),
        ("approve", "approve one tool definition, or a group that pending printed"),
        ("reject", "reject one tool definition, with a reason"),
    ]:
        sub = commands.add_parser(name, help=text, description=text)
        sub.add_argument("--config", metavar="PATH", help="absolute path to polarizer.toml")
        sub.add_argument("--ledger-dir", metavar="DIR", help="absolute path to the ledger dir")
        if name == "verify":
            sub.add_argument("--args", action="store_true", help="also check side files")
        if name in ("approve", "reject"):
            sub.add_argument("names", nargs="*", metavar="PREFIX TOOL DEF_HASH")
            sub.add_argument(
                "--allow-no-terminal",
                action="store_true",
                help="run although stdin is not a terminal (for scripts)",
            )
        if name == "approve":
            sub.add_argument("--group", metavar="ID", help="a group id that pending printed")
        if name in ("pending", "approve"):
            sub.add_argument("--upstream", metavar="PREFIX", help="only this upstream")
        if name == "reject":
            sub.add_argument("--reason", metavar="TEXT", help="why (required)")
    return parser


def _err(line: str) -> int:
    print(line, file=sys.stderr)
    return 2


def _set_up_streams() -> None:
    """UTF-8, backslashreplace and "\n" newlines on stdout and stderr, so every platform prints
    the same bytes. On native Windows the defaults would be the console or locale code page
    and "\r\n". Not for serve: its stdout is the protocol channel."""
    for stream in (sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="backslashreplace", newline="\n")


def _stdin_is_terminal() -> bool:
    try:
        return os.isatty(sys.stdin.fileno())
    except (AttributeError, ValueError, OSError):
        return False


def _check_decision_args(args) -> None:
    """approve's and reject's own argument rules, then the terminal check. Raises UsageError."""
    if args.command == "approve":
        if args.group is not None and args.names or args.group is None and len(args.names) != 3:
            raise UsageError("approve needs <prefix> <tool> <def_hash>, or --group <group id>")
        if args.group is None and args.upstream is not None:
            raise UsageError("--upstream goes with --group, not with one definition")
    else:
        if len(args.names) != 3:
            raise UsageError("reject needs <prefix> <tool> <def_hash>")
        if not fold_reason(args.reason):
            raise UsageError("reject needs --reason <text>")
    if not args.allow_no_terminal and not _stdin_is_terminal():
        raise UsageError(
            f"{args.command} needs a terminal; pass --allow-no-terminal if this is a script"
        )


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else list(argv)
    if argv[:1] != ["serve"]:
        _set_up_streams()
    try:
        args = _parser().parse_args(argv)
        if args.command == "serve":
            if args.config is None:
                raise UsageError("serve needs --config <absolute path to polarizer.toml>")
            if not os.path.isabs(args.config):
                raise UsageError(f"--config must be an absolute path, got {args.config}")
            return serve(Path(args.config))
        if (args.config is None) == (args.ledger_dir is None):
            raise UsageError(
                f"{args.command} needs exactly one of --config <absolute path> "
                "or --ledger-dir <absolute path>"
            )
        given = args.config if args.config is not None else args.ledger_dir
        if not os.path.isabs(given):
            flag = "--config" if args.config is not None else "--ledger-dir"
            raise UsageError(f"{flag} must be an absolute path, got {given}")
        if args.command in ("approve", "reject"):
            _check_decision_args(args)
    except UsageError as e:
        return _err(f"polarizer: {e}")
    if args.config is not None:
        try:
            cfg = config.load(Path(args.config), require_env=False)
        except config.ConfigError as e:
            return _err(str(e))
        ledger_dir, forbidden = cfg.ledger_dir, list(cfg.ledger_forbidden_paths)
    else:
        ledger_dir, forbidden = Path(args.ledger_dir), ledgerdir.always_forbidden()
    if args.command == "verify":
        return verify(ledger_dir, args.args)
    if args.command == "pending":
        return pending(ledger_dir, args.upstream)
    if args.command in ("approve", "reject"):
        return decide(args, ledger_dir, forbidden)
    return repair(ledger_dir, forbidden)


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


def pending(ledger_dir: Path, upstream: str | None) -> int:
    """Read-only, like verify: the definitions waiting for a decision."""
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
    state = pins.PinState()
    result = verify_bytes(data, head, state.apply)
    if result.status != "intact":
        for line in result.output_lines():
            print(line)
        return result.exit_code
    for line in pins.render(pins.blocks(state, ledger_dir, upstream)):
        print(line)
    return 0


def decide(args, ledger_dir: Path, forbidden: list[Path]) -> int:
    """approve (one or a group) and reject, after their argument checks."""
    try:
        warning = ledgerdir.check_location(ledger_dir, forbidden)
    except ledgerdir.ForbiddenPath as e:
        return _err(str(e))
    if warning:
        print(warning, file=sys.stderr)
    if args.command == "reject" or args.group is None:
        if not defhash.is_hash(args.names[2]):
            return _err(
                f"polarizer: {args.names[2]} is not a definition hash (64 lowercase hex characters)"
            )
    try:
        decider = Decider.open(ledger_dir)
    except Refusal as e:
        print(e.line, file=sys.stderr)
        return e.exit_code
    except OSError as e:
        return _err(f"polarizer: cannot open {e.filename or ledger_dir}: {e.strerror}")
    try:
        if args.command == "reject":
            prefix, tool, def_hash = args.names
            decider.reject(prefix, tool, def_hash, fold_reason(args.reason), print)
        elif args.group is not None:
            return decider.approve_group(
                args.group, args.upstream, print, lambda line: print(line, file=sys.stderr)
            )
        else:
            decider.approve_one(*args.names, print)
        return 0
    except Refusal as e:
        print(e.line, file=sys.stderr)
        return e.exit_code
    except writer.LedgerError as e:
        print(e.line, file=sys.stderr)
        return e.exit_code
    except OSError as e:
        print(f"polarizer: could not record the decision: {e.strerror or e}", file=sys.stderr)
        return 1
    finally:
        decider.close()


def repair(ledger_dir: Path, forbidden: list[Path]) -> int:
    try:
        warning = ledgerdir.check_location(ledger_dir, forbidden)
    except ledgerdir.ForbiddenPath as e:
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


def serve(config_path: Path) -> int:
    """polarizer serve (PROXY-SPEC.md, Startup): read the config, check ledger_dir's location,
    open the ledger, then serve MCP over stdio until the client closes stdin."""
    try:
        cfg = config.load(config_path)
    except config.ConfigError as e:
        return _err(str(e))
    try:
        warning = ledgerdir.check_location(cfg.ledger_dir, cfg.ledger_forbidden_paths)
    except ledgerdir.ForbiddenPath as e:
        return _err(str(e))
    if warning:
        print(warning, file=sys.stderr)
    state = pins.PinState()
    try:
        ledger = writer.LedgerWriter.open(cfg.ledger_dir, on_entry=state.apply)
    except writer.LedgerError as e:
        print(e.line, file=sys.stderr)
        return e.exit_code
    except OSError as e:
        return _err(f"polarizer: cannot open {e.filename or cfg.ledger_dir}: {e.strerror}")
    try:
        anyio.run(_serve, cfg, ledger, state)
    finally:
        ledger.close()
    return 0


async def _serve(cfg: config.Config, ledger: "writer.LedgerWriter", state: pins.PinState) -> None:
    from mcp import StdioServerParameters
    from mcp.server.stdio import stdio_server

    from polarizer.proxy import Gateway
    from polarizer.upstream import UpstreamSpec

    specs = [
        UpstreamSpec(
            prefix=u.prefix,
            target=StdioServerParameters(command=u.command, args=list(u.args), env=dict(u.env)),
            connect_timeout=u.connect_timeout_seconds,
        )
        for u in cfg.upstreams
    ]
    gateway = Gateway(specs, ledger, config_sha256=cfg.sha256, pins=state)
    async with gateway.running():
        async with stdio_server() as (read_stream, write_stream):
            await gateway.server.run(
                read_stream, write_stream, gateway.server.create_initialization_options()
            )
