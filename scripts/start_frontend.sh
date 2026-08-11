#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT/frontend"

if [[ ! -d node_modules ]]; then
  npm install
fi

echo "Starting Vite on 0.0.0.0:5173 (reachable from Windows HOST)"
exec npm run dev -- --host 0.0.0.0 --port 5173
