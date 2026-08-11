#!/usr/bin/env bash
set -euo pipefail
API="${API:-http://127.0.0.1:8000}"
SAMPLE="${1:-}"

echo "Health:"
curl -sf "$API/health" | tee /tmp/media2text_health.json
echo

if [[ -z "$SAMPLE" ]]; then
  echo "Usage: $0 /path/to/sample.mp4"
  echo "Backend must be running. This script uploads and polls until ready."
  exit 0
fi

echo "Uploading $SAMPLE"
RESP=$(curl -sf -F "file=@${SAMPLE}" -F "topic=smoke test" "$API/api/videos/upload")
echo "$RESP"
ID=$(python3 -c "import json,sys; print(json.load(sys.stdin)['id'])" <<<"$RESP")

for i in $(seq 1 180); do
  ST=$(curl -sf "$API/api/videos/$ID/status")
  STATUS=$(python3 -c "import json,sys; print(json.load(sys.stdin)['status'])" <<<"$ST")
  PROG=$(python3 -c "import json,sys; print(json.load(sys.stdin)['progress'])" <<<"$ST")
  LABEL=$(python3 -c "import json,sys; print(json.load(sys.stdin)['stage_label'])" <<<"$ST")
  echo "[$i] $STATUS $PROG% $LABEL"
  if [[ "$STATUS" == "ready" ]]; then
    curl -sf "$API/api/videos/$ID/timeline" | head -c 500
    echo
    curl -sf "$API/api/videos/$ID/summaries" | head -c 500
    echo
    echo "SMOKE OK"
    exit 0
  fi
  if [[ "$STATUS" == "failed" ]]; then
    echo "$ST"
    echo "SMOKE FAILED"
    exit 1
  fi
  sleep 5
done

echo "Timeout waiting for ready"
exit 1
