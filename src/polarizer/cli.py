"""The polarizer command line: serve, verify [--args] and repair (docs/PROXY-SPEC.md).

serve's stdout is the protocol channel: everything it says to the person goes to stderr.
verify and repair print results to stdout. Usage errors, config errors, unreadable files,
forbidden-path refusals and the git-tree warning go to stderr, one line each; every error
among them exits 2.
"""

import argparse
import os
import sys
from pathlib import Path

import anyio

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
    text = "run the proxy over stdio (started by the MCP client)"
    serve = commands.add_parser("serve", help=text, description=text)
    serve.add_argument("--config", metavar="PATH", help="absolute path to polarizer.toml")
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


def _set_up_streams() -> None:
    """UTF-8, backslashreplace and "\n" newlines on stdout and stderr, so every platform prints
    the same bytes. On native Windows the defaults would be the console or locale code page
    and "\r\n". Not for serve: its stdout is the protocol channel."""
    for stream in (sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="backslashreplace", newline="\n")


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
    try:
        ledger = writer.LedgerWriter.open(cfg.ledger_dir)
    except writer.LedgerError as e:
        print(e.line, file=sys.stderr)
        return e.exit_code
    except OSError as e:
        return _err(f"polarizer: cannot open {e.filename or cfg.ledger_dir}: {e.strerror}")
    try:
        anyio.run(_serve, cfg, ledger)
    finally:
        ledger.close()
    return 0


async def _serve(cfg: config.Config, ledger: "writer.LedgerWriter") -> None:
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
    gateway = Gateway(specs, ledger, config_sha256=cfg.sha256)
    async with gateway.running():
        async with stdio_server() as (read_stream, write_stream):
            await gateway.server.run(
                read_stream, write_stream, gateway.server.create_initialization_options()
            )
