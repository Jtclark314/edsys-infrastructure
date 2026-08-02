#!/usr/bin/env bash
set -euo pipefail

stack_dir=/srv/edsys/edsys-infrastructure/docker/netbox
backup_root=/var/backups/netbox
netbox_image='docker.io/netboxcommunity/netbox:v4.6.7-5.0.2@sha256:2e8cce924de85fc73da279f30034de747d036ebb674b9e86a8b28ac79461ea06'
run_id=$(date -u +%Y%m%dT%H%M%SZ)
staging="$backup_root/.staging-$run_id"
final="$backup_root/$run_id"

install -d -o root -g root -m 0700 "$backup_root" "$staging"
cd "$stack_dir"

ping_healthchecks() {
  [[ -n "${HC_PING_URL:-}" ]] || return 0
  local suffix="${1:+/$1}"
  curl -fsS --max-time 10 "${HC_PING_URL}${suffix}" >/dev/null || true
}
finish() {
  local rc=$?
  if (( rc == 0 )); then ping_healthchecks ""; else ping_healthchecks fail; fi
  exit "$rc"
}
trap finish EXIT
ping_healthchecks start

docker compose exec -T postgres pg_dump -U netbox -d netbox --format=custom --compress=9 \
  >"$staging/netbox.pgdump"

for volume in netbox-media netbox-scripts netbox-reports; do
  docker run --rm --read-only --user 0:0 --entrypoint /bin/tar \
    -v "edsys-netbox_${volume}:/source:ro" \
    -v "$staging:/backup" \
    "$netbox_image" \
    -C /source -czf "/backup/$volume.tar.gz" .
done

docker compose images --format json | jq -s . >"$staging/images.json"
docker compose config --images | sort -u >"$staging/image-identities.txt"
docker compose exec -T netbox /opt/netbox/netbox/manage.py shell --no-imports <<'PY' \
  | tail -n 1 >"$staging/object-counts.json"
import json
from django.apps import apps

result = {}
for model in apps.get_models():
    if model._meta.app_label in {"circuits", "dcim", "extras", "ipam", "tenancy", "virtualization", "wireless"}:
        try:
            result[f"{model._meta.app_label}.{model._meta.model_name}"] = model.objects.count()
        except Exception:
            pass
print(json.dumps(dict(sorted(result.items())), sort_keys=True))
PY
python3 -m json.tool "$staging/object-counts.json" >/dev/null

cp compose.yaml "$staging/compose.yaml"
cp -a configuration caddy env "$staging/"
{
  printf 'created_utc=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  printf 'hostname=%s\n' "$(hostname -f)"
  docker version --format 'docker_server={{.Server.Version}}'
  docker compose version --short | sed 's/^/compose=/'
  docker compose exec -T netbox awk -F'"' '/^version:/ { print $2; exit }' /opt/netbox/netbox/release.yaml | sed 's/^/netbox=/'
  docker compose exec -T netbox /opt/netbox/venv/bin/python /opt/netbox/netbox/manage.py --version | tail -n 1 | sed 's/^/django=/'
  docker compose exec -T postgres psql -U netbox -d netbox -Atc 'SHOW data_checksums' | sed 's/^/postgres_data_checksums=/'
} >"$staging/manifest.txt"
(cd "$staging" && find . -type f ! -name SHA256SUMS -print0 | sort -z | xargs -0 sha256sum) >"$staging/SHA256SUMS"
chmod -R go-rwx "$staging"
mv "$staging" "$final"
ln -sfn "$run_id" "$backup_root/current"

find "$backup_root" -mindepth 1 -maxdepth 1 -type d -name '20????????T??????Z' -mtime +35 -print0 \
  | xargs -0r --no-run-if-empty rm -rf --

printf '%s\n' "$final"
