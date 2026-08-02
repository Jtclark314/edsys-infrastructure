#!/usr/bin/env bash
set -euo pipefail

stack_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$stack_dir"

for file in db_password valkey_tasks_password valkey_cache_password secret_key api_token_pepper_1 superuser_password sync_api_bearer export_api_bearer; do
  if [[ ! -s "/etc/edsys-secrets/netbox/$file" ]]; then
    echo "Missing required secret file: /etc/edsys-secrets/netbox/$file" >&2
    exit 1
  fi
done

docker compose config --quiet
docker compose pull
docker compose up -d --remove-orphans

deadline=$((SECONDS + 300))
until [[ $(docker inspect --format '{{.State.Health.Status}}' edsys-netbox-netbox-1 2>/dev/null || true) == healthy ]]; do
  if (( SECONDS >= deadline )); then
    docker compose ps
    docker compose logs --tail 120 netbox
    echo "NetBox did not become healthy within five minutes." >&2
    exit 1
  fi
  sleep 5
done

docker compose exec -T netbox /opt/netbox/netbox/manage.py check
docker compose exec -T netbox /opt/netbox/netbox/manage.py migrate --check
docker compose exec -T netbox /opt/netbox/netbox/manage.py shell \
  </srv/edsys/edsys-infrastructure/docker/netbox/scripts/bootstrap-rbac.py

docker compose ps
