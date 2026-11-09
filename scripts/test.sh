#!/usr/bin/env sh
# Linux/macOS counterpart of scripts/test.ps1. Run `uv sync --locked --extra dev` first.
set -eu
cd "$(dirname "$0")/.."
.venv/bin/python -m pytest -q
.venv/bin/ruff check .
.venv/bin/ruff format --check .
