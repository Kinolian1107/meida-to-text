#!/usr/bin/env bash
# Install media2text backend/frontend as system-level systemd services so they
# start automatically whenever this WSL distro boots (systemd=true in /etc/wsl.conf).
# Must be run with sudo: sudo ./scripts/systemd/install.sh
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
  echo "Run with sudo: sudo $0" >&2
  exit 1
fi

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
UNIT_DIR="$ROOT/scripts/systemd"

cp "$UNIT_DIR/media2text-backend.service" /etc/systemd/system/media2text-backend.service
cp "$UNIT_DIR/media2text-frontend.service" /etc/systemd/system/media2text-frontend.service

systemctl daemon-reload
systemctl enable --now media2text-backend.service media2text-frontend.service

echo "Installed. Check status with:"
echo "  systemctl status media2text-backend.service media2text-frontend.service"
