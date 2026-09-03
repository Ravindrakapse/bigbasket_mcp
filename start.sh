#!/usr/bin/env bash
# Launch the BigBasket MCP server, installing dependencies on first run.
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"

if ! command -v uv >/dev/null 2>&1; then
  echo "uv is required: https://docs.astral.sh/uv/getting-started/installation/" >&2
  exit 1
fi

uv sync --quiet
uv run playwright install chromium --with-deps >/dev/null 2>&1 || uv run playwright install chromium
exec uv run main.py
