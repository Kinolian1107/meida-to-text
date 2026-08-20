#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [[ ! -d .venv ]]; then
  python3 -m venv .venv
  # shellcheck disable=SC1091
  source .venv/bin/activate
  pip install -U pip
  pip install -e ".[dev]"
else
  # shellcheck disable=SC1091
  source .venv/bin/activate
fi

if [[ ! -f .env ]]; then
  cp .env.example .env
  echo "Created .env from .env.example — edit CLOUD_LLM_* / QWEN_VL_* as needed"
fi

# shellcheck disable=SC1091
set -a && source .env && set +a

export PYTHONPATH="$ROOT/backend:${PYTHONPATH:-}"
HOST="${API_HOST:-0.0.0.0}"
PORT="${API_PORT:-10002}"
echo "Starting API on http://${HOST}:${PORT} (WSL2: use Windows host IP / localhost forwarded)"
exec uvicorn app.main:app --host "$HOST" --port "$PORT" --reload
