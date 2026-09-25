#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root/apps/api"

uv run ruff check .
uv run ruff format --check .
uv run ty check
uv run pytest
