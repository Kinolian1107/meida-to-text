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

cp "$UNIT_DIR/media2text-potprovider.service" /etc/systemd/system/media2text-potprovider.service
cp "$UNIT_DIR/media2text-backend.service" /etc/systemd/system/media2text-backend.service
cp "$UNIT_DIR/media2text-frontend.service" /etc/systemd/system/media2text-frontend.service

# The PO Token provider container may predate this unit and carry its own
# restart policy; drop it so systemd is the only thing managing the container.
if docker inspect bgutil-provider >/dev/null 2>&1; then
  docker update --restart=no bgutil-provider >/dev/null 2>&1 || true
fi

systemctl daemon-reload
systemctl enable --now \
  media2text-potprovider.service \
  media2text-backend.service \
  media2text-frontend.service

echo "Installed. Check status with:"
echo "  systemctl status media2text-potprovider.service media2text-backend.service media2text-frontend.service"

echo
echo "WSL systemd only starts after the distro itself is running."
WIN_PS1="$(wslpath -w "$ROOT/scripts/windows/register-wsl-autostart.ps1")"
POWERSHELL=""
for candidate in \
  /mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe \
  /mnt/c/Windows/System32/powershell.exe; do
  if [[ -x "$candidate" ]]; then
    POWERSHELL="$candidate"
    break
  fi
done
echo "Register the Windows logon task from a WSL shell (no sudo / no Administrator):"
echo "  powershell.exe -NoProfile -ExecutionPolicy Bypass -File \"$WIN_PS1\""
if [[ -n "$POWERSHELL" ]]; then
  echo "Registering Windows logon autostart now..."
  "$POWERSHELL" -NoProfile -ExecutionPolicy Bypass -File "$WIN_PS1" \
    || echo "Windows autostart registration failed; run the command above from a WSL shell (not via sudo)."
fi
