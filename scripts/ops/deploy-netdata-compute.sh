#!/usr/bin/env bash
set -euo pipefail
umask 077

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
verify_script="${repo_root}/scripts/ops/verify-netdata-compute.py"
parent_ip="192.168.50.50"
group_name="edsys-compute"
children=(pve-edcore pve-node0 pve-node1 pve-node2)

usage() {
  cat <<'EOF'
Usage: deploy-netdata-compute.sh --check | --apply

--check  Verify the exact five-node parent/child topology without changing it.
--apply  Back up configuration, align packages, deploy the topology, and verify it.
EOF
}

case "${1:-}" in
  --check)
    exec python3 "$verify_script"
    ;;
  --apply) ;;
  *) usage >&2; exit 2 ;;
esac

if [[ ${EUID} -ne 0 ]]; then
  echo "Run --apply as root." >&2
  exit 2
fi

for command in curl getent install python3 scp ssh systemctl uuidgen; do
  command -v "$command" >/dev/null || {
    echo "Required command is missing: $command" >&2
    exit 1
  }
done
operator_user="${SUDO_USER:-jeremy}"
operator_home="$(getent passwd "$operator_user" | cut -d: -f6)"
[[ -n "$operator_home" && -r "${operator_home}/.ssh/config" && \
   -r "${operator_home}/.ssh/id_ed25519" && \
   -r "${operator_home}/.ssh/known_hosts" ]] || {
  echo "The EdSys operator SSH configuration is unavailable for ${operator_user}." >&2
  exit 1
}
ssh_options=(
  -F "${operator_home}/.ssh/config"
  -i "${operator_home}/.ssh/id_ed25519"
  -o IdentitiesOnly=yes
  -o "UserKnownHostsFile=${operator_home}/.ssh/known_hosts"
  -o BatchMode=yes
)
scp_options=(
  -q
  -F "${operator_home}/.ssh/config"
  -i "${operator_home}/.ssh/id_ed25519"
  -o IdentitiesOnly=yes
  -o "UserKnownHostsFile=${operator_home}/.ssh/known_hosts"
  -o BatchMode=yes
)
[[ -x "$verify_script" ]] || chmod 0755 "$verify_script"
[[ -r /usr/share/keyrings/netdata-archive-keyring.gpg ]] || {
  echo "Netdata public package keyring is missing on 9950x." >&2
  exit 1
}

stamp="$(date -u +%Y%m%dT%H%M%SZ)"
parent_backup="/var/backups/edsys-netdata-compute/${stamp}"
tmpdir="$(mktemp -d)"
config_mutated=0
backup_started=0

cleanup() {
  rm -rf -- "$tmpdir"
}

restore_local_file() {
  local target="$1" label="$2"
  if [[ -f "${parent_backup}/${label}.exists" ]]; then
    cp -a -- "${parent_backup}/${label}" "$target"
  else
    rm -f -- "$target"
  fi
}

rollback_configs() {
  local status=$?
  trap - ERR
  if (( config_mutated == 1 )); then
    echo "Deployment failed; restoring the pre-change Netdata configuration." >&2
    restore_local_file /etc/netdata/netdata.conf netdata.conf || true
    restore_local_file /etc/netdata/stream.conf stream.conf || true
    restore_local_file /etc/netdata/edsys-compute-stream.key stream.key || true
    systemctl restart netdata || true
    for child in "${children[@]}"; do
      ssh "${ssh_options[@]}" "root@${child}" \
        "set -e; backup='${parent_backup}'; \
         if test -f \"\$backup/netdata.conf.exists\"; then cp -a \"\$backup/netdata.conf\" /etc/netdata/netdata.conf; else rm -f /etc/netdata/netdata.conf; fi; \
         if test -f \"\$backup/stream.conf.exists\"; then cp -a \"\$backup/stream.conf\" /etc/netdata/stream.conf; else rm -f /etc/netdata/stream.conf; fi; \
         systemctl restart netdata" || true
    done
  fi
  if (( backup_started == 1 )); then
    echo "Private backup material remains under ${parent_backup} on every host." >&2
  else
    echo "Preflight failed before live configuration or package changes." >&2
  fi
  cleanup
  exit "$status"
}
trap cleanup EXIT
trap rollback_configs ERR

echo "Preflighting child hosts and the parent streaming endpoint."
curl -fsS --max-time 10 "http://127.0.0.1:19999/api/v1/info" >/dev/null
for child in "${children[@]}"; do
  ssh "${ssh_options[@]}" "root@${child}" \
    "test \"\$(hostname)\" = '${child}'; curl -fsS --max-time 10 http://${parent_ip}:19999/api/v1/info >/dev/null"
done

install -d -m 0700 "$parent_backup"
for item in \
  "/etc/netdata/netdata.conf:netdata.conf" \
  "/etc/netdata/stream.conf:stream.conf" \
  "/etc/netdata/edsys-compute-stream.key:stream.key"; do
  target="${item%%:*}"
  label="${item##*:}"
  if [[ -e "$target" ]]; then
    cp -a -- "$target" "${parent_backup}/${label}"
    : >"${parent_backup}/${label}.exists"
  fi
done
systemctl is-enabled netdata >"${parent_backup}/netdata.enabled" 2>&1 || true
systemctl is-active netdata >"${parent_backup}/netdata.active" 2>&1 || true

for child in "${children[@]}"; do
  ssh "${ssh_options[@]}" "root@${child}" \
    "set -e; backup='${parent_backup}'; install -d -m 0700 \"\$backup\"; \
     if test -e /etc/netdata/netdata.conf; then cp -a /etc/netdata/netdata.conf \"\$backup/netdata.conf\"; : >\"\$backup/netdata.conf.exists\"; fi; \
     if test -e /etc/netdata/stream.conf; then cp -a /etc/netdata/stream.conf \"\$backup/stream.conf\"; : >\"\$backup/stream.conf.exists\"; fi; \
     if test -d /var/lib/netdata/cloud.d; then cp -a /var/lib/netdata/cloud.d \"\$backup/cloud.d\"; fi; \
     dpkg-query -W -f='\${Package}\t\${Version}\n' 'netdata*' >\"\$backup/packages.tsv\" 2>/dev/null || true; \
     systemctl is-enabled netdata >\"\$backup/netdata.enabled\" 2>&1 || true; \
     systemctl is-active netdata >\"\$backup/netdata.active\" 2>&1 || true"
done
backup_started=1

echo "Aligning Netdata edge packages on the four Debian 13 Proxmox hosts."
for child in "${children[@]}"; do
  scp "${scp_options[@]}" /usr/share/keyrings/netdata-archive-keyring.gpg "root@${child}:/tmp/netdata-archive-keyring.gpg"
  ssh "${ssh_options[@]}" "root@${child}" 'bash -s' <<'REMOTE_INSTALL'
set -euo pipefail
install -m 0444 -o root -g root /tmp/netdata-archive-keyring.gpg /usr/share/keyrings/netdata-archive-keyring.gpg
rm -f /tmp/netdata-archive-keyring.gpg
cat >/etc/apt/sources.list.d/netdata-edge.sources <<'EOF'
X-Repolib-Name: Netdata edge repository
Types: deb
URIs: http://repository.netdata.cloud/repos/edge/debian/
Suites: trixie/
Signed-By: /usr/share/keyrings/netdata-archive-keyring.gpg
By-Hash: Yes
Enabled: Yes

X-Repolib-Name: Netdata repository configuration repository
Types: deb
URIs: http://repository.netdata.cloud/repos/repoconfig/debian/
Suites: trixie/
Signed-By: /usr/share/keyrings/netdata-archive-keyring.gpg
By-Hash: Yes
Enabled: Yes
EOF
chmod 0644 /etc/apt/sources.list.d/netdata-edge.sources
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
if ! apt-cache show libbson-1.0-0t64 libmongoc-1.0-0t64 >/dev/null 2>&1; then
  echo "Debian 13 Netdata dependencies are unavailable; verify this host's active APT suites." >&2
  exit 1
fi
apt-get install -y netdata-repo-edge netdata
systemctl enable netdata >/dev/null
REMOTE_INSTALL
done

if [[ -s /etc/netdata/edsys-compute-stream.key ]] && \
   grep -Eq '^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$' \
     /etc/netdata/edsys-compute-stream.key; then
  stream_key="$(tr -d '\r\n' </etc/netdata/edsys-compute-stream.key)"
else
  stream_key="$(uuidgen)"
  printf '%s\n' "$stream_key" >"${tmpdir}/stream.key"
  install -m 0600 -o root -g root "${tmpdir}/stream.key" /etc/netdata/edsys-compute-stream.key
fi

cat >"${tmpdir}/parent-netdata.conf" <<EOF
# Managed by EdSys deploy-netdata-compute.sh.
[global]
    hostname = 9950x

[host labels]
    group = ${group_name}
EOF
cat >"${tmpdir}/parent-stream.conf" <<EOF
# Managed by EdSys deploy-netdata-compute.sh. Contains a private streaming key.
[${stream_key}]
    type = api
    enabled = yes
    allow from = 192.168.50.51 192.168.50.52 192.168.50.53 192.168.50.54
    db = dbengine
    health enabled = auto
    postpone alerts on connect = 1m
    enable replication = yes
    replication period = 1d
EOF
install -m 0644 -o root -g root "${tmpdir}/parent-netdata.conf" /etc/netdata/netdata.conf
install -m 0600 -o root -g root "${tmpdir}/parent-stream.conf" /etc/netdata/stream.conf
config_mutated=1

systemctl restart netdata
for _ in {1..30}; do
  if curl -fsS --max-time 3 "http://127.0.0.1:19999/api/v1/info" >/dev/null 2>&1; then
    break
  fi
  sleep 1
done
curl -fsS --max-time 3 "http://127.0.0.1:19999/api/v1/info" >/dev/null

echo "Configuring and starting the four Netdata children."
for child in "${children[@]}"; do
  cat >"${tmpdir}/${child}-netdata.conf" <<EOF
# Managed by EdSys deploy-netdata-compute.sh.
[global]
    hostname = ${child}

[host labels]
    group = ${group_name}
EOF
  cat >"${tmpdir}/${child}-stream.conf" <<EOF
# Managed by EdSys deploy-netdata-compute.sh. Contains a private streaming key.
[stream]
    enabled = yes
    destination = ${parent_ip}:19999
    api key = ${stream_key}
    enable compression = yes
    buffer size = 10MiB
    reconnect delay = 15s
EOF
  scp "${scp_options[@]}" "${tmpdir}/${child}-netdata.conf" "root@${child}:/tmp/netdata.conf.edsys"
  scp "${scp_options[@]}" "${tmpdir}/${child}-stream.conf" "root@${child}:/tmp/stream.conf.edsys"
  ssh "${ssh_options[@]}" "root@${child}" \
    "set -e; install -m 0644 -o root -g root /tmp/netdata.conf.edsys /etc/netdata/netdata.conf; \
     install -m 0600 -o root -g root /tmp/stream.conf.edsys /etc/netdata/stream.conf; \
     rm -f /tmp/netdata.conf.edsys /tmp/stream.conf.edsys; \
     systemctl restart netdata; systemctl is-active --quiet netdata"
done

echo "Waiting for all four child streams to become reachable on 9950x."
for _ in {1..90}; do
  if python3 "$verify_script" >/dev/null 2>&1; then
    break
  fi
  sleep 2
done
python3 "$verify_script"

config_mutated=0
trap - ERR
echo "Private pre-change backups: ${parent_backup} on 9950x and each child."
echo "Netdata Cloud claim state was preserved; the parent/child node identity is unchanged."
