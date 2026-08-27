#!/usr/bin/env bash
set -Eeuo pipefail

readonly stack_dir=/srv/edsys/edsys-infrastructure/docker/edcore-automation
readonly secret_root=/etc/edsys-secrets/edcore-automation
readonly evidence_root=/etc/edsys-escrow/client-disposition
readonly acceptance=$evidence_root/edsys-edge-livingroom-ingestion.json
readonly mqtt_image='docker.io/library/eclipse-mosquitto:2.1.2-alpine@sha256:6f8d8a947c506f8a2290ec65cd4bd2bc7cb4d43fb5f6271f861cb013e2ef9797'
readonly influx_image='docker.io/library/influxdb:2.8.0@sha256:09a5361809c771d863bcfa844a09598a82a6d9bbba1c9a9e2fa312e310572a14'
readonly broker_network=edsys-edcore-automation-broker
readonly data_network=edsys-edcore-automation-data
readonly source_topic=edsys/v1/telemetry/environment/edge-livingroom/synthetic
readonly topic_pseudonym_input=edge-livingroom/synthetic
readonly payload_pseudonym_input=edge-livingroom

[[ ${EUID} -eq 0 ]] || { echo "Run as root on edcore-automation." >&2; exit 1; }
[[ $(hostname -s) == edcore-automation ]] || { echo "Refusing ingestion acceptance on the wrong guest." >&2; exit 1; }
[[ $# -eq 1 && $1 == --accept ]] || { echo "Usage: $0 --accept" >&2; exit 64; }
/usr/local/sbin/edsys-automation-source-guard --runtime
cd "$stack_dir"

[[ ! -e /etc/edsys-escrow/online-keys-finalized.json && \
   ! -L /etc/edsys-escrow/online-keys-finalized.json ]] || {
  echo "Online external keys were already finalized; synthetic acceptance cannot be rerun." >&2
  exit 1
}
for relative in \
  pki/ca/ca.crt \
  pki/clients/edsys-edge-livingroom.crt pki/clients/edsys-edge-livingroom.key \
  pki/clients/nodered.crt pki/clients/nodered.key \
  pki/clients/command-audit.crt pki/clients/command-audit.key \
  pki/clients/event-replay.crt pki/clients/event-replay.key \
  influxdb/grafana_token; do
  [[ -s "$secret_root/$relative" ]] || { echo "Missing synthetic-acceptance file: $relative" >&2; exit 1; }
done
set -a
# shellcheck disable=SC1091
source .env
set +a
[[ ${LAN_BIND_ADDRESS:-} == 192.168.50.82 ]]
[[ ${INFLUXDB_ORG:-} =~ ^[A-Za-z0-9._-]{1,64}$ ]]
[[ ${INFLUXDB_BUCKET:-} =~ ^[A-Za-z0-9._-]{1,64}$ ]]

for service in mosquitto influxdb node-red telegraf; do
  container=$(docker compose ps -q "$service")
  [[ -n $container && \
     $(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$container") == healthy ]] || {
    echo "$service is not healthy; refusing synthetic acceptance." >&2
    exit 1
  }
done

run_id="edge-$(date -u +%Y%m%d%H%M%S)-$(openssl rand -hex 3)"
readonly run_id
mqtt_id_suffix=$(printf '%06x' "$$")
readonly mqtt_id_suffix
[[ $mqtt_id_suffix =~ ^[0-9a-f]{6}$ ]] || {
  echo "Unable to derive a bounded MQTT client-ID suffix." >&2
  exit 1
}
readonly record_container="edsys-edge-record-$run_id"
readonly trace_path="/var/lib/automation-event-harness/acceptance/$run_id.jsonl"
source_digest=$(printf '%s' "$topic_pseudonym_input" | sha256sum | awk '{print substr($1,1,16)}')
readonly source_digest
payload_source_digest=$(printf '%s' "$payload_pseudonym_input" | sha256sum | awk '{print substr($1,1,16)}')
readonly payload_source_digest
[[ $source_digest =~ ^[0-9a-f]{16}$ && $payload_source_digest =~ ^[0-9a-f]{16}$ && \
   $source_digest != "$payload_source_digest" ]] || {
  echo "Unable to derive distinct bounded trace pseudonyms." >&2
  exit 1
}
readonly sanitized_topic="telemetry/environment/source-$source_digest"
readonly sanitized_payload_source="source-$payload_source_digest"
readonly replay_topic="edsys/test/v1/replay/$run_id/$sanitized_topic"
synthetic_value=$(python3 - <<'PY'
import secrets
print(f"{1_000_000 + secrets.randbelow(900_000_000) / 1000:.3f}")
PY
)
readonly synthetic_value
scratch=$(mktemp -d /var/tmp/edcore-edge-acceptance.XXXXXX)
readonly scratch
record_pid=
ready_pid=
replay_sub_pid=
command_sub_pid=

cleanup() {
  local rc=$?
  for pid in "$ready_pid" "$record_pid" "$replay_sub_pid" "$command_sub_pid"; do
    if [[ -n $pid ]]; then
      kill "$pid" 2>/dev/null || true
    fi
  done
  docker rm -f "$record_container" >/dev/null 2>&1 || true
  rm -rf -- "$scratch"
  exit "$rc"
}
trap cleanup EXIT

mqtt_base() {
  local identity=$1 client_id=$2 command=$3
  shift 3
  [[ $client_id =~ ^[a-z0-9][a-z0-9-]{0,22}$ ]] || {
    echo "Unsafe or overlong MQTT client ID: $client_id" >&2
    return 64
  }
  [[ $command == mosquitto_pub || $command == mosquitto_sub ]] || {
    echo "Unsupported MQTT probe command: $command" >&2
    return 64
  }
  docker run --rm --read-only --cap-drop ALL --security-opt no-new-privileges:true \
    --network "$broker_network" --tmpfs /tmp:rw,noexec,nosuid,nodev,size=16m \
    -v "$secret_root/pki/ca/ca.crt:/run/identity/ca.crt:ro" \
    -v "$secret_root/pki/clients/$identity.crt:/run/identity/client.crt:ro" \
    -v "$secret_root/pki/clients/$identity.key:/run/identity/client.key:ro" \
    "$mqtt_image" "$command" -i "$client_id" "$@" --cafile /run/identity/ca.crt \
    --cert /run/identity/client.crt --key /run/identity/client.key
}

mqtt_timeout_was_authenticated() {
  local error_file=$1 client_id=$2 started=$3 log_file=$4
  local deadline
  grep -Fq 'Timed out' "$error_file" || return 1
  if grep -Eiq \
      'zero length clientid|client identifier|not authori[sz]ed|certificate|tls|ssl|connection (error|refused|lost)|protocol error|network error|host not found' \
      "$error_file"; then
    return 1
  fi
  deadline=$((SECONDS + 5))
  while (( SECONDS < deadline )); do
    docker compose logs --no-color --since "$started" mosquitto >"$log_file" 2>/dev/null || true
    if grep -F "New client connected from " "$log_file" | grep -Fq " as $client_id "; then
      ! grep -Fq "Denied SUBSCRIBE from $client_id" "$log_file" || return 1
      ! grep -Eiq "Client $client_id .*not authori[sz]ed|Client $client_id .*protocol error" \
        "$log_file" || return 1
      return 0
    fi
    sleep 0.2
  done
  return 1
}

influx_query() {
  local query=$1
  docker run --rm --read-only --user 0:0 --cap-drop ALL \
    --security-opt no-new-privileges:true --network "$data_network" \
    --tmpfs /tmp:rw,noexec,nosuid,nodev,size=32m -e HOME=/tmp \
    -e SSL_CERT_FILE=/run/identity/ca.crt \
    -v "$secret_root/pki/ca/ca.crt:/run/identity/ca.crt:ro" \
    -v "$secret_root/influxdb/grafana_token:/run/identity/read_token:ro" \
    --entrypoint /bin/sh "$influx_image" -ec '
      export INFLUX_TOKEN="$(cat /run/identity/read_token)"
      exec influx query --host https://influxdb:8086 --org "$1" --raw "$2"
    ' sh "$INFLUXDB_ORG" "$query"
}

# Wait for the recorder's non-retained online event rather than relying on a
# fixed startup sleep, then publish exactly one selected-telemetry event in a
# quiet ten-second aggregation window. Any concurrent environment event makes
# the acceptance fail closed instead of producing ambiguous evidence.
mqtt_base nodered "edge-ready-$mqtt_id_suffix" mosquitto_sub \
  -h mosquitto -p 8883 -V mqttv5 -q 1 -W 20 -C 1 \
  -t edsys/v1/availability/edcore-automation/event-replay >"$scratch/ready.json" 2>"$scratch/ready.err" &
ready_pid=$!
docker compose --profile tools run --rm --no-deps -T --name "$record_container" \
  event-harness record --output "$trace_path" --duration 25 --max-events 1000 \
  >"$scratch/record.json" 2>"$scratch/record.err" &
record_pid=$!
wait "$ready_pid"
ready_pid=
jq -e '.status == "online" and .source == "event-replay"' "$scratch/ready.json" >/dev/null

# Telegraf rounds its basicstats period to ten-second boundaries. Start near
# the beginning of the next complete window so a single accepted input yields
# count/min/max/mean evidence with no dependence on a wall-clock race.
delay=$(python3 - <<'PY'
import time
now = time.time()
target = (int(now) // 10 + 1) * 10 + 1
print(max(0, target - now))
PY
)
sleep "$delay"
payload=$(python3 - "$synthetic_value" "$payload_pseudonym_input" <<'PY'
from datetime import datetime, timezone
import json
import sys
print(json.dumps({
    "schema": "edsys.telemetry.environment.v1",
    "source": sys.argv[2],
    "metric": "synthetic-acceptance",
    "value": float(sys.argv[1]),
    "unit": "acceptance-only",
    "quality": "synthetic",
    "tags": {"location_class": "livingroom", "sensor_type": "synthetic"},
    "ts": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
}, separators=(",", ":")))
PY
)
mqtt_base edsys-edge-livingroom "edge-pub-$mqtt_id_suffix" mosquitto_pub \
  -h mosquitto -p 8883 -V mqttv5 -q 1 \
  -t "$source_topic" -m "$payload"
wait "$record_pid"
record_pid=
jq -e '.recorded >= 1 and .rejected >= 0' "$scratch/record.json" >/dev/null

# Validate the stored trace itself, not only the recorder's summary. Exactly
# one environmental event must exist and it must be the hashed/sanitized form
# of the synthetic edge event. The only output is its non-secret SHA-256.
trace_hash=$(docker compose --profile tools run --rm --no-deps -T --entrypoint python \
  event-harness - "$trace_path" "$sanitized_topic" "$sanitized_payload_source" \
  "$synthetic_value" "$payload_pseudonym_input" <<'PY'
import hashlib
import json
from pathlib import Path
import sys

path = Path(sys.argv[1])
expected_topic = sys.argv[2]
expected_payload_source = sys.argv[3]
expected_value = float(sys.argv[4])
raw_identity = sys.argv[5]
raw = path.read_bytes()
decoded = raw.decode("utf-8")
assert raw_identity not in decoded
lines = [json.loads(line) for line in decoded.splitlines()]
assert lines and lines[0]["kind"] == "header"
events = [item for item in lines[1:] if item.get("kind") == "event"]
environment = [item for item in events if item.get("topic", "").startswith("telemetry/environment/")]
assert len(environment) == 1
event = environment[0]
assert event["topic"] == expected_topic
assert event["payload"]["value"] == expected_value
assert event["payload"]["metric"] == "synthetic-acceptance"
assert expected_payload_source != expected_topic.rsplit("/", 1)[1]
assert event["payload"]["source"] == expected_payload_source
assert "ts" not in event["payload"]
print(hashlib.sha256(raw).hexdigest())
PY
)
[[ $trace_hash =~ ^[0-9a-f]{64}$ ]]

# Subscribe before replay, require the exact sanitized event in the test-only
# namespace, and simultaneously prove that replay emits no HA command.
mqtt_base nodered "edge-replay-$mqtt_id_suffix" mosquitto_sub \
  -h mosquitto -p 8883 -V mqttv5 -q 1 -W 20 -C 1 \
  -t "$replay_topic" >"$scratch/replayed.json" 2>"$scratch/replayed.err" &
replay_sub_pid=$!
command_probe_started=$(date -u +%Y-%m-%dT%H:%M:%S.%NZ)
mqtt_base command-audit "edge-audit-$mqtt_id_suffix" mosquitto_sub \
  -h mosquitto -p 8883 -V mqttv5 -q 1 -W 8 -C 1 \
  -t 'edsys/v1/command/ha/#' >"$scratch/command.json" 2>"$scratch/command.err" &
command_sub_pid=$!
sleep 1
docker compose --profile tools run --rm --no-deps -T event-harness replay \
  --input "$trace_path" --run-id "$run_id" --speed 100 >"$scratch/replay.json"
wait "$replay_sub_pid"
replay_sub_pid=
jq -e --arg source "$sanitized_payload_source" --argjson value "$synthetic_value" \
  '.source == $source and .metric == "synthetic-acceptance" and .value == $value' \
  "$scratch/replayed.json" >/dev/null
set +e
wait "$command_sub_pid"
command_rc=$?
set -e
command_sub_pid=
if ! {
  [[ $command_rc -eq 27 && ! -s "$scratch/command.json" ]] &&
    mqtt_timeout_was_authenticated "$scratch/command.err" \
      "edge-audit-$mqtt_id_suffix" "$command_probe_started" "$scratch/command.log"
}; then
  echo "Synthetic replay emitted a production command or the audit probe failed." >&2
  exit 1
fi

# The selected stream is intentionally aggregated into one bounded series.
# A quiet-window row with count=1 and equal min/max/mean proves that the exact
# synthetic value traversed edge mTLS -> broker ACL -> Telegraf -> InfluxDB.
# The Flux record update retains grouped measurement/host keys so raw CSV emits
# a data row instead of an otherwise successful header-only table.
flux_query=$(cat <<EOF
from(bucket: "$INFLUXDB_BUCKET")
  |> range(start: -5m)
  |> filter(fn: (r) => r._measurement == "selected_telemetry")
  |> filter(fn: (r) => r._field == "value_count" or r._field == "value_min" or r._field == "value_max" or r._field == "value_mean")
  |> group(columns: ["_measurement", "host"])
  |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
  |> filter(fn: (r) => exists r.value_count and exists r.value_min and exists r.value_max and exists r.value_mean)
  |> filter(fn: (r) => r.value_count == 1 and r.value_min == $synthetic_value and r.value_max == $synthetic_value and r.value_mean == $synthetic_value)
  |> map(fn: (r) => ({r with _value: "edsys-edge-ingestion-passed"}))
EOF
)
influx_deadline=$((SECONDS + 90))
influx_passed=false
while (( SECONDS < influx_deadline )); do
  if influx_query "$flux_query" 2>"$scratch/influx.err" | \
    tr -d '\r' | \
    grep -Eq '(^|,)edsys-edge-ingestion-passed(,|$)'; then
    influx_passed=true
    break
  fi
  sleep 3
done
[[ $influx_passed == true ]] || {
  echo "Synthetic selected telemetry was not proven in InfluxDB." >&2
  cat "$scratch/influx.err" >&2
  exit 1
}

certificate_hash=$(sha256sum "$secret_root/pki/clients/edsys-edge-livingroom.crt" | awk '{print $1}')
install -d -o root -g root -m 0700 "$evidence_root"
temporary=$acceptance.new
python3 - "$certificate_hash" "$run_id" "$source_topic" "$sanitized_topic" \
  "$trace_hash" "$synthetic_value" >"$temporary" <<'PY'
from datetime import datetime, timezone
import json
import sys
print(json.dumps({
    "schema": "edsys.edcore-automation.synthetic-ingestion-acceptance.v1",
    "identity": "edsys-edge-livingroom",
    "certificate_sha256": sys.argv[1],
    "run_id": sys.argv[2],
    "source_topic": sys.argv[3],
    "sanitized_topic": sys.argv[4],
    "trace_sha256": sys.argv[5],
    "synthetic_value": float(sys.argv[6]),
    "influx_measurement": "selected_telemetry",
    "accepted_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
}, sort_keys=True))
PY
chown root:root "$temporary"
chmod 0600 "$temporary"
mv "$temporary" "$acceptance"
printf 'Synthetic edge ingestion and test-namespace replay acceptance passed; evidence recorded at %s.\n' "$acceptance"
