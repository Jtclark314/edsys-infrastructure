#!/usr/bin/env bash
set -euo pipefail

OUTPUT_ROOT="${1:-/tmp/edsys-audits}"
AUDIT_ID="${2:-docker-$(date +%Y%m%d-%H%M%S)}"
OUT_DIR="${OUTPUT_ROOT}/${AUDIT_ID}"
mkdir -p "$OUT_DIR"

if ! command -v docker >/dev/null 2>&1; then
  echo "docker is not available on this host" | tee "${OUT_DIR}/docker-not-available.txt"
  exit 0
fi

{
  echo "audit_id=${AUDIT_ID}"
  echo "started_at=$(date -Is)"
  echo "safety=read-only Docker metadata; no env vars; no secrets; no logs"
} > "${OUT_DIR}/metadata.txt"

docker ps --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}' > "${OUT_DIR}/docker-ps.txt" 2>&1 || true
docker network ls > "${OUT_DIR}/docker-networks.txt" 2>&1 || true
docker volume ls > "${OUT_DIR}/docker-volumes_REVIEW_BEFORE_COMMIT.txt" 2>&1 || true

if docker compose version >/dev/null 2>&1; then
  docker compose ls > "${OUT_DIR}/docker-compose-ls.txt" 2>&1 || true
fi

docker ps -q | while read -r id; do
  [ -z "$id" ] && continue
  name="$(docker inspect --format '{{.Name}}' "$id" | sed 's#^/##')"
  docker inspect --format 'Name={{.Name}}
Image={{.Config.Image}}
Mounts={{range .Mounts}}{{.Source}} -> {{.Destination}}; {{end}}
Networks={{range $name, $_ := .NetworkSettings.Networks}}{{$name}} {{end}}
Labels={{range $key, $value := .Config.Labels}}{{$key}}={{$value}}; {{end}}' "$id" \
    > "${OUT_DIR}/inspect-${name}-sanitized.txt" 2>&1 || true
done

echo "Docker baseline written to: ${OUT_DIR}"
echo "Review output before copying any content into Git."
