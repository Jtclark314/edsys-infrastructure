#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
SOURCE_DIR="${COURIER_SOURCE_DIR:-/home/jeremy/code/edsys-courier}"
VERSION="${COURIER_VERSION:-0.3.2}"

test -f "${SOURCE_DIR}/server/Dockerfile" || {
  echo "Courier source not found at ${SOURCE_DIR}" >&2
  exit 1
}
test -f /etc/edsys-courier/courier.env || {
  echo "Missing /etc/edsys-courier/courier.env" >&2
  exit 1
}

docker build --pull -t "edsys-courier-server:${VERSION}" "${SOURCE_DIR}/server"
export COURIER_VERSION="${VERSION}"
docker compose --env-file "${SCRIPT_DIR}/.env" -f "${SCRIPT_DIR}/compose.yaml" config --quiet
docker compose --env-file "${SCRIPT_DIR}/.env" -f "${SCRIPT_DIR}/compose.yaml" up -d

for _ in $(seq 1 30); do
  if curl -fsS http://127.0.0.1:3045/healthz >/dev/null; then
    docker compose --env-file "${SCRIPT_DIR}/.env" -f "${SCRIPT_DIR}/compose.yaml" ps
    exit 0
  fi
  sleep 1
done

docker compose --env-file "${SCRIPT_DIR}/.env" -f "${SCRIPT_DIR}/compose.yaml" logs --tail=100
exit 1
