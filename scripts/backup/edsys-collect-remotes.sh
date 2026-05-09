#!/usr/bin/env bash
set -Eeuo pipefail

CONFIG_FILE="${EDSYS_BACKUP_ENV:-/etc/edsys-backup/backup.env}"
if [[ -f "${CONFIG_FILE}" ]]; then
  # shellcheck disable=SC1090
  source "${CONFIG_FILE}"
fi

: "${BACKUP_ROOT:=/srv/edsys-backup}"
: "${STAGING_DIR:=${BACKUP_ROOT}/staging}"

RUN_ID="${1:-$(date -u +%Y%m%dT%H%M%SZ)}"
OUT_DIR="${STAGING_DIR}/remote-exports/${RUN_ID}"
mkdir -p "${OUT_DIR}"

MANIFEST="${OUT_DIR}/MANIFEST.txt"
: > "${MANIFEST}"

log() {
  echo "[$(date -Is)] $*" | tee -a "${MANIFEST}"
}

collect_tar() {
  local label="$1"
  local user="$2"
  local host="$3"
  shift 3
  local paths=("$@")
  local output="${OUT_DIR}/${label}.tar.gz"
  local path_args
  path_args="$(printf ' %q' "${paths[@]}")"

  log "Collecting ${label} from ${user}@${host}: ${paths[*]}"
  if [[ "${user}" == "root" ]]; then
    ssh -o BatchMode=yes -o ConnectTimeout=8 "${user}@${host}" "tar -czf -${path_args} 2>/dev/null" > "${output}" \
      && log "OK ${label} ${output}" \
      || { rm -f "${output}"; log "FAILED ${label}"; }
  else
    ssh -o BatchMode=yes -o ConnectTimeout=8 "${user}@${host}" "sudo -n tar -czf -${path_args} 2>/dev/null" > "${output}" \
      && log "OK ${label} ${output}" \
      || { rm -f "${output}"; log "FAILED ${label}"; }
  fi
}

collect_text() {
  local label="$1"
  local user="$2"
  local host="$3"
  local command="$4"
  local output="${OUT_DIR}/${label}.txt"

  log "Collecting text ${label} from ${user}@${host}"
  ssh -o BatchMode=yes -o ConnectTimeout=8 "${user}@${host}" "${command}" > "${output}" 2>&1 \
    && log "OK ${label} ${output}" \
    || { log "FAILED ${label}; see ${output}"; }
}

collect_text "9950x-local-baseline" "jeremy" "127.0.0.1" "hostnamectl; ip -br addr; df -h; docker ps --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}' 2>/dev/null || true"

collect_tar "edcore-dnsmasq" "jeremy" "192.168.50.1" "/etc/dnsmasq.d" "/var/lib/misc/dnsmasq.leases" "/etc/hostname" "/etc/hosts"
collect_tar "pihole-primary-config" "jeremy" "192.168.50.5" "/etc/pihole" "/etc/dnsmasq.d" "/etc/hostname" "/etc/hosts"
collect_tar "pihole-secondary-config" "jeremy" "192.168.50.6" "/etc/pihole" "/etc/dnsmasq.d" "/etc/hostname" "/etc/hosts"

collect_tar "pve-node0-config" "root" "192.168.50.51" "/etc/pve" "/etc/network/interfaces" "/etc/hosts" "/etc/hostname"
collect_tar "pve-node1-config" "root" "192.168.50.52" "/etc/pve" "/etc/network/interfaces" "/etc/hosts" "/etc/hostname"
collect_tar "pve-node2-config" "root" "192.168.50.53" "/etc/pve" "/etc/network/interfaces" "/etc/hosts" "/etc/hostname"

collect_text "arr-server-docker-baseline" "jeremy" "192.168.50.201" "hostnamectl; docker ps --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}' 2>/dev/null || true; findmnt"
collect_text "family-services-docker-baseline" "jeremy" "192.168.50.78" "hostnamectl; docker ps --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}' 2>/dev/null || true; findmnt"
collect_text "edsys-voice-docker-baseline" "jeremy" "192.168.50.12" "hostnamectl; docker ps --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}' 2>/dev/null || true; findmnt"
collect_text "edsys-ingress-docker-baseline" "jeremy" "192.168.50.4" "hostnamectl; docker ps --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}' 2>/dev/null || true; findmnt"

log "Remote collection complete: ${OUT_DIR}"
