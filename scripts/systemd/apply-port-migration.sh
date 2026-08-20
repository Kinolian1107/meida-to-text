#!/usr/bin/env bash
set -euo pipefail

if [[ ${EUID} -ne 0 ]]; then
  exec sudo "$0" "$@"
fi

MEDIA_ROOT="/home/kino/git/media2text"
HERMES_ROOT="/home/kino/git/hermes-daily-research"
LAZYBUN_ROOT="/home/kino/git/LazyBun"
GLOBAL_SOURCE_ROOT="/home/kino/git/docker_postgres"
GLOBAL_ROOT="/opt/docker/postgres"
GLOBAL_COMPOSE="${GLOBAL_ROOT}/docker-compose.yml"
GLOBAL_ENV="${GLOBAL_ROOT}/.env"
COMBINED_COMPOSE="/opt/docker/docker-compose.yml"
COMBINED_ENV="/opt/docker/.env"
POSTGRES_CLIENT_IMAGE="docker.io/library/postgres:17.8-alpine3.23"
HERMES_STOPPED=false

resume_hermes_on_error() {
  local exit_code=$?
  trap - ERR
  docker compose \
    --project-directory "${LAZYBUN_ROOT}" \
    -f "${LAZYBUN_ROOT}/docker-compose.yml" \
    up -d postgres redis >/dev/null 2>&1 || true
  if [[ -f "${GLOBAL_COMPOSE}" && -f "${GLOBAL_ENV}" ]]; then
    docker compose \
      --project-name postgres \
      --project-directory "${GLOBAL_ROOT}" \
      --env-file "${GLOBAL_ENV}" \
      -f "${GLOBAL_COMPOSE}" \
      up -d postgres >/dev/null 2>&1 || true
  fi
  if [[ "${HERMES_STOPPED}" == "true" ]]; then
    docker compose \
      --project-directory "${HERMES_ROOT}" \
      --env-file "${HERMES_ROOT}/.env.admin" \
      -f "${HERMES_ROOT}/docker-compose.yml" \
      up -d postgres >/dev/null 2>&1 || true
    systemctl start \
      hermes-daily-research-web.service \
      hermes-daily-research-worker.service >/dev/null 2>&1 || true
  fi
  echo "Port migration stopped at a failed checkpoint; inspect the error above." >&2
  exit "${exit_code}"
}
trap resume_hermes_on_error ERR

wait_for_container() {
  local container="$1"
  local health
  for _ in $(seq 1 60); do
    health="$(docker inspect \
      --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' \
      "${container}" 2>/dev/null || true)"
    if [[ "${health}" == "healthy" || "${health}" == "running" ]]; then
      return 0
    fi
    sleep 1
  done
  echo "Container ${container} did not become ready." >&2
  return 1
}

wait_for_http() {
  local url="$1"
  for _ in $(seq 1 60); do
    if curl -fsS -m 2 "${url}" >/dev/null 2>&1; then
      return 0
    fi
    sleep 1
  done
  echo "HTTP endpoint ${url} did not become ready." >&2
  return 1
}

verify_postgres_password() {
  local port="$1"
  local database="$2"
  local password="$3"
  printf '%s\n' "${password}" |
    docker run --rm -i --network host \
      --entrypoint sh \
      "${POSTGRES_CLIENT_IMAGE}" \
      -c '
        IFS= read -r PGPASSWORD
        export PGPASSWORD
        test "$(
          psql -h 127.0.0.1 -p "$1" -U postgres -d "$2" -tAc "SELECT 1"
        )" = "1"
      ' sh "${port}" "${database}"
}

verify_redis_password() {
  local container="$1"
  local password="$2"
  printf '%s\n' "${password}" |
    docker exec -i "${container}" sh -c '
      IFS= read -r REDISCLI_AUTH
      export REDISCLI_AUTH
      test "$(redis-cli --no-auth-warning ping)" = "PONG"
    '
}

require_listener() {
  local port="$1"
  if ! ss -H -ltn "sport = :${port}" |
    awk 'NR { found = 1 } END { exit !found }'; then
    echo "Expected TCP port ${port} is not listening." >&2
    return 1
  fi
}

require_no_listener() {
  local port="$1"
  if ss -H -ltn "sport = :${port}" |
    awk 'NR { found = 1 } END { exit !found }'; then
    echo "Legacy TCP port ${port} is still listening." >&2
    return 1
  fi
}

# Validate all user-owned sources before touching a running service.
for command in docker openssl python3 ss systemctl systemd-analyze; do
  command -v "${command}" >/dev/null
done
docker image inspect "${POSTGRES_CLIENT_IMAGE}" >/dev/null
docker compose \
  --project-directory "${LAZYBUN_ROOT}" \
  -f "${LAZYBUN_ROOT}/docker-compose.yml" \
  config --quiet
docker compose \
  --project-directory "${HERMES_ROOT}" \
  --env-file "${HERMES_ROOT}/.env.admin" \
  -f "${HERMES_ROOT}/docker-compose.yml" \
  config --quiet
python3 - <<PY
from pathlib import Path
import yaml

yaml.safe_load(
    Path("${GLOBAL_SOURCE_ROOT}/postgres-docker-compose.yml").read_text(
        encoding="utf-8"
    )
)
PY
systemd-analyze verify \
  "${MEDIA_ROOT}/scripts/systemd/media2text-potprovider.service" \
  "${MEDIA_ROOT}/scripts/systemd/media2text-backend.service" \
  "${MEDIA_ROOT}/scripts/systemd/media2text-frontend.service" \
  "${HERMES_ROOT}/deploy/hermes-daily-research-web.service"

# Move LazyBun first so its former 15432 mapping cannot block global PostgreSQL.
docker compose \
  --project-directory "${LAZYBUN_ROOT}" \
  -f "${LAZYBUN_ROOT}/docker-compose.yml" \
  up -d --force-recreate postgres redis
wait_for_container lazybun-postgres-1
wait_for_container lazybun-redis-1
(
  set -a
  # shellcheck disable=SC1091
  source "${LAZYBUN_ROOT}/.env"
  set +a
  verify_postgres_password 15434 lazybun "${POSTGRES_PASSWORD}"
  verify_redis_password lazybun-redis-1 "${REDIS_PASSWORD}"
)

# Recreate the existing Compose project with its original project name and data dir.
install -d -m 0755 "${GLOBAL_ROOT}"
install -m 0644 \
  "${GLOBAL_SOURCE_ROOT}/postgres-docker-compose.yml" \
  "${GLOBAL_COMPOSE}"
GLOBAL_POSTGRES_PASSWORD="$(openssl rand -hex 32)"
printf "ALTER ROLE postgres WITH PASSWORD '%s';\n" "${GLOBAL_POSTGRES_PASSWORD}" |
  docker exec -i postgres \
    psql -U postgres -d postgres -v ON_ERROR_STOP=1 >/dev/null
umask 077
printf 'POSTGRES_PASSWORD=%s\nPOSTGRES_USER=postgres\nPOSTGRES_DB=postgres\n' \
  "${GLOBAL_POSTGRES_PASSWORD}" >"${GLOBAL_ENV}"
chown root:root "${GLOBAL_ENV}"
chmod 0600 "${GLOBAL_ENV}"
docker compose \
  --project-name postgres \
  --project-directory "${GLOBAL_ROOT}" \
  --env-file "${GLOBAL_ENV}" \
  -f "${GLOBAL_COMPOSE}" \
  up -d --force-recreate postgres
wait_for_container postgres
verify_postgres_password 15432 postgres "${GLOBAL_POSTGRES_PASSWORD}"

# Hermes host processes read the host-mapped DB port, so stop them only for
# the short database recreation window.
systemctl stop \
  hermes-daily-research-web.service \
  hermes-daily-research-worker.service
HERMES_STOPPED=true
docker compose \
  --project-directory "${HERMES_ROOT}" \
  --env-file "${HERMES_ROOT}/.env.admin" \
  -f "${HERMES_ROOT}/docker-compose.yml" \
  up -d --force-recreate postgres
wait_for_container hermes-daily-research-postgres-1

# Install service definitions only after all database checkpoints pass.
install -m 0644 \
  "${MEDIA_ROOT}/scripts/systemd/media2text-potprovider.service" \
  /etc/systemd/system/media2text-potprovider.service
install -m 0644 \
  "${MEDIA_ROOT}/scripts/systemd/media2text-backend.service" \
  /etc/systemd/system/media2text-backend.service
install -m 0644 \
  "${MEDIA_ROOT}/scripts/systemd/media2text-frontend.service" \
  /etc/systemd/system/media2text-frontend.service
install -m 0644 \
  "${HERMES_ROOT}/deploy/hermes-daily-research-web.service" \
  /etc/systemd/system/hermes-daily-research-web.service
install -d -m 0755 /etc/systemd/system/ollama.service.d
install -m 0644 \
  "${MEDIA_ROOT}/scripts/systemd/ollama-lan.conf" \
  /etc/systemd/system/ollama.service.d/lan.conf

systemctl daemon-reload
systemctl enable \
  media2text-potprovider.service \
  media2text-backend.service \
  media2text-frontend.service \
  hermes-daily-research-web.service \
  hermes-daily-research-worker.service

# Explicit restart is required: enable --now leaves already-active units on
# their old ExecStart command and therefore on their old ports.
systemctl restart ollama.service
systemctl restart media2text-potprovider.service
wait_for_container bgutil-provider
systemctl restart media2text-backend.service
systemctl restart media2text-frontend.service
systemctl restart \
  hermes-daily-research-web.service \
  hermes-daily-research-worker.service
HERMES_STOPPED=false

wait_for_http http://127.0.0.1:14416/ping
wait_for_http http://127.0.0.1:10002/health
wait_for_http http://127.0.0.1:10001/
wait_for_http http://127.0.0.1:10003/
wait_for_http http://127.0.0.1:11434/api/tags

for port in 10001 10002 10003 11434 14416 15432 15433 15434 16379 18790 18793; do
  require_listener "${port}"
done
for port in 4416 5173 5432 5433 8000 8088 18791; do
  require_no_listener "${port}"
done

# Keep the older combined compose definition from reintroducing host port 5432
# if it is used later; it is not the owner of the running postgres container.
if [[ -f "${COMBINED_COMPOSE}" ]]; then
  COMBINED_COMPOSE="${COMBINED_COMPOSE}" python3 - <<'PY'
import os
from pathlib import Path

path = Path(os.environ["COMBINED_COMPOSE"])
text = path.read_text(encoding="utf-8")
old = '      - "5432:5432"'
new = '      - "127.0.0.1:15432:5432"'
if old in text:
    path.write_text(text.replace(old, new, 1), encoding="utf-8")
PY
fi
if [[ -f "${COMBINED_ENV}" ]]; then
  chown root:root "${COMBINED_ENV}"
  chmod 0600 "${COMBINED_ENV}"
  COMBINED_ENV="${COMBINED_ENV}" \
    GLOBAL_POSTGRES_PASSWORD="${GLOBAL_POSTGRES_PASSWORD}" python3 - <<'PY'
import os
from pathlib import Path

path = Path(os.environ["COMBINED_ENV"])
lines = path.read_text(encoding="utf-8").splitlines()
replacement = f"POSTGRES_PASSWORD={os.environ['GLOBAL_POSTGRES_PASSWORD']}"
for index, line in enumerate(lines):
    if line.startswith("POSTGRES_PASSWORD="):
        lines[index] = replacement
        break
else:
    lines.append(replacement)
path.write_text("\n".join(lines) + "\n", encoding="utf-8")
PY
fi

trap - ERR
echo "WSL service migration complete."
echo "Run scripts/windows/configure-mirrored-firewall.ps1 as Windows Administrator next."
