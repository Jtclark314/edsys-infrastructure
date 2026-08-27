#!/usr/bin/env bash
set -Eeuo pipefail

stack_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
readonly stack_dir
readonly secret_root=/etc/edsys-secrets/edcore-automation
readonly influx_image='docker.io/library/influxdb:2.8.0@sha256:09a5361809c771d863bcfa844a09598a82a6d9bbba1c9a9e2fa312e310572a14'
readonly data_network=edsys-edcore-automation-data
readonly ingress_network=edsys-edcore-automation-ingress
readonly ingress_bridge=br-ed-ingress
readonly egress_network=edsys-edcore-automation-egress
readonly egress_bridge=br-edsys-egress
readonly mosquitto_container=edsys-edcore-automation-mosquitto-1
readonly influxdb_container=edsys-edcore-automation-influxdb-1
cd "$stack_dir"

[[ ${EUID} -eq 0 ]] || { echo "Run as root on edcore-automation." >&2; exit 1; }
[[ $(hostname -s) == edcore-automation ]] || { echo "Refusing to deploy outside edcore-automation." >&2; exit 1; }

# Validate the complete transferred tree before it can drive Docker as root.
# A prior deployment may already have the three Mosquitto sources in runtime
# ownership, so choose one of two exact (never mixed/permissive) states.
guard_phase=--transfer
if [[ $(stat -c '%u:%g:%a' mosquitto/aclfile) == 1883:0:640 ]]; then
  guard_phase=--runtime
fi
"$stack_dir/scripts/source-guard.sh" "$guard_phase"
install -o root -g root -m 0755 scripts/source-guard.sh /usr/local/sbin/edsys-automation-source-guard

[[ -f .env ]] || { echo "Missing runtime .env; review and copy .env.example first." >&2; exit 1; }
set -a
# shellcheck disable=SC1091
source .env
set +a
[[ ${LAN_BIND_ADDRESS:-} == 192.168.50.82 ]] || { echo "LAN_BIND_ADDRESS must be the accepted guest address." >&2; exit 1; }
ip -4 -o address show | awk '{print $4}' | grep -qx '192.168.50.82/24' || {
  echo "The accepted static address is not assigned to this guest." >&2
  exit 1
}

# Both non-internal container planes have fixed, audited, non-overlapping
# subnets. The ingress plane exists only so Docker can implement exact host
# port publication; nftables denies every new connection originating there.
# Refuse unsafe interface names, stale/colliding ingress objects, unexpected
# endpoints, or either plane overlapping another Docker network/host route.
for bridge in "$ingress_bridge" "$egress_bridge"; do
  [[ $bridge =~ ^[a-z0-9][a-z0-9-]*$ && ${#bridge} -le 15 ]] || {
    echo "Fixed bridge name is not IFNAMSIZ-safe: $bridge" >&2
    exit 1
  }
done

docker_network_id_text=$(docker network ls --quiet)
if [[ -n $docker_network_id_text ]]; then
  mapfile -t docker_network_ids <<<"$docker_network_id_text"
  docker_networks_json=$(docker network inspect "${docker_network_ids[@]}")
else
  docker_network_ids=()
  docker_networks_json='[]'
fi

# A previous failed Docker create or an older reviewed release may leave the
# stable network name behind. Reuse it only when its complete security
# boundary and every attached endpoint still match this release. Never delete
# or silently recreate a stale/in-use network here.
ingress_count=$(jq -r --arg network "$ingress_network" \
  '[.[] | select(.Name == $network)] | length' <<<"$docker_networks_json")
[[ $ingress_count =~ ^[0-9]+$ && $ingress_count -le 1 ]] || {
  echo "Unable to establish a unique ingress network preflight." >&2
  exit 1
}
if [[ $ingress_count -eq 1 ]]; then
  # Docker 29 may serialize an unset IP range as `"IPRange":""`. Accept
  # only that no-op representation or an absent key; exact object equality
  # still rejects null/nonempty values, extra keys, and additional entries.
  jq -e \
    --arg network "$ingress_network" \
    --arg bridge "$ingress_bridge" \
    --arg mosquitto "$mosquitto_container" \
    --arg influxdb "$influxdb_container" '
      [.[] | select(.Name == $network)] as $matches |
      ($matches | length) == 1 and
      ($matches[0] |
        .Driver == "bridge" and
        .Scope == "local" and
        .Internal == false and
        .Attachable == false and
        .Ingress == false and
        .ConfigOnly == false and
        .EnableIPv6 == false and
        .Options == {
          "com.docker.network.bridge.enable_icc": "false",
          "com.docker.network.bridge.name": $bridge
        } and
        .IPAM.Driver == "default" and
        (.IPAM.Options == null or .IPAM.Options == {}) and
        (
          .IPAM.Config == [{
            "Subnet": "172.31.82.16/29",
            "Gateway": "172.31.82.17"
          }] or
          .IPAM.Config == [{
            "Subnet": "172.31.82.16/29",
            "IPRange": "",
            "Gateway": "172.31.82.17"
          }]
        ) and
        .Labels["com.docker.compose.project"] == "edsys-edcore-automation" and
        .Labels["com.docker.compose.network"] == "ingress" and
        ((.Containers // {}) | to_entries | length) <= 2 and
        ((.Containers // {}) | to_entries | all(
          (.value.Name == $mosquitto and
           .value.IPv4Address == "172.31.82.18/29" and
           (.value.IPv6Address // "") == "") or
          (.value.Name == $influxdb and
           .value.IPv4Address == "172.31.82.19/29" and
           (.value.IPv6Address // "") == "")
        ))
      )
    ' <<<"$docker_networks_json" >/dev/null || {
      echo "Existing $ingress_network is stale, foreign, or has unexpected endpoints; inspect it without deleting it." >&2
      exit 1
    }
  ip link show dev "$ingress_bridge" >/dev/null 2>&1 || {
    echo "Existing $ingress_network has no matching $ingress_bridge link; inspect it without deleting it." >&2
    exit 1
  }
else
  if ip link show dev "$ingress_bridge" >/dev/null 2>&1; then
    echo "$ingress_bridge exists without the reviewed $ingress_network object; inspect it without deleting it." >&2
    exit 1
  fi
fi

jq -e --arg network "$ingress_network" --arg bridge "$ingress_bridge" '
  all(.[];
    (.Name == $network) or
    ((.Options // {})["com.docker.network.bridge.name"] // "") != $bridge
  )
' <<<"$docker_networks_json" >/dev/null || {
  echo "Another Docker network claims fixed bridge $ingress_bridge; inspect it without deleting it." >&2
  exit 1
}

{
  jq -r --arg egress "$egress_network" --arg ingress "$ingress_network" '
    .[] |
    select(.Name != $egress and .Name != $ingress) |
    .IPAM.Config[]?.Subnet // empty
  ' <<<"$docker_networks_json"
  ip -j route show table all | jq -r --arg egress "$egress_bridge" --arg ingress "$ingress_bridge" '
    .[] |
    select(.dev != $egress and .dev != $ingress) |
    .dst // empty
  '
} | python3 -c '
import ipaddress
import sys
targets = (
    ipaddress.ip_network("172.31.82.0/28"),
    ipaddress.ip_network("172.31.82.16/29"),
)
for raw in sys.stdin:
    try:
        candidate = ipaddress.ip_network(raw.strip(), strict=False)
    except ValueError:
        continue
    for target in targets:
        if candidate.version == target.version and candidate.overlaps(target):
            raise SystemExit(
                f"automation {target} overlaps existing network/route: {candidate}"
            )
'

required=(
  pki/ca/ca.crt
  pki/servers/mosquitto.crt pki/servers/mosquitto.key
  pki/servers/node-red.crt pki/servers/node-red.key
  pki/servers/influxdb.crt pki/servers/influxdb.key
  pki/clients/mqtt-health.crt pki/clients/mqtt-health.key
  pki/clients/nodered.crt pki/clients/nodered.key
  pki/clients/automation-runtime.crt pki/clients/automation-runtime.key
  pki/clients/telegraf.crt pki/clients/telegraf.key
  pki/clients/event-replay.crt pki/clients/event-replay.key
  pki/clients/command-audit.crt pki/clients/command-audit.key
  node-red/admin_password_hash node-red/credential_secret
  influxdb/admin_password influxdb/admin_token
)
for relative in "${required[@]}"; do
  [[ -s "$secret_root/$relative" ]] || { echo "Missing required runtime file: $secret_root/$relative" >&2; exit 1; }
done
for certificate in "$secret_root"/pki/servers/*.crt "$secret_root"/pki/clients/*.crt; do
  openssl verify -CAfile "$secret_root/pki/ca/ca.crt" "$certificate" >/dev/null
  openssl x509 -checkend $((30 * 86400)) -noout -in "$certificate" >/dev/null || {
    echo "Certificate expires in less than 30 days: $certificate" >&2
    exit 1
  }
done

# Mosquitto 2.1 warns on permissive ACL/config ownership and plans to reject it
# in a future release. UID 1883 owns the sources and container group 0 reads.
chown 1883:0 mosquitto/mosquitto.conf mosquitto/aclfile mosquitto/aclfile-internal
chmod 0640 mosquitto/mosquitto.conf mosquitto/aclfile mosquitto/aclfile-internal
/usr/local/sbin/edsys-automation-source-guard --runtime

# Refresh root-owned units on every reviewed deployment so ExecStartPre guards
# cannot lag the source contract.
for unit in systemd/*; do
  install -o root -g root -m 0644 "$unit" "/etc/systemd/system/$(basename "$unit")"
done
systemctl daemon-reload

# Refresh and atomically apply the reviewed firewall before Docker can create
# or attach the publication-only ingress bridge. A failed replacement restores
# the prior canonical rules and stops deployment before Compose mutation.
"$stack_dir/scripts/install-firewall.sh" --apply

docker compose config --quiet
docker compose -f compose.yaml -f compose.bootstrap.yaml config --quiet
docker compose pull --ignore-buildable
docker compose build --pull
docker compose run --rm --no-deps -e INFLUX_TOKEN=config-validation-placeholder \
  --entrypoint telegraf telegraf config check --strict-env-handling \
  --config /etc/telegraf/telegraf.conf

# Start the steady definitions first. Only a database whose public setup API
# reports `allowed:true` is recreated once with bootstrap credentials.
docker compose up -d --remove-orphans mosquitto influxdb

wait_healthy() {
  local service=$1 timeout=$2 container deadline
  container=$(docker compose ps -q "$service")
  [[ -n "$container" ]] || { echo "$service has no container." >&2; return 1; }
  deadline=$((SECONDS + timeout))
  while (( SECONDS < deadline )); do
    state=$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$container" 2>/dev/null || true)
    [[ "$state" == healthy ]] && return 0
    [[ "$state" == exited || "$state" == dead ]] && break
    sleep 3
  done
  docker compose ps "$service"
  docker compose logs --tail 100 "$service"
  echo "$service did not become healthy." >&2
  return 1
}
wait_healthy mosquitto 120
wait_healthy influxdb 180

assert_published_port() {
  local service=$1 port=$2 container key
  container=$(docker compose ps -q "$service")
  [[ -n $container ]] || { echo "$service has no container for its publication gate." >&2; return 1; }
  key=$port/tcp
  docker inspect "$container" | jq -e --arg key "$key" --arg host "$LAN_BIND_ADDRESS" --arg port "$port" '
    .[0].HostConfig.PortBindings[$key] == [{"HostIp": $host, "HostPort": $port}]
    and .[0].NetworkSettings.Ports[$key] == [{"HostIp": $host, "HostPort": $port}]
  ' >/dev/null || {
    echo "$service does not have the exact effective $LAN_BIND_ADDRESS:$port publication." >&2
    return 1
  }
  [[ $(docker compose port "$service" "$port") == "$LAN_BIND_ADDRESS:$port" ]] || {
    echo "Compose did not report the exact effective $service publication." >&2
    return 1
  }
}

assert_published_port mosquitto 8883
assert_published_port influxdb 8086

# `jq -e` treats the JSON boolean false as a failing result. Slurp exactly one
# object, require a boolean, and convert it to a nonempty raw string inside jq
# so an already-initialized InfluxDB follows the idempotent false branch.
setup_allowed=$(curl --fail --silent --show-error \
  --cacert "$secret_root/pki/ca/ca.crt" \
  --resolve edcore-automation.edsys.local:8086:192.168.50.82 \
  https://edcore-automation.edsys.local:8086/api/v2/setup | jq -ers '
    if length == 1
       and (.[0] | type) == "object"
       and (.[0].allowed | type) == "boolean"
    then (.[0].allowed | tostring)
    else error("InfluxDB setup response must contain one boolean allowed field")
    end
  ')
if [[ $setup_allowed == true ]]; then
  docker compose -f compose.yaml -f compose.bootstrap.yaml up -d --force-recreate influxdb
  wait_healthy influxdb 180
  assert_published_port influxdb 8086
elif [[ $setup_allowed != false ]]; then
  echo "InfluxDB setup state is not boolean." >&2
  exit 1
fi

influx_admin_cli() {
  docker run --rm --read-only --cap-drop ALL --security-opt no-new-privileges:true \
    --network "$data_network" --tmpfs /tmp:rw,noexec,nosuid,nodev,size=32m \
    -e SSL_CERT_FILE=/run/identity/ca.crt \
    -v "$secret_root/pki/ca/ca.crt:/run/identity/ca.crt:ro" \
    -v "$secret_root/influxdb/admin_token:/run/identity/admin_token:ro" \
    --entrypoint /bin/sh "$influx_image" -ec '
      export INFLUX_TOKEN="$(cat /run/identity/admin_token)"
      exec influx "$@"
    ' sh "$@"
}

bucket_json=$(influx_admin_cli bucket list --host https://influxdb:8086 \
  --org "$INFLUXDB_ORG" --name "$INFLUXDB_BUCKET" --json)
bucket_id=$(jq -er 'if type == "array" and length == 1 then .[0].id else error("expected one bucket") end' <<<"$bucket_json")
[[ "$bucket_id" =~ ^[0-9a-f]{16}$ ]] || { echo "InfluxDB bucket ID has an unexpected format." >&2; exit 1; }

create_scoped_token() {
  local role=$1 permission=$2 output=$3
  [[ ! -s "$output" ]] || return 0
  local response token temporary
  response=$(influx_admin_cli auth create --host https://influxdb:8086 \
    --org "$INFLUXDB_ORG" --description "edcore-$role" \
    "--$permission-bucket" "$bucket_id" --json)
  token=$(jq -er '.token | select(type == "string" and length >= 32)' <<<"$response")
  temporary="$output.new"
  umask 027
  printf '%s\n' "$token" >"$temporary"
  chown root:root "$temporary"
  chmod 0440 "$temporary"
  mv -f "$temporary" "$output"
  unset token response
}

create_scoped_token telegraf write "$secret_root/influxdb/telegraf_token"
create_scoped_token grafana read "$secret_root/influxdb/grafana_token"

# Remove both bootstrap secrets and every variable added by the reviewed
# one-time overlay before any dependent service is allowed to start. The
# pinned image itself legitimately provides DOCKER_INFLUXDB_INIT_CLI_CONFIG_NAME.
docker compose up -d --force-recreate influxdb
wait_healthy influxdb 180
assert_published_port influxdb 8086
influx_container=$(docker compose ps -q influxdb)
if ! influx_mount_destinations=$(docker inspect --format \
  '{{range .Mounts}}{{println .Destination}}{{end}}' "$influx_container"); then
  echo "Unable to inspect steady InfluxDB mounts." >&2
  exit 1
fi
if grep -Eq 'influxdb_admin_(password|token)' <<<"$influx_mount_destinations"; then
  echo "Steady InfluxDB still mounts a bootstrap credential." >&2
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

docker compose up -d --build --remove-orphans
for service in mosquitto influxdb automation-runtime node-red telegraf; do
  wait_healthy "$service" 240
done
assert_published_port mosquitto 8883
assert_published_port influxdb 8086
assert_published_port node-red 1880

docker compose exec -T node-red sh -ec \
  'test "$(cat /data/.edsys-health/mqtt.status)" = connected'
node_red_started=$(docker inspect --format '{{.State.StartedAt}}' "$(docker compose ps -q node-red)")
if docker compose logs --no-color --since "$node_red_started" node-red | \
  grep -Eiq 'circular config node dependency|missing broker configuration|flows stopped due to missing node types'; then
  echo "Node-RED reported a flow/configuration startup failure." >&2
  exit 1
fi
telegraf_started=$(docker inspect --format '{{.State.StartedAt}}' "$(docker compose ps -q telegraf)")
telegraf_logs=$(docker compose logs --no-color --since "$telegraf_started" telegraf)
[[ $(grep -Fc '[inputs.mqtt_consumer] Connected' <<<"$telegraf_logs") -ge 2 ]] || {
  echo "Telegraf did not establish both bounded MQTT consumers." >&2
  exit 1
}
if grep -Eq '(^|[[:space:]])E!' <<<"$telegraf_logs"; then
  echo "Telegraf logged a startup/runtime error." >&2
  exit 1
fi

# Prove the active Project, flow location, Git backing, credential encryption,
# and mounted root-owned secret agree without emitting the key or cleartext.
docker compose exec -T node-red node <<'NODE'
const crypto = require("crypto");
const fs = require("fs");
const config = JSON.parse(fs.readFileSync("/data/.config.projects.json", "utf8"));
const secret = fs.readFileSync("/run/secrets/node_red_credential_secret", "utf8").trim();
if (config.activeProject !== "edcore-automation") throw new Error("wrong active Project");
if (config.projects?.["edcore-automation"]?.credentialSecret !== secret) throw new Error("Project key mismatch");
if (!fs.statSync("/data/projects/edcore-automation/.git").isDirectory()) throw new Error("Project is not Git-backed");
const flows = JSON.parse(fs.readFileSync("/data/projects/edcore-automation/flows.json", "utf8"));
if (!flows.some(node => node.id === "tab-dependency-monitoring" && node.type === "tab")) throw new Error("reviewed flow not loaded");
if (fs.readFileSync("/data/projects/edcore-automation/.edsys-release", "utf8").trim() !== "1.0.2") throw new Error("wrong Project release");
const encrypted = JSON.parse(fs.readFileSync("/data/projects/edcore-automation/flows_cred.json", "utf8"));
if (Object.keys(encrypted).length !== 1 || typeof encrypted.$ !== "string") throw new Error("credentials not encrypted");
const key = crypto.createHash("sha256").update(secret).digest();
const iv = Buffer.from(encrypted.$.substring(0, 32), "hex");
const decipher = crypto.createDecipheriv("aes-256-ctr", key, iv);
JSON.parse(decipher.update(encrypted.$.substring(32), "base64", "utf8") + decipher.final("utf8"));
console.log("node_red_project_encryption=passed");
NODE

docker compose --profile tools run --rm --no-deps event-harness self-test
docker compose ps
systemctl enable edsys-automation-compose.service >/dev/null
printf 'Deployment completed with scoped InfluxDB tokens and encrypted Git-backed Node-RED Project credentials.\n'
