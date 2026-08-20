#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="${ARENA_HERO_PYTHON:-$ROOT/.venv/bin/python}"
if [[ ! -x "$PYTHON" && -x "$ROOT/.venv-wsl/bin/python" ]]; then
  PYTHON="$ROOT/.venv-wsl/bin/python"
fi
if [[ ! -x "$PYTHON" ]]; then
  echo "Python virtual environment not found. Create .venv and install requirements.txt first." >&2
  exit 1
fi

cd "$ROOT"
export PYTHONUNBUFFERED=1
exec "$PYTHON" "$ROOT/arena_hero_dashboard_server.py" \
  --host "${ARENA_HERO_DASHBOARD_HOST:-127.0.0.1}" \
  --port "${ARENA_HERO_DASHBOARD_PORT:-8766}" \
  "$@"
