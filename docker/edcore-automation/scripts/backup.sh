#!/usr/bin/env bash
set -Eeuo pipefail

readonly stack_dir=/srv/edsys/edsys-infrastructure/docker/edcore-automation
readonly backup_root=/var/backups/edcore-automation
readonly secret_root=/etc/edsys-secrets/edcore-automation
readonly secret_escrow_root=/var/backups/edcore-automation-secret-escrow
readonly secret_escrow_acceptance=/etc/edsys-escrow/edcore-automation-accepted.json
readonly mosquitto_image='docker.io/library/eclipse-mosquitto:2.1.2-alpine@sha256:6f8d8a947c506f8a2290ec65cd4bd2bc7cb4d43fb5f6271f861cb013e2ef9797'
readonly node_red_image='localhost/edsys/edcore-automation-node-red:4.1.13-1'
readonly influx_image='docker.io/library/influxdb:2.8.0@sha256:09a5361809c771d863bcfa844a09598a82a6d9bbba1c9a9e2fa312e310572a14'
readonly data_network=edsys-edcore-automation-data
readonly healthchecks_env=/etc/edsys-secrets/edcore-automation/healthchecks/backup.env
run_id=$(date -u +%Y%m%dT%H%M%SZ)
readonly run_id
readonly staging=$backup_root/.staging-$run_id
readonly final=$backup_root/$run_id

[[ ${EUID} -eq 0 ]] || { echo "Run as root on edcore-automation." >&2; exit 1; }
[[ $(hostname -s) == edcore-automation ]] || { echo "Refusing to back up the wrong guest." >&2; exit 1; }
/usr/local/sbin/edsys-automation-source-guard --runtime
[[ ! -e "$staging" && ! -e "$final" ]] || { echo "Backup run ID collision." >&2; exit 1; }
install -d -o root -g root -m 0700 "$backup_root" "$staging"
cd "$stack_dir"

ping_healthchecks() {
  [[ -n ${HC_PING_URL:-} ]] || return 0
  local state=${1:-} suffix='' url=$HC_PING_URL
  case "$state" in
    "") ;;
    start|fail) suffix=/$state ;;
    *) echo "Invalid Healthchecks state." >&2; return 1 ;;
  esac
  [[ -f $healthchecks_env && ! -L $healthchecks_env && \
     $(stat -c '%u:%g:%a' "$healthchecks_env") == 0:0:600 ]] || {
    echo "Healthchecks environment must be root:root mode 0600." >&2
    return 1
  }
  [[ $url =~ ^https://[A-Za-z0-9._~:/?#@!\$\&\(\)\*+,\;=%-]+$ ]] || {
    echo "Healthchecks URL has an unsafe form." >&2
    return 1
  }
  env -u HC_PING_URL curl --config - >/dev/null 2>&1 <<EOF || true
fail
silent
show-error
max-time = 10
url = "${url}${suffix}"
EOF
}
cleanup() {
  local rc=$?
  if (( rc != 0 )); then
    rm -rf -- "$staging"
    ping_healthchecks fail
  else
    ping_healthchecks ""
  fi
  exit "$rc"
}
trap cleanup EXIT
ping_healthchecks start

for service in mosquitto influxdb automation-runtime node-red telegraf; do
  container=$(docker compose ps -q "$service")
  [[ -n "$container" && $(docker inspect --format '{{.State.Status}}' "$container") == running ]] || {
    echo "$service is not running; refusing an incomplete backup." >&2
    exit 1
  }
done

install -d -m 0700 "$staging/config" "$staging/config/runtime" "$staging/influxdb-backup" \
  "$staging/secret-escrow" "$staging/custody-evidence"
cp compose.yaml compose.bootstrap.yaml service-definition.yml .env "$staging/config/"
cp -a mosquitto node-red telegraf "$staging/config/"
cp -a runtime/config "$staging/config/runtime/"

# The ordinary application backup never reads plaintext runtime secrets. It
# carries the last independently cold-tested age ciphertext so the off-guest
# pull and encrypted Restic set contain the complete recovery chain.
secret_escrow=$(readlink -e "$secret_escrow_root/current")
[[ -f $secret_escrow && ! -L $secret_escrow && $secret_escrow == "$secret_escrow_root"/*.tar.age ]] || {
  echo "Accepted encrypted secret escrow is absent." >&2
  exit 1
}
[[ $(stat -c '%u:%g:%a' "$secret_escrow") == 0:0:600 ]] || {
  echo "Encrypted secret escrow must be root:root mode 0600." >&2
  exit 1
}
grep -aq '^age-encryption.org/v1' "$secret_escrow" || { echo "Secret escrow is not a native age archive." >&2; exit 1; }
[[ -f $secret_escrow_acceptance && ! -L $secret_escrow_acceptance && \
   $(stat -c '%u:%g:%a' "$secret_escrow_acceptance") == 0:0:600 ]] || {
  echo "Cold-restore acceptance for secret escrow is absent or unsafe." >&2
  exit 1
}
jq -e --arg name "$(basename "$secret_escrow")" \
  --arg hash "$(sha256sum "$secret_escrow" | awk '{print $1}')" '
    (keys | sort) == (["archive_name","archive_sha256","schema","tested_on","tested_utc"] | sort)
    and .schema == "edsys.edcore-automation.secret-escrow-acceptance.v1"
    and .archive_name == $name and .archive_sha256 == $hash
    and (.tested_on | type == "string" and length > 0 and . != "edcore-automation")
  ' "$secret_escrow_acceptance" >/dev/null || {
  echo "Secret escrow does not match its 9950x cold-restore acceptance." >&2
  exit 1
}
cp "$secret_escrow" "$staging/secret-escrow/$(basename "$secret_escrow")"
chmod 0600 "$staging/secret-escrow/$(basename "$secret_escrow")"
cp "$secret_escrow_acceptance" "$staging/secret-escrow/ACCEPTANCE.json"
chmod 0600 "$staging/secret-escrow/ACCEPTANCE.json"

# Preserve the non-secret, root-only custody proof chain beside the encrypted
# escrow. These exact evidence files contain hashes/status only, never keys.
custody_files=(
  /etc/edsys-escrow/client-delivery/homeassistant.json
  /etc/edsys-escrow/client-delivery/frigate.json
  /etc/edsys-escrow/client-disposition/edsys-edge-livingroom-ingestion.json
  /etc/edsys-escrow/client-disposition/edsys-edge-livingroom.json
  /etc/edsys-escrow/online-keys-finalized.json
)
for evidence in "${custody_files[@]}"; do
  [[ -f $evidence && ! -L $evidence && $(stat -c '%u:%g:%a' "$evidence") == 0:0:600 ]] || {
    echo "Custody evidence is absent or unsafe: $evidence" >&2
    exit 1
  }
  cp "$evidence" "$staging/custody-evidence/$(basename "$evidence")"
done
chmod 0600 "$staging"/custody-evidence/*
docker compose config --images | sort -u >"$staging/image-identities.txt"
# Compose releases have emitted either one JSON array or one JSON object per
# line. Normalize both shapes before sorting so a CLI format change cannot
# prevent an otherwise valid backup.
docker compose images --format json | jq -s '
  if length == 1 and (.[0] | type) == "array" then .[0] else . end
  | sort_by(.Service)
' >"$staging/images.json"

# Force a persistence checkpoint before archiving the broker's named volume.
docker compose kill -s USR1 mosquitto >/dev/null
sleep 2
mosquitto_container=$(docker compose ps -q mosquitto)
mosquitto_volume=$(docker inspect --format '{{range .Mounts}}{{if eq .Destination "/mosquitto/data"}}{{.Name}}{{end}}{{end}}' "$mosquitto_container")
[[ -n "$mosquitto_volume" ]] || { echo "Unable to resolve Mosquitto volume." >&2; exit 1; }
docker run --rm --read-only --user 0:0 --entrypoint /bin/tar \
  -v "$mosquitto_volume:/source:ro" -v "$staging:/backup" "$mosquitto_image" \
  -C /source -czf /backup/mosquitto-data.tar.gz .
tar -tzf "$staging/mosquitto-data.tar.gz" >/dev/null

# A dirty Node-RED Project is an unreviewed production change, not a backup
# source. Runtime-only Project settings/sessions are excluded because they can
# contain the credential key or active sessions and are regenerated from the
# root-owned secret at restore.
docker compose exec -T node-red sh -ec '
  test -d /data/projects/edcore-automation/.git
  test -z "$(git -C /data/projects/edcore-automation status --porcelain)"
  git -C /data/projects/edcore-automation bundle create /tmp/edcore-automation.bundle --all
'
node_red_container=$(docker compose ps -q node-red)
docker compose exec -T node-red cat /tmp/edcore-automation.bundle \
  >"$staging/node-red-project.bundle"
docker compose exec -T node-red rm -f /tmp/edcore-automation.bundle
bundle_verify_repo="$staging/.bundle-verify.git"
git -c init.defaultBranch=main init --bare "$bundle_verify_repo" >/dev/null
git -C "$bundle_verify_repo" bundle verify "$staging/node-red-project.bundle" >/dev/null
rm -rf -- "$bundle_verify_repo"
node_red_volume=$(docker inspect --format '{{range .Mounts}}{{if eq .Destination "/data"}}{{.Name}}{{end}}{{end}}' "$node_red_container")
[[ -n "$node_red_volume" ]] || { echo "Unable to resolve Node-RED volume." >&2; exit 1; }
docker run --rm --read-only --user 0:0 --entrypoint /bin/tar \
  -v "$node_red_volume:/source:ro" -v "$staging:/backup" "$node_red_image" \
  --exclude='./.config.projects.json' --exclude='./.config.runtime.json' --exclude='./.sessions.json' \
  -C /source -czf /backup/node-red-data.tar.gz .
tar -tzf "$staging/node-red-data.tar.gz" >/dev/null

docker run --rm --read-only --user 0:0 --cap-drop ALL \
  --security-opt no-new-privileges:true --network "$data_network" \
  --tmpfs /tmp:rw,noexec,nosuid,nodev,size=64m -e HOME=/tmp \
  -e SSL_CERT_FILE=/run/identity/ca.crt \
  -v "$secret_root/pki/ca/ca.crt:/run/identity/ca.crt:ro" \
  -v "$secret_root/influxdb/admin_token:/run/identity/admin_token:ro" \
  -v "$staging/influxdb-backup:/backup" \
  --entrypoint /bin/sh "$influx_image" -ec '
    export INFLUX_TOKEN="$(cat /run/identity/admin_token)"
    exec influx backup --host https://influxdb:8086 /backup
  '
find "$staging/influxdb-backup" -type f -print -quit | grep -q . || { echo "InfluxDB backup is empty." >&2; exit 1; }

docker compose exec -T automation-runtime python -m automation_runtime.backup \
  /var/lib/automation-runtime/seen.sqlite3 /tmp/automation-runtime.sqlite3
docker compose exec -T automation-runtime cat /tmp/automation-runtime.sqlite3 \
  >"$staging/automation-runtime.sqlite3"
docker compose exec -T automation-runtime rm -f /tmp/automation-runtime.sqlite3
sqlite3 "$staging/automation-runtime.sqlite3" 'PRAGMA integrity_check' | grep -qx ok

# Sanitized traces are runtime evidence and may still reveal behavioral
# patterns, so they remain root-only and travel only through encrypted backup.
event_volume=edsys-edcore-automation_automation-event-harness-data
if docker volume inspect "$event_volume" >/dev/null 2>&1; then
  docker run --rm --read-only --user 0:0 --entrypoint /bin/tar \
    -v "$event_volume:/source:ro" -v "$staging:/backup" "$mosquitto_image" \
    -C /source -czf /backup/event-harness-data.tar.gz .
else
  tar -C "$staging" -czf "$staging/event-harness-data.tar.gz" --files-from /dev/null
fi
tar -tzf "$staging/event-harness-data.tar.gz" >/dev/null

python3 - "$staging" "$run_id" <<'PY'
import json
from pathlib import Path
import socket
import sys
from datetime import datetime, timezone

root = Path(sys.argv[1])
run_id = sys.argv[2]
artifacts = sorted(
    path.relative_to(root).as_posix()
    for path in root.rglob("*")
    if path.is_file() and path.name not in {"MANIFEST.json", "SHA256SUMS"}
)
images = sorted(
    line.strip()
    for line in (root / "image-identities.txt").read_text(encoding="utf-8").splitlines()
    if line.strip()
)
manifest = {
    "schema": "edsys.edcore-automation.backup.v1",
    "run_id": run_id,
    "created_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    "hostname": socket.gethostname().split(".", 1)[0],
    "compose_project": "edsys-edcore-automation",
    "artifacts": artifacts,
    "artifact_count": len(artifacts),
    "images": images,
    "services": ["automation-runtime", "influxdb", "mosquitto", "node-red", "telegraf"],
}
(root / "MANIFEST.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
PY
python3 -m json.tool "$staging/MANIFEST.json" >/dev/null
(cd "$staging" && find . -type f ! -name SHA256SUMS -print0 | sort -z | xargs -0 sha256sum) >"$staging/SHA256SUMS"
(cd "$staging" && sha256sum -c SHA256SUMS >/dev/null)

chown -R root:root "$staging"
chmod -R go-rwx "$staging"
mv "$staging" "$final"
ln -sfn "$run_id" "$backup_root/current"
find "$backup_root" -mindepth 1 -maxdepth 1 -type d -name '20??????T??????Z' ! -path "$final" -mtime +35 -print0 \
  | xargs -0r --no-run-if-empty rm -rf --
printf '%s\n' "$final"
