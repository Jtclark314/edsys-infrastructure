#!/usr/bin/env bash
set -Eeuo pipefail

/usr/local/sbin/edsys-automation-source-guard --runtime

stack_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
readonly stack_dir
readonly secret_root=/etc/edsys-secrets/edcore-automation
readonly mqtt_image='docker.io/library/eclipse-mosquitto:2.1.2-alpine@sha256:6f8d8a947c506f8a2290ec65cd4bd2bc7cb4d43fb5f6271f861cb013e2ef9797'
readonly network=edsys-edcore-automation-broker
readonly ingress_network=edsys-edcore-automation-ingress
scratch=$(mktemp -d /var/tmp/edcore-automation-verify.XXXXXX)
readonly scratch
mqtt_id_suffix=$(printf '%06x' "$$")
readonly mqtt_id_suffix
[[ $mqtt_id_suffix =~ ^[0-9a-f]{6}$ ]] || {
  echo "Unable to derive a bounded MQTT client-ID suffix." >&2
  exit 1
}
cd "$stack_dir"

[[ ${EUID} -eq 0 ]] || { echo "Run as root on edcore-automation." >&2; exit 1; }
[[ $(hostname -s) == edcore-automation ]] || { echo "Refusing to verify the wrong guest." >&2; exit 1; }
cleanup() { rm -rf -- "$scratch"; }
trap cleanup EXIT

set -a
# shellcheck disable=SC1091
source .env
set +a
[[ ${LAN_BIND_ADDRESS:-} == 192.168.50.82 ]]

wait_healthy() {
  local service=$1 container deadline state
  container=$(docker compose ps -q "$service")
  [[ -n "$container" ]] || return 1
  deadline=$((SECONDS + 120))
  while (( SECONDS < deadline )); do
    state=$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$container")
    [[ "$state" == healthy ]] && return 0
    sleep 2
  done
  return 1
}
for service in mosquitto influxdb automation-runtime node-red telegraf; do
  wait_healthy "$service" || { docker compose ps; echo "$service is not healthy." >&2; exit 1; }
done
telegraf_started=$(docker inspect --format '{{.State.StartedAt}}' "$(docker compose ps -q telegraf)")
telegraf_logs=$(docker compose logs --no-color --since "$telegraf_started" telegraf)
[[ $(grep -Fc '[inputs.mqtt_consumer] Connected' <<<"$telegraf_logs") -ge 2 ]]
if grep -Eq '(^|[[:space:]])E!' <<<"$telegraf_logs"; then
  echo "Telegraf logged an error after its current container start." >&2
  exit 1
fi

# Steady-state secret minimization, topology isolation, and bounded resources.
jq -e '
  .["userland-proxy"] == false
  and (has("allow-direct-routing") | not)
' /etc/docker/daemon.json >/dev/null
mosquitto_container=$(docker compose ps -q mosquitto)
influx_container=$(docker compose ps -q influxdb)
runtime_container=$(docker compose ps -q automation-runtime)
node_red_container=$(docker compose ps -q node-red)
telegraf_container=$(docker compose ps -q telegraf)
if ! influx_mount_destinations=$(docker inspect --format \
  '{{range .Mounts}}{{println .Destination}}{{end}}' "$influx_container"); then
  echo "Unable to inspect steady InfluxDB mounts." >&2
  exit 1
fi
if grep -Eq 'influxdb_admin_(password|token)' <<<"$influx_mount_destinations"; then
  echo "Steady InfluxDB mounts a bootstrap credential." >&2
  exit 1
fi
unset influx_mount_destinations
if ! influx_env_names=$(docker inspect --format \
  '{{range .Config.Env}}{{println (index (split . "=") 0)}}{{end}}' "$influx_container"); then
  echo "Unable to inspect steady InfluxDB environment names." >&2
  exit 1
fi
for forbidden_influx_env_name in \
  DOCKER_INFLUXDB_INIT_MODE \
  DOCKER_INFLUXDB_INIT_USERNAME \
  DOCKER_INFLUXDB_INIT_PASSWORD_FILE \
  DOCKER_INFLUXDB_INIT_ADMIN_TOKEN_FILE \
  DOCKER_INFLUXDB_INIT_ORG \
  DOCKER_INFLUXDB_INIT_BUCKET \
  DOCKER_INFLUXDB_INIT_RETENTION; do
  if grep -Fxq -- "$forbidden_influx_env_name" <<<"$influx_env_names"; then
    echo "Steady InfluxDB retains forbidden bootstrap environment name: $forbidden_influx_env_name" >&2
    exit 1
  fi
done
unset influx_env_names forbidden_influx_env_name
docker inspect "$mosquitto_container" | jq -e '
  (.[0].NetworkSettings.Networks | keys | sort) ==
    ["edsys-edcore-automation-broker","edsys-edcore-automation-ingress"]
  and .[0].NetworkSettings.Networks["edsys-edcore-automation-ingress"].IPAddress == "172.31.82.18"
' >/dev/null
docker inspect "$influx_container" | jq -e '
  (.[0].NetworkSettings.Networks | keys | sort) ==
    ["edsys-edcore-automation-data","edsys-edcore-automation-ingress"]
  and .[0].NetworkSettings.Networks["edsys-edcore-automation-ingress"].IPAddress == "172.31.82.19"
' >/dev/null
docker inspect "$runtime_container" | jq -e '
  (.[0].NetworkSettings.Networks | keys) == ["edsys-edcore-automation-broker"]
' >/dev/null
docker inspect "$telegraf_container" | jq -e '
  (.[0].NetworkSettings.Networks | keys | sort) ==
    ["edsys-edcore-automation-broker","edsys-edcore-automation-data"]
' >/dev/null
for service in mosquitto influxdb automation-runtime node-red telegraf; do
  container=$(docker compose ps -q "$service")
  docker inspect "$container" | jq -e \
    '.[0].HostConfig.Memory > 0 and .[0].HostConfig.PidsLimit > 0 and .[0].HostConfig.NanoCpus > 0' >/dev/null
done
docker inspect "$node_red_container" | jq -e \
  '.[0].NetworkSettings.Networks | keys | sort == ["edsys-edcore-automation-broker","edsys-edcore-automation-data","edsys-edcore-automation-egress"]' >/dev/null
docker inspect "$node_red_container" | jq -e \
  '.[0].NetworkSettings.Networks["edsys-edcore-automation-egress"].IPAddress == "172.31.82.2"' >/dev/null
docker network inspect edsys-edcore-automation-egress | jq -e '
  .[0].Options["com.docker.network.bridge.name"] == "br-edsys-egress"
  and (.[0].IPAM.Config | length == 1)
  and .[0].IPAM.Config[0].Subnet == "172.31.82.0/28"
  and .[0].IPAM.Config[0].Gateway == "172.31.82.1"
' >/dev/null
# Match deploy's exact two-shape Docker 29 compatibility rule for an unset
# ingress IP range; a null/nonempty value, extra key, or second entry fails.
docker network inspect "$ingress_network" | jq -e \
  --arg mosquitto "$mosquitto_container" --arg influxdb "$influx_container" '
  .[0].Internal == false
  and .[0].EnableIPv6 == false
  and .[0].Options["com.docker.network.bridge.name"] == "br-ed-ingress"
  and .[0].Options["com.docker.network.bridge.enable_icc"] == "false"
  and (.[0].Options | has("com.docker.network.bridge.gateway_mode_ipv4") | not)
  and (.[0].Options | has("com.docker.network.bridge.trusted_host_interfaces") | not)
  and (
    .[0].IPAM.Config == [{
      "Subnet": "172.31.82.16/29",
      "Gateway": "172.31.82.17"
    }]
    or
    .[0].IPAM.Config == [{
      "Subnet": "172.31.82.16/29",
      "IPRange": "",
      "Gateway": "172.31.82.17"
    }]
  )
  and (.[0].Containers | keys | sort) == ([$mosquitto, $influxdb] | sort)
  and .[0].Containers[$mosquitto].IPv4Address == "172.31.82.18/29"
  and .[0].Containers[$influxdb].IPv4Address == "172.31.82.19/29"
' >/dev/null
if docker run --rm --network "container:$influx_container" \
  localhost/edsys/edcore-automation-runtime:1.0.0 python -c \
  'import socket; socket.create_connection(("mosquitto", 8883), 2)' >/dev/null 2>&1; then
  echo "InfluxDB network namespace can reach the broker." >&2
  exit 1
fi
if docker run --rm --network "container:$runtime_container" \
  localhost/edsys/edcore-automation-runtime:1.0.0 python -c \
  'import socket; socket.create_connection(("influxdb", 8086), 2)' >/dev/null 2>&1; then
  echo "Automation runtime network namespace can reach the data plane." >&2
  exit 1
fi

namespace_connection_must_fail() {
  local namespace=$1 host=$2 port=$3 description=$4
  if docker run --rm --read-only --cap-drop ALL \
    --security-opt no-new-privileges:true --pids-limit 32 --memory 64m \
    --network "container:$namespace" \
    localhost/edsys/edcore-automation-runtime:1.0.0 \
    python -c 'import socket,sys; socket.create_connection((sys.argv[1], int(sys.argv[2])), 2)' \
    "$host" "$port" >/dev/null 2>&1; then
    echo "$description unexpectedly connected." >&2
    exit 1
  fi
}

# The publication-only ingress plane is not an outbound or lateral trust
# plane. Its two services may return established DNAT traffic only.
namespace_connection_must_fail "$mosquitto_container" 172.31.82.19 8086 \
  "Mosquitto reached InfluxDB laterally over ingress"
namespace_connection_must_fail "$influx_container" 172.31.82.18 8883 \
  "InfluxDB reached Mosquitto laterally over ingress"
for namespace in "$mosquitto_container" "$influx_container"; do
  namespace_connection_must_fail "$namespace" 192.168.50.5 53 \
    "Ingress service reached LAN DNS"
  namespace_connection_must_fail "$namespace" 192.168.50.75 8123 \
    "Ingress service reached Home Assistant"
  namespace_connection_must_fail "$namespace" 192.168.50.82 22 \
    "Ingress service reached a host service"
  namespace_connection_must_fail "$namespace" 1.1.1.1 443 \
    "Ingress service reached the Internet"
done
docker compose exec -T node-red node -e '
  const net=require("net");
  const s=net.createConnection({host:"192.168.50.75",port:8123,timeout:3000},()=>{s.destroy();process.exit(0)});
  s.on("timeout",()=>{s.destroy();process.exit(1)}); s.on("error",()=>process.exit(1));
'
if docker compose exec -T node-red node -e '
  const net=require("net");
  const s=net.createConnection({host:"1.1.1.1",port:443,timeout:2000},()=>{s.destroy();process.exit(0)});
  s.on("timeout",()=>{s.destroy();process.exit(1)}); s.on("error",()=>process.exit(1));
'; then
  echo "Node-RED bypassed the reviewed HA/DNS-only egress boundary." >&2
  exit 1
fi

# Secret escrow must have been decrypted/cold-tested off-guest; external
# clients must have accepted delivery before their and the CA signing keys are
# removed from this broker VM.
[[ -f /etc/edsys-escrow/online-keys-finalized.json && ! -L /etc/edsys-escrow/online-keys-finalized.json && \
   $(stat -c '%u:%g:%a' /etc/edsys-escrow/online-keys-finalized.json) == 0:0:600 ]]
jq -e '
  (keys | sort) == (["archive_name","archive_sha256","finalized_utc","removed","schema"] | sort)
  and .schema == "edsys.edcore-automation.online-key-finalization.v1"
  and .removed == ["automation-ca","homeassistant","frigate","edsys-edge-livingroom"]
  and (.archive_sha256 | test("^[0-9a-f]{64}$"))
' /etc/edsys-escrow/online-keys-finalized.json >/dev/null
for forbidden_key in \
  "$secret_root/pki/ca/ca.key" \
  "$secret_root/pki/clients/homeassistant.key" \
  "$secret_root/pki/clients/frigate.key" \
  "$secret_root/pki/clients/edsys-edge-livingroom.key"; do
  [[ ! -e $forbidden_key && ! -L $forbidden_key ]] || {
    echo "Online-only key custody finalization is incomplete." >&2
    exit 1
  }
done

nft list table inet edsys_automation_filter >/dev/null
firewall_before=$(nft -j list table inet edsys_automation_filter | jq -cS .)
printf 'this is deliberately invalid nft syntax\n' >"$scratch/invalid-firewall.nft"
if /usr/local/sbin/edsys-automation-firewall --candidate "$scratch/invalid-firewall.nft" \
  >/dev/null 2>&1; then
  echo "Invalid firewall candidate was unexpectedly accepted." >&2
  exit 1
fi
firewall_after=$(nft -j list table inet edsys_automation_filter | jq -cS .)
[[ $firewall_after == "$firewall_before" ]] || {
  echo "Active firewall changed after an invalid atomic candidate." >&2
  exit 1
}

assert_published_port() {
  local service=$1 port=$2 container key
  container=$(docker compose ps -q "$service")
  key=$port/tcp
  docker inspect "$container" | jq -e --arg key "$key" --arg host "$LAN_BIND_ADDRESS" --arg port "$port" '
    .[0].HostConfig.PortBindings[$key] == [{"HostIp": $host, "HostPort": $port}]
    and .[0].NetworkSettings.Ports[$key] == [{"HostIp": $host, "HostPort": $port}]
  ' >/dev/null
  [[ $(docker compose port "$service" "$port") == "$LAN_BIND_ADDRESS:$port" ]]
}
assert_published_port node-red 1880
assert_published_port influxdb 8086
assert_published_port mosquitto 8883

# Image EXPOSE metadata may create null entries, but neither the daemon request
# nor its effective mapping may publish plaintext or internal MQTT listeners.
for container in "$mosquitto_container" "$influx_container" "$runtime_container" "$node_red_container" "$telegraf_container"; do
  docker inspect "$container" | jq -e '
    .[0].HostConfig.PortBindings["1883/tcp"] == null
    and .[0].HostConfig.PortBindings["8884/tcp"] == null
    and .[0].NetworkSettings.Ports["1883/tcp"] == null
    and .[0].NetworkSettings.Ports["8884/tcp"] == null
  ' >/dev/null
done

curl --fail --silent --show-error --cacert "$secret_root/pki/ca/ca.crt" \
  --resolve "edcore-automation.edsys.local:1880:192.168.50.82" \
  -o /dev/null https://edcore-automation.edsys.local:1880/
curl --fail --silent --show-error --cacert "$secret_root/pki/ca/ca.crt" \
  --resolve "edcore-automation.edsys.local:8086:192.168.50.82" \
  https://edcore-automation.edsys.local:8086/health | jq -e '.status == "pass"' >/dev/null

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
  docker run --rm --network "$network" \
    -v "$secret_root/pki/ca/ca.crt:/run/identity/ca.crt:ro" \
    -v "$secret_root/pki/clients/$identity.crt:/run/identity/client.crt:ro" \
    -v "$secret_root/pki/clients/$identity.key:/run/identity/client.key:ro" \
    "$mqtt_image" "$command" -i "$client_id" "$@" \
    --cafile /run/identity/ca.crt --cert /run/identity/client.crt --key /run/identity/client.key
}

mqtt_session_was_authenticated() {
  local client_id=$1 expected_identity=$2 started=$3 log_file=$4
  local deadline
  deadline=$((SECONDS + 5))
  while (( SECONDS < deadline )); do
    docker compose logs --no-color --since "$started" mosquitto >"$log_file" 2>/dev/null || true
    if grep -F "New client connected from " "$log_file" |
        grep -F " as $client_id " |
        grep -Fq "u'$expected_identity'"; then
      ! grep -Eiq "Client $client_id .*not authori[sz]ed|Client $client_id .*protocol error" \
        "$log_file" || return 1
      return 0
    fi
    sleep 0.2
  done
  return 1
}

mqtt_timeout_was_authenticated() {
  local error_file=$1 client_id=$2 expected_identity=$3 started=$4 log_file=$5
  grep -Fq 'Timed out' "$error_file" || return 1
  if grep -Eiq \
      'zero length clientid|client identifier|not authori[sz]ed|certificate|tls|ssl|connection (error|refused|lost)|protocol error|network error|host not found' \
      "$error_file"; then
    return 1
  fi

  # A timeout is meaningful only after this exact mTLS identity reached an
  # accepted broker session. A denied subscription or failed CONNECT must not
  # be mistaken for proof that the command namespace was empty.
  mqtt_session_was_authenticated \
    "$client_id" "$expected_identity" "$started" "$log_file" || return 1
  ! grep -Fq "Denied SUBSCRIBE from $client_id" "$log_file"
}

mqtt_acl_publish_must_fail() {
  local identity=$1 client_id=$2 audit_client_id=$3 topic=$4
  local publisher_output="$scratch/acl-$client_id.publisher.log"
  local publisher_broker_log="$scratch/acl-$client_id.broker.log"
  local audit_trace="$scratch/acl-$client_id.audit.trace"
  local audit_error="$scratch/acl-$client_id.audit.err"
  local audit_broker_log="$scratch/acl-$client_id.audit.log"
  local audit_started publisher_started audit_pid audit_rc

  audit_started=$(date -u +%Y-%m-%dT%H:%M:%S.%NZ)
  mqtt_base command-audit "$audit_client_id" mosquitto_sub \
    -d -h mosquitto -p 8883 -V mqttv5 -q 1 -W 10 -C 1 \
    -F 'EDSYS-AUDIT-DELIVERY:%p' -t "$topic" \
    >"$audit_trace" 2>"$audit_error" &
  audit_pid=$!

  # Do not publish until the exact read-only audit identity has both an
  # accepted mTLS broker session and a successful QoS 1 SUBACK. The audit
  # timeout is deliberately longer than this readiness deadline so the
  # subscriber cannot expire at the publication boundary.
  if ! mqtt_session_was_authenticated \
      "$audit_client_id" command-audit "$audit_started" "$audit_broker_log"; then
    kill "$audit_pid" 2>/dev/null || true
    wait "$audit_pid" 2>/dev/null || true
    echo "$identity command-write audit subscriber did not authenticate." >&2
    return 1
  fi
  local readiness_deadline=$((SECONDS + 5))
  while ! {
    grep -Fqx "Client $audit_client_id received SUBACK" "$audit_trace" &&
      grep -Fqx 'Subscribed (mid: 1): 1' "$audit_trace"
  }; do
    if (( SECONDS >= readiness_deadline )) || ! kill -0 "$audit_pid" 2>/dev/null; then
      kill "$audit_pid" 2>/dev/null || true
      wait "$audit_pid" 2>/dev/null || true
      echo "$identity command-write audit subscriber lacked a successful SUBACK." >&2
      return 1
    fi
    sleep 0.1
  done

  publisher_started=$(date -u +%Y-%m-%dT%H:%M:%S.%NZ)
  set +e
  mqtt_base "$identity" "$client_id" mosquitto_pub \
    -d -h mosquitto -p 8883 -V mqttv5 -q 1 -t "$topic" \
    -m '{"schema":"edsys.verification.noop.v1"}' \
    >"$publisher_output" 2>&1
  # Mosquitto 2.1 returns zero even when MQTT v5 PUBACK reason 135 denies the
  # publish, so the CLI status is deliberately not used as the ACL verdict.
  wait "$audit_pid"
  audit_rc=$?
  set -e

  if ! mqtt_session_was_authenticated \
      "$client_id" "$identity" "$publisher_started" "$publisher_broker_log"; then
    echo "$identity publisher did not establish the expected authenticated session." >&2
    return 1
  fi
  if [[ $(grep -Fxc "Client $client_id received PUBACK (Mid: 1, RC:135)" \
          "$publisher_output") -ne 1 || \
        $(grep -Fxc 'Warning: Publish 1 failed: Not authorized.' \
          "$publisher_output") -ne 1 || \
        $(grep -Fc ' received PUBACK ' "$publisher_output") -ne 1 ]]; then
    echo "$identity publisher lacked the exact MQTT v5 not-authorized PUBACK." >&2
    return 1
  fi
  if grep -Eiq \
      'zero length clientid|client identifier|certificate|tls|ssl|connection (error|refused|lost)|protocol error|network error|host not found' \
      "$publisher_output"; then
    echo "$identity publisher encountered a transport/authentication failure." >&2
    return 1
  fi
  if ! {
    [[ $audit_rc -eq 27 ]] &&
      ! grep -Fq "Client $audit_client_id received PUBLISH " "$audit_trace" &&
      ! grep -Fq 'EDSYS-AUDIT-DELIVERY:' "$audit_trace" &&
      mqtt_timeout_was_authenticated \
        "$audit_error" "$audit_client_id" command-audit \
        "$audit_started" "$audit_broker_log"
  }; then
    echo "$identity command write was delivered or its authenticated audit timed out incorrectly." >&2
    return 1
  fi
}

# Known-good mTLS publication plus explicit anonymous/no-certificate denial.
mqtt_base mqtt-health "v-health-$mqtt_id_suffix" mosquitto_pub \
  -h mosquitto -p 8883 -V mqttv5 -q 1 \
  -t edsys/test/v1/health/mqtt-health/verify -m '{"status":"probe"}'
no_cert_client_id="v-nocert-$mqtt_id_suffix"
readonly no_cert_client_id
no_cert_started=$(date -u +%Y-%m-%dT%H:%M:%S.%NZ)
set +e
docker run --rm --network "$network" -v "$secret_root/pki/ca/ca.crt:/run/ca.crt:ro" "$mqtt_image" \
  mosquitto_pub -i "$no_cert_client_id" -h mosquitto -p 8883 -V mqttv5 \
  --cafile /run/ca.crt -t edsys/test/v1/health/anonymous -m denied \
  >"$scratch/no-cert.out" 2>"$scratch/no-cert.err"
no_cert_rc=$?
set -e
if (( no_cert_rc == 0 )); then
  echo "MQTT accepted a client without a certificate." >&2
  exit 1
fi
if grep -Eiq 'zero length clientid|client identifier' "$scratch/no-cert.err"; then
  echo "Missing-certificate probe was rejected for its client ID, not client authentication." >&2
  exit 1
fi
no_cert_deadline=$((SECONDS + 5))
no_cert_reason=false
while (( SECONDS < no_cert_deadline )); do
  docker compose logs --no-color --since "$no_cert_started" mosquitto \
    >"$scratch/no-cert.log" 2>/dev/null || true
  if grep -Eiq 'peer did not return a certificate|alert certificate required|certificate required' \
      "$scratch/no-cert.err" "$scratch/no-cert.log"; then
    no_cert_reason=true
    break
  fi
  sleep 0.2
done
[[ $no_cert_reason == true ]] || {
  echo "Missing-certificate probe failed without a TLS client-authentication diagnostic." >&2
  exit 1
}

# Every broker-resident non-runtime identity must be denied a direct production
# command write. External-client keys have already left this VM.
acl_index=0
for identity in mqtt-health nodered telegraf event-replay command-audit; do
  mqtt_acl_publish_must_fail "$identity" "v-acl${acl_index}-$mqtt_id_suffix" \
    "v-aclm${acl_index}-$mqtt_id_suffix" \
    edsys/v1/command/ha/verification/nonexistent
  ((acl_index += 1))
done

# Legitimate retained state remains globally available without retaining an
# actuator request. The derived-state verification namespace is non-device.
state_topic=edsys/v1/state/derived/verification/retained
mqtt_base nodered "v-statepub-$mqtt_id_suffix" mosquitto_pub \
  -h mosquitto -p 8883 -V mqttv5 -q 1 -r \
  -t "$state_topic" -m '{"verification":true}'
state_value=$(mqtt_base nodered "v-statesub-$mqtt_id_suffix" mosquitto_sub \
  -h mosquitto -p 8883 -V mqttv5 -q 1 \
  --retained-only -W 5 -C 1 -t "$state_topic")
[[ "$state_value" == '{"verification":true}' ]] || { echo "Retained state acceptance failed." >&2; exit 1; }
mqtt_base nodered "v-statedel-$mqtt_id_suffix" mosquitto_pub \
  -h mosquitto -p 8883 -V mqttv5 -q 1 -r -n -t "$state_topic"

# Subscribe before injecting a retained request. RAP in the runtime must
# preserve and reject RETAIN, producing an ack but no production command.
python3 - <<'PY' >"$scratch/request.json"
import json
from datetime import datetime, timedelta, timezone
from uuid import uuid4
now = datetime.now(timezone.utc)
print(json.dumps({
    "schema": "edsys.command.request.v1",
    "id": str(uuid4()),
    "created_at": now.isoformat().replace("+00:00", "Z"),
    "expires_at": (now + timedelta(seconds=120)).isoformat().replace("+00:00", "Z"),
    "target": "ha/verification/nonexistent",
    "action": "noop",
    "parameters": {},
}, separators=(",", ":")))
PY

mqtt_base nodered "v-acksub-$mqtt_id_suffix" mosquitto_sub \
  -h mosquitto -p 8884 -V mqttv5 -q 1 -W 10 -C 1 \
  -t 'edsys/v1/automation/ack/#' >"$scratch/ack.json" 2>"$scratch/ack.err" &
ack_pid=$!
command_probe_started=$(date -u +%Y-%m-%dT%H:%M:%S.%NZ)
mqtt_base command-audit "v-cmdsub-$mqtt_id_suffix" mosquitto_sub \
  -h mosquitto -p 8883 -V mqttv5 -q 1 -W 6 -C 1 \
  -t 'edsys/v1/command/ha/#' >"$scratch/command.json" 2>"$scratch/command.err" &
command_pid=$!
sleep 1
mqtt_base nodered "v-reqpub-$mqtt_id_suffix" mosquitto_pub \
  -h mosquitto -p 8884 -V mqttv5 -q 1 -r \
  -t edsys/v1/automation/request/nodered -m "$(<"$scratch/request.json")"
wait "$ack_pid"
jq -e '.status == "rejected" and .reason_code == "retained_request"' "$scratch/ack.json" >/dev/null
set +e
wait "$command_pid"
command_rc=$?
set -e
if ! {
  [[ $command_rc -eq 27 && ! -s "$scratch/command.json" ]] &&
    mqtt_timeout_was_authenticated "$scratch/command.err" \
      "v-cmdsub-$mqtt_id_suffix" command-audit \
      "$command_probe_started" "$scratch/command.log"
}; then
  echo "Retained request emitted a production command or command probe failed." >&2
  exit 1
fi
# Clear the retained test request. The runtime may issue one additional
# rejection ack for the retained zero-length delete; no command can result.
mqtt_base nodered "v-reqdel-$mqtt_id_suffix" mosquitto_pub \
  -h mosquitto -p 8884 -V mqttv5 -q 1 -r -n \
  -t edsys/v1/automation/request/nodered

# Broker restart must not reveal any retained production command.
docker compose restart -t 30 mosquitto >/dev/null
for service in mosquitto automation-runtime node-red telegraf; do wait_healthy "$service"; done
post_restart_probe_started=$(date -u +%Y-%m-%dT%H:%M:%S.%NZ)
set +e
retained_command=$(mqtt_base command-audit "v-retcmd-$mqtt_id_suffix" mosquitto_sub \
  -h mosquitto -p 8883 -V mqttv5 -q 1 \
  --retained-only -W 3 -C 1 -t 'edsys/v1/command/ha/#' 2>"$scratch/post-restart.err")
retained_rc=$?
set -e
if ! {
  [[ $retained_rc -eq 27 && -z "$retained_command" ]] &&
    mqtt_timeout_was_authenticated "$scratch/post-restart.err" \
      "v-retcmd-$mqtt_id_suffix" command-audit \
      "$post_restart_probe_started" "$scratch/post-restart.log"
}; then
  echo "A retained production command survived broker restart or probe authentication failed." >&2
  exit 1
fi

# Project mode must use the reviewed flow path and root-backed key; its
# credentials ciphertext must decrypt without exposing cleartext.
docker compose exec -T node-red node <<'NODE'
const crypto = require("crypto");
const fs = require("fs");
if (fs.existsSync("/data/edcore-automation")) throw new Error("unintended non-Project flow path exists");
const config = JSON.parse(fs.readFileSync("/data/.config.projects.json", "utf8"));
const secret = fs.readFileSync("/run/secrets/node_red_credential_secret", "utf8").trim();
if (config.activeProject !== "edcore-automation") throw new Error("wrong active Project");
if (config.projects?.["edcore-automation"]?.credentialSecret !== secret) throw new Error("Project key mismatch");
const project = "/data/projects/edcore-automation";
if (!fs.statSync(`${project}/.git`).isDirectory()) throw new Error("Project is not Git-backed");
const flows = JSON.parse(fs.readFileSync(`${project}/flows.json`, "utf8"));
if (!flows.some(n => n.id === "tab-dependency-monitoring" && n.type === "tab")) throw new Error("reviewed dependency flow missing");
if (fs.readFileSync("/data/projects/edcore-automation/.edsys-release", "utf8").trim() !== "1.0.2") throw new Error("wrong Project release");
const encrypted = JSON.parse(fs.readFileSync(`${project}/flows_cred.json`, "utf8"));
if (Object.keys(encrypted).length !== 1 || typeof encrypted.$ !== "string") throw new Error("credentials not encrypted");
const key = crypto.createHash("sha256").update(secret).digest();
const iv = Buffer.from(encrypted.$.substring(0, 32), "hex");
const decipher = crypto.createDecipheriv("aes-256-ctr", key, iv);
JSON.parse(decipher.update(encrypted.$.substring(32), "base64", "utf8") + decipher.final("utf8"));
NODE
docker compose exec -T node-red sh -ec 'test -z "$(git -C /data/projects/edcore-automation status --porcelain)"'
docker compose --profile tools run --rm --no-deps event-harness self-test
"$stack_dir/scripts/backup.sh" >/dev/null
"$stack_dir/scripts/restore-test.sh" >/dev/null
systemctl enable --now edsys-automation-backup.timer edsys-automation-restore-test.timer >/dev/null

printf 'EdCore automation live verification passed: isolation, TLS/auth/ACLs, retained state, retained-request rejection, command non-retention across restart, Project encryption, backup/cold restore, and service health.\n'
