#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# shellcheck disable=SC1091
source "$ROOT/.venv/bin/activate"
pip install -U yt-dlp
python -c "import yt_dlp; print('yt-dlp', yt_dlp.version.__version__)"
