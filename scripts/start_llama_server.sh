#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"

# shellcheck disable=SC1091
[[ -f "$ROOT/.env" ]] && set -a && source "$ROOT/.env" && set +a

BIN="${LLAMA_SERVER_BIN:-llama-server}"
HOST="${LLAMA_SERVER_HOST:-127.0.0.1}"
PORT="${LLAMA_SERVER_PORT:-8080}"
MODEL="${QWEN_VL_GGUF:-}"
MMPROJ="${QWEN_VL_MMPROJ:-}"
PID_FILE="${DATA_DIR:-$ROOT/data}/sqlite/llama-server.pid"

if [[ -z "$MODEL" || -z "$MMPROJ" ]]; then
  echo "Set QWEN_VL_GGUF and QWEN_VL_MMPROJ in .env"
  echo "Example HF repo: Qwen/Qwen3-VL-8B-Instruct-GGUF"
  exit 1
fi

if ! command -v "$BIN" >/dev/null 2>&1; then
  echo "llama-server not found in PATH (LLAMA_SERVER_BIN=$BIN)"
  exit 1
fi

mkdir -p "$(dirname "$PID_FILE")"
echo "Starting llama-server on ${HOST}:${PORT}"
echo "NOTE: Pipeline can auto-manage this when LLAMA_AUTO_MANAGE=true"

"$BIN" \
  -m "$MODEL" \
  --mmproj "$MMPROJ" \
  --host "$HOST" --port "$PORT" \
  -c 8192 -ngl 99 --flash-attn on &
echo $! > "$PID_FILE"
wait
