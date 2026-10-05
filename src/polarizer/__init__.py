"""Polarizer: a local proxy between an AI agent and its MCP servers, with a verifiable ledger."""

from importlib import metadata

# One version source: pyproject.toml, read from the installed package's metadata
# (docs/MEASURE-SPEC.md, section 16, question 8).
try:
    __version__ = metadata.version("polarizer")
except metadata.PackageNotFoundError:  # run from a source tree that was never installed
    __version__ = "unknown"
