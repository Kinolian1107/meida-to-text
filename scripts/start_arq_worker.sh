#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

# shellcheck disable=SC1091
source .venv/bin/activate
# shellcheck disable=SC1091
[[ -f .env ]] && set -a && source .env && set +a

if [[ -z "${REDIS_URL:-}" ]]; then
  echo "Set REDIS_URL and JOB_QUEUE_BACKEND=arq in .env first"
  exit 1
fi

pip install -e ".[queue]" >/dev/null
export PYTHONPATH="$ROOT/backend:${PYTHONPATH:-}"
exec arq app.workers.arq_worker.WorkerSettings
