#!/usr/bin/env bash
# Called from Windows at logon (see scripts/windows/register-wsl-autostart.ps1).
# systemd units start when this distro boots, but they do not keep the WSL
# instance alive; this process does. Also wait for systemd before start, so a
# cold Windows logon does not race Docker / multi-user.target.
set -u

for _ in $(seq 1 90); do
  case "$(systemctl is-system-running 2>/dev/null || true)" in
    running|degraded) break ;;
  esac
  sleep 1
done

systemctl start \
  media2text-potprovider.service \
  media2text-backend.service \
  media2text-frontend.service \
  || true

exec /bin/sleep infinity
