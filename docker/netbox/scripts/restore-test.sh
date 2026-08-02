#!/usr/bin/env bash
set -euo pipefail

stack_dir=/srv/edsys/edsys-infrastructure/docker/netbox
secret_dir=/etc/edsys-secrets/netbox
backup_root=/var/backups/netbox
source_dir=${1:-$(readlink -f "$backup_root/current")}
suffix=$$
network="edsys-netbox-restore-$suffix"
postgres="edsys-netbox-restore-postgres-$suffix"
tasks="edsys-netbox-restore-tasks-$suffix"
cache="edsys-netbox-restore-cache-$suffix"
test_dir=$(mktemp -d /var/tmp/netbox-restore-test.XXXXXX)
netbox_image='docker.io/netboxcommunity/netbox:v4.6.7-5.0.2@sha256:2e8cce924de85fc73da279f30034de747d036ebb674b9e86a8b28ac79461ea06'
postgres_image='docker.io/postgres:18-alpine@sha256:b6a16ed0eb96e2c362811f7eeb951eac8b459e7b40be4149ea5444aa7c65569b'
valkey_image='docker.io/valkey/valkey:9.1-alpine@sha256:3fe38a705227d29534a199e876b38d5474dec4d3baca980ac6894df539416562'

ping_healthchecks() {
  [[ -n "${HC_PING_URL:-}" ]] || return 0
  local suffix_path="${1:+/$1}"
  curl -fsS --max-time 10 "${HC_PING_URL}${suffix_path}" >/dev/null || true
}
cleanup() {
  local rc=$?
  docker rm -f "$postgres" "$tasks" "$cache" >/dev/null 2>&1 || true
  docker network rm "$network" >/dev/null 2>&1 || true
  rm -rf "$test_dir"
  if (( rc == 0 )); then ping_healthchecks ""; else ping_healthchecks fail; fi
  exit "$rc"
}
trap cleanup EXIT
ping_healthchecks start

[[ -f "$source_dir/netbox.pgdump" ]] || { echo "Missing PostgreSQL dump" >&2; exit 1; }
(cd "$source_dir" && sha256sum -c SHA256SUMS)
for archive in netbox-media.tar.gz netbox-scripts.tar.gz netbox-reports.tar.gz; do
  tar -tzf "$source_dir/$archive" >/dev/null
done

docker network create --internal "$network" >/dev/null
docker run -d --name "$postgres" --network "$network" --network-alias postgres \
  --security-opt no-new-privileges:true \
  -e POSTGRES_DB=netbox -e POSTGRES_USER=netbox \
  -e POSTGRES_PASSWORD_FILE=/run/secrets/db_password \
  -e POSTGRES_INITDB_ARGS=--data-checksums \
  -v "$secret_dir/db_password:/run/secrets/db_password:ro" \
  "$postgres_image" >/dev/null

docker run -d --name "$tasks" --network "$network" --network-alias valkey-tasks \
  --read-only --user 0:0 --cap-drop ALL --security-opt no-new-privileges:true \
  --tmpfs /data:rw,noexec,nosuid,nodev,size=64m \
  -v "$secret_dir/valkey_tasks_password:/run/secrets/redis_password:ro" \
  "$valkey_image" /bin/sh -ec 'exec valkey-server --save "" --appendonly no --requirepass "$(cat /run/secrets/redis_password)"' >/dev/null

docker run -d --name "$cache" --network "$network" --network-alias valkey-cache \
  --read-only --user 0:0 --cap-drop ALL --security-opt no-new-privileges:true \
  --tmpfs /data:rw,noexec,nosuid,nodev,size=64m \
  -v "$secret_dir/valkey_cache_password:/run/secrets/redis_cache_password:ro" \
  "$valkey_image" /bin/sh -ec 'exec valkey-server --save "" --appendonly no --requirepass "$(cat /run/secrets/redis_cache_password)"' >/dev/null

for _ in $(seq 1 60); do
  docker exec "$postgres" pg_isready -U netbox -d netbox >/dev/null 2>&1 && break
  sleep 1
done
docker exec "$postgres" pg_isready -U netbox -d netbox >/dev/null
docker cp "$source_dir/netbox.pgdump" "$postgres:/tmp/netbox.pgdump"
docker exec "$postgres" pg_restore -U netbox -d netbox --exit-on-error /tmp/netbox.pgdump
docker exec "$postgres" pg_amcheck -U netbox -d netbox --install-missing
docker exec -i "$postgres" psql -U netbox -d netbox -v ON_ERROR_STOP=1 -At <<'SQL' >"$test_dir/assertions.txt"
SELECT 'data_checksums=' || current_setting('data_checksums');
SELECT 'devices=' || count(*) FROM dcim_device;
SELECT 'inventory_items=' || count(*) FROM dcim_inventoryitem;
SELECT 'ip_addresses=' || count(*) FROM ipam_ipaddress;
SELECT 'virtual_machines=' || count(*) FROM virtualization_virtualmachine;
SELECT 'services=' || count(*) FROM ipam_service;
SELECT 'netbox_vm=' || count(*) FROM virtualization_virtualmachine WHERE name='netbox';
SQL

grep -qx 'data_checksums=on' "$test_dir/assertions.txt"
awk -F= '$1 != "data_checksums" && $2 + 0 < 1 { bad=1 } END { exit bad }' "$test_dir/assertions.txt"

netbox_args=(
  --rm --network "$network" --user netbox:root --read-only
  --cap-drop ALL --security-opt no-new-privileges:true
  --tmpfs "/tmp:rw,noexec,nosuid,nodev,size=128m"
  --env-file "$stack_dir/env/netbox.env"
  -v "$stack_dir/configuration:/etc/netbox/config:ro"
  -v "$secret_dir/db_password:/run/secrets/db_password:ro"
  -v "$secret_dir/valkey_tasks_password:/run/secrets/redis_password:ro"
  -v "$secret_dir/valkey_cache_password:/run/secrets/redis_cache_password:ro"
  -v "$secret_dir/secret_key:/run/secrets/secret_key:ro"
  -v "$secret_dir/api_token_pepper_1:/run/secrets/api_token_pepper_1:ro"
  -v "$secret_dir/superuser_password:/run/secrets/superuser_password:ro"
  --entrypoint /opt/netbox/venv/bin/python
  "$netbox_image" /opt/netbox/netbox/manage.py
)
docker run "${netbox_args[@]}" migrate --check
docker run "${netbox_args[@]}" check
docker run "${netbox_args[@]}" shell -c \
  'from dcim.models import Device; from virtualization.models import VirtualMachine; from ipam.models import Service; assert Device.objects.exists(); assert VirtualMachine.objects.filter(name="netbox").exists(); assert Service.objects.exists(); print("representative_orm_checks=passed")'

printf 'NetBox isolated restore passed: %s\n' "$(tr '\n' ' ' <"$test_dir/assertions.txt")"
