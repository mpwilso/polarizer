#!/usr/bin/env bash
# The checks CI runs, exactly: ruff (lint and format), the docs check, then pytest.
set -euo pipefail
cd "$(dirname "$0")/.."
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked python scripts/check_docs.py
uv run --locked pytest
