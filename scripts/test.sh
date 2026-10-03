#!/usr/bin/env bash
# The checks CI runs, exactly: ruff (lint and format), then pytest. Run from anywhere.
set -euo pipefail
cd "$(dirname "$0")/.."
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked pytest
