#!/usr/bin/env bash
set -Eeuo pipefail

readonly secret_root=/etc/edsys-secrets/edcore-automation
readonly backup_root=/var/backups/edcore-automation
readonly source_dir=${1:-$(readlink -f "$backup_root/current")}
readonly suffix=$$
mqtt_id_suffix=$(printf '%06x' "$suffix")
readonly mqtt_id_suffix
[[ $mqtt_id_suffix =~ ^[0-9a-f]{6}$ ]] || {
  echo "Unable to derive a bounded MQTT client-ID suffix." >&2
  exit 1
}
readonly network=edsys-automation-restore-$suffix
readonly mosquitto_container=edsys-automation-restore-mqtt-$suffix
readonly influx_container=edsys-automation-restore-influx-$suffix
readonly node_red_container=edsys-automation-restore-nodered-$suffix
readonly mosquitto_volume=edsys-automation-restore-mqtt-$suffix
readonly influx_volume=edsys-automation-restore-influx-$suffix
readonly node_red_volume=edsys-automation-restore-nodered-$suffix
test_dir=$(mktemp -d /var/tmp/edcore-automation-restore.XXXXXX)
readonly test_dir
readonly mosquitto_image='docker.io/library/eclipse-mosquitto:2.1.2-alpine@sha256:6f8d8a947c506f8a2290ec65cd4bd2bc7cb4d43fb5f6271f861cb013e2ef9797'
readonly influx_image='docker.io/library/influxdb:2.8.0@sha256:09a5361809c771d863bcfa844a09598a82a6d9bbba1c9a9e2fa312e310572a14'
readonly node_red_image='localhost/edsys/edcore-automation-node-red:4.1.13-1'
readonly healthchecks_env=/etc/edsys-secrets/edcore-automation/healthchecks/restore-test.env

[[ ${EUID} -eq 0 ]] || { echo "Run as root on edcore-automation." >&2; exit 1; }
[[ $(hostname -s) == edcore-automation ]] || { echo "Refusing to test restore on the wrong guest." >&2; exit 1; }
/usr/local/sbin/edsys-automation-source-guard --runtime

ping_healthchecks() {
  [[ -n ${HC_PING_URL:-} ]] || return 0
  local state=${1:-} suffix_path='' url=$HC_PING_URL
  case "$state" in
    "") ;;
    start|fail) suffix_path=/$state ;;
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
url = "${url}${suffix_path}"
EOF
}
cleanup() {
  local rc=$?
  docker rm -f "$mosquitto_container" "$influx_container" "$node_red_container" >/dev/null 2>&1 || true
  docker volume rm -f "$mosquitto_volume" "$influx_volume" "$node_red_volume" >/dev/null 2>&1 || true
  docker network rm "$network" >/dev/null 2>&1 || true
  rm -rf -- "$test_dir"
  if (( rc == 0 )); then ping_healthchecks ""; else ping_healthchecks fail; fi
  exit "$rc"
}
trap cleanup EXIT
ping_healthchecks start

[[ -d "$source_dir" ]] || { echo "Backup source does not exist: $source_dir" >&2; exit 1; }
(cd "$source_dir" && sha256sum -c SHA256SUMS >/dev/null)
python3 - "$source_dir" <<'PY'
import json
from pathlib import Path
import re
import sys

root = Path(sys.argv[1])
manifest = json.loads((root / "MANIFEST.json").read_text(encoding="utf-8"))
required = {"schema", "run_id", "created_utc", "hostname", "compose_project", "artifacts", "artifact_count", "images", "services"}
assert set(manifest) == required
assert manifest["schema"] == "edsys.edcore-automation.backup.v1"
assert re.fullmatch(r"[0-9]{8}T[0-9]{6}Z", manifest["run_id"])
assert manifest["run_id"] == root.name
assert manifest["hostname"] == "edcore-automation"
assert manifest["compose_project"] == "edsys-edcore-automation"
assert manifest["services"] == ["automation-runtime", "influxdb", "mosquitto", "node-red", "telegraf"]
files = sorted(
    path.relative_to(root).as_posix()
    for path in root.rglob("*")
    if path.is_file() and path.name not in {"MANIFEST.json", "SHA256SUMS"}
)
assert manifest["artifacts"] == files
assert manifest["artifact_count"] == len(files)
PY

for archive in mosquitto-data.tar.gz node-red-data.tar.gz event-harness-data.tar.gz; do
  tar -tzf "$source_dir/$archive" >/dev/null
done
bundle_verify_repo="$test_dir/bundle-verify.git"
git -c init.defaultBranch=main init --bare "$bundle_verify_repo" >/dev/null
git -C "$bundle_verify_repo" bundle verify "$source_dir/node-red-project.bundle" >/dev/null
sqlite3 "$source_dir/automation-runtime.sqlite3" 'PRAGMA integrity_check' | grep -qx ok

read_backup_env() {
  local key=$1 value
  value=$(sed -n "s/^${key}=//p" "$source_dir/config/.env")
  [[ -n $value && $(grep -c "^${key}=" "$source_dir/config/.env") -eq 1 ]] || {
    echo "Backup has no unique ${key} setting." >&2
    exit 1
  }
  printf '%s' "$value"
}
influx_init_username=$(read_backup_env INFLUXDB_INIT_USERNAME)
influx_org=$(read_backup_env INFLUXDB_ORG)
influx_bucket=$(read_backup_env INFLUXDB_BUCKET)
influx_retention=$(read_backup_env INFLUXDB_RETENTION)
for value in "$influx_init_username" "$influx_org" "$influx_bucket"; do
  [[ $value =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$ ]] || {
    echo "Backup contains an unsafe InfluxDB setup value." >&2
    exit 1
  }
done
[[ $influx_retention =~ ^[1-9][0-9]*[smhdw]$ ]] || {
  echo "Backup contains an unsafe InfluxDB retention value." >&2
  exit 1
}

docker network create --internal "$network" >/dev/null
docker volume create "$mosquitto_volume" >/dev/null
docker volume create "$influx_volume" >/dev/null
docker volume create "$node_red_volume" >/dev/null

# The immutable backup tree is intentionally root-only. Copy only the three
# non-secret broker configuration files into this disposable root-owned test
# directory with container-readable modes; never relax the backup itself.
install -d -o root -g root -m 0700 "$test_dir/mosquitto-config"
install -o root -g root -m 0444 \
  "$source_dir/config/mosquitto/mosquitto.conf" \
  "$source_dir/config/mosquitto/aclfile" \
  "$source_dir/config/mosquitto/aclfile-internal" \
  "$test_dir/mosquitto-config/"

docker run --rm --user 0:0 --entrypoint /bin/tar \
  -v "$mosquitto_volume:/target" -v "$source_dir:/backup:ro" "$mosquitto_image" \
  -C /target -xzf /backup/mosquitto-data.tar.gz
docker run -d --name "$mosquitto_container" --network "$network" --network-alias mosquitto \
  --user 1883:0 \
  --read-only --cap-drop ALL --security-opt no-new-privileges:true \
  --tmpfs /tmp:rw,noexec,nosuid,nodev,size=32m \
  -v "$mosquitto_volume:/mosquitto/data" \
  -v "$test_dir/mosquitto-config/mosquitto.conf:/mosquitto/config/mosquitto.conf:ro" \
  -v "$test_dir/mosquitto-config/aclfile:/mosquitto/config/aclfile:ro" \
  -v "$test_dir/mosquitto-config/aclfile-internal:/mosquitto/config/aclfile-internal:ro" \
  -v "$secret_root/pki/ca/ca.crt:/run/secrets/automation_ca_cert:ro" \
  -v "$secret_root/pki/servers/mosquitto.crt:/run/secrets/mosquitto_server_cert:ro" \
  -v "$secret_root/pki/servers/mosquitto.key:/run/secrets/mosquitto_server_key:ro" \
  -v "$secret_root/pki/clients/mqtt-health.crt:/run/secrets/mqtt_health_client_cert:ro" \
  -v "$secret_root/pki/clients/mqtt-health.key:/run/secrets/mqtt_health_client_key:ro" \
  -v "$secret_root/pki/clients/command-audit.crt:/run/secrets/command_audit_client_cert:ro" \
  -v "$secret_root/pki/clients/command-audit.key:/run/secrets/command_audit_client_key:ro" \
  "$mosquitto_image" >/dev/null
for attempt in $(seq 1 30); do
  printf -v restore_wait_client_id 'r-wait-%s-%02d' "$mqtt_id_suffix" "$attempt"
  [[ $restore_wait_client_id =~ ^[a-z0-9][a-z0-9-]{0,22}$ ]] || {
    echo "Unsafe or overlong restore MQTT client ID." >&2
    exit 1
  }
  docker exec "$mosquitto_container" mosquitto_pub -i "$restore_wait_client_id" \
    -h mosquitto -p 8883 \
    --cafile /run/secrets/automation_ca_cert \
    --cert /run/secrets/mqtt_health_client_cert --key /run/secrets/mqtt_health_client_key \
    -t edsys/test/v1/health/mqtt-health/restore -m '{"status":"probe"}' -q 1 >/dev/null 2>&1 && break
  sleep 1
done
docker exec "$mosquitto_container" mosquitto_pub -i "r-health-$mqtt_id_suffix" \
  -h mosquitto -p 8883 \
  --cafile /run/secrets/automation_ca_cert \
  --cert /run/secrets/mqtt_health_client_cert --key /run/secrets/mqtt_health_client_key \
  -t edsys/test/v1/health/mqtt-health/restore -m '{"status":"probe"}' -q 1 >/dev/null
set +e
retained_output=$(docker exec "$mosquitto_container" mosquitto_sub -i "r-audit-$mqtt_id_suffix" \
  -h mosquitto -p 8883 \
  --cafile /run/secrets/automation_ca_cert \
  --cert /run/secrets/command_audit_client_cert --key /run/secrets/command_audit_client_key \
  -V mqttv5 --retained-only -W 3 -C 1 -t 'edsys/v1/command/ha/#' 2>"$test_dir/retained-command.err")
retained_rc=$?
set -e
docker logs "$mosquitto_container" >"$test_dir/retained-command.log" 2>&1
if ! {
  [[ $retained_rc -eq 27 && -z "$retained_output" ]] &&
    grep -Fq 'Timed out' "$test_dir/retained-command.err" &&
    ! grep -Eiq \
      'zero length clientid|client identifier|not authori[sz]ed|certificate|tls|ssl|connection (error|refused|lost)|protocol error|network error|host not found' \
      "$test_dir/retained-command.err" &&
    grep -F 'New client connected from ' "$test_dir/retained-command.log" |
      grep -Fq " as r-audit-$mqtt_id_suffix " &&
    ! grep -Fq "Denied SUBSCRIBE from r-audit-$mqtt_id_suffix" \
      "$test_dir/retained-command.log" &&
    ! grep -Eiq \
      "Client r-audit-$mqtt_id_suffix .*not authori[sz]ed|Client r-audit-$mqtt_id_suffix .*protocol error" \
      "$test_dir/retained-command.log"
}; then
  echo "Isolated broker restore contained a retained command or the authenticated probe failed (rc=$retained_rc)." >&2
  exit 1
fi

docker run -d --name "$influx_container" --network "$network" --network-alias influxdb \
  --user 1000:0 \
  --cap-drop ALL --security-opt no-new-privileges:true \
  -e DOCKER_INFLUXDB_INIT_MODE=setup \
  -e "DOCKER_INFLUXDB_INIT_USERNAME=$influx_init_username" \
  -e DOCKER_INFLUXDB_INIT_PASSWORD_FILE=/run/secrets/admin_password \
  -e DOCKER_INFLUXDB_INIT_ADMIN_TOKEN_FILE=/run/secrets/admin_token \
  -e "DOCKER_INFLUXDB_INIT_ORG=$influx_org" \
  -e "DOCKER_INFLUXDB_INIT_BUCKET=$influx_bucket" \
  -e "DOCKER_INFLUXDB_INIT_RETENTION=$influx_retention" \
  -v "$secret_root/influxdb/admin_password:/run/secrets/admin_password:ro" \
  -v "$secret_root/influxdb/admin_token:/run/secrets/admin_token:ro" \
  -v "$influx_volume:/var/lib/influxdb2" "$influx_image" influxd >/dev/null
for _ in $(seq 1 60); do
  docker exec "$influx_container" influx ping --host http://127.0.0.1:8086 >/dev/null 2>&1 && break
  sleep 1
done
docker exec "$influx_container" influx ping --host http://127.0.0.1:8086 >/dev/null
docker run --rm --network "$network" \
  -v "$source_dir/influxdb-backup:/backup:ro" \
  -v "$secret_root/influxdb/admin_token:/run/secrets/admin_token:ro" \
  --entrypoint /bin/sh "$influx_image" -ec '
    export INFLUX_TOKEN="$(cat /run/secrets/admin_token)"
    exec influx restore --host http://influxdb:8086 --full /backup
  ' >/dev/null
docker run --rm --network "$network" \
  -v "$secret_root/influxdb/admin_token:/run/secrets/admin_token:ro" "$influx_image" sh -ec '
    export INFLUX_TOKEN="$(cat /run/secrets/admin_token)"
    influx bucket list --host http://influxdb:8086 --org edsys --name automation_selected --json
  ' | jq -e 'type == "array" and length >= 1' >/dev/null

docker run --rm --user 0:0 --entrypoint /bin/tar \
  -v "$node_red_volume:/target" -v "$source_dir:/backup:ro" "$node_red_image" \
  -C /target -xzf /backup/node-red-data.tar.gz
docker run -d --name "$node_red_container" --network "$network" \
  --read-only --cap-drop ALL --security-opt no-new-privileges:true --group-add 0 \
  --tmpfs /tmp:rw,noexec,nosuid,nodev,size=64m,uid=1000,gid=0,mode=0770 \
  -e NODE_RED_ADMIN_USERNAME=admin \
  -v "$node_red_volume:/data" \
  -v "$secret_root/pki/ca/ca.crt:/run/secrets/automation_ca_cert:ro" \
  -v "$secret_root/node-red/admin_password_hash:/run/secrets/node_red_admin_password_hash:ro" \
  -v "$secret_root/node-red/credential_secret:/run/secrets/node_red_credential_secret:ro" \
  -v "$secret_root/pki/servers/node-red.crt:/run/secrets/node_red_tls_cert:ro" \
  -v "$secret_root/pki/servers/node-red.key:/run/secrets/node_red_tls_key:ro" \
  -v "$secret_root/pki/clients/nodered.crt:/run/secrets/mqtt_client_cert:ro" \
  -v "$secret_root/pki/clients/nodered.key:/run/secrets/mqtt_client_key:ro" \
  "$node_red_image" >/dev/null
for _ in $(seq 1 60); do
  docker exec "$node_red_container" node /opt/edsys/healthcheck.js >/dev/null 2>&1 && break
  sleep 1
done
docker exec "$node_red_container" node /opt/edsys/healthcheck.js >/dev/null
docker exec "$node_red_container" sh -ec '
  test -d /data/projects/edcore-automation/.git
  test -z "$(git -C /data/projects/edcore-automation status --porcelain)"
  node -e '\''const fs=require("fs"); const c=JSON.parse(fs.readFileSync("/data/projects/edcore-automation/flows_cred.json")); if(Object.keys(c).length!==1||typeof c.$!=="string") process.exit(1)'\''
'

printf 'Isolated EdCore automation restore passed for %s.\n' "$(basename "$source_dir")"
