#!/usr/bin/env bash
set -Eeuo pipefail

CONFIG_FILE="${EDSYS_BACKUP_CONFIG:-/etc/edsys-backup/edsys-backup.conf}"
if [[ -f "${CONFIG_FILE}" ]]; then
  # shellcheck disable=SC1090
  source "${CONFIG_FILE}"
fi

: "${BACKUP_ROOT:=/srv/edsys-backup}"
: "${STAGING_DIR:=${BACKUP_ROOT}/staging}"
: "${BACKUP_SSH_KEY:=/home/jeremy/.ssh/id_ed25519}"
: "${BACKUP_KNOWN_HOSTS:=/etc/edsys-backup/known_hosts}"

RUN_ID="${1:-$(date -u +%Y%m%dT%H%M%SZ)}"
OUT_DIR="${STAGING_DIR}/remote-exports/${RUN_ID}"
mkdir -p "${OUT_DIR}"
touch "${BACKUP_KNOWN_HOSTS}"

MANIFEST="${OUT_DIR}/MANIFEST.txt"
: > "${MANIFEST}"

log() {
  echo "[$(date -Is)] $*" | tee -a "${MANIFEST}"
}

ssh_args() {
  local args=(
    -o BatchMode=yes
    -o ConnectTimeout=8
    -o StrictHostKeyChecking=accept-new
    -o UserKnownHostsFile="${BACKUP_KNOWN_HOSTS}"
  )
  if [[ -r "${BACKUP_SSH_KEY}" ]]; then
    args+=(-i "${BACKUP_SSH_KEY}" -o IdentitiesOnly=yes)
  fi
  printf '%s\0' "${args[@]}"
}

run_ssh() {
  local user="$1"
  local host="$2"
  shift 2
  local args=()
  while IFS= read -r -d '' arg; do
    args+=("${arg}")
  done < <(ssh_args)
  ssh "${args[@]}" "${user}@${host}" "$@"
}

collect_local_text() {
  local label="$1"
  local output="${OUT_DIR}/${label}.txt"

  log "Collecting local text ${label}"
  {
    hostnamectl
    ip -br addr
    df -h
    docker ps --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}' 2>/dev/null || true
  } > "${output}" 2>&1 \
    && log "OK ${label} ${output}" \
    || { log "FAILED ${label}; see ${output}"; }
}

collect_tar_excluding() {
  local label="$1"
  local user="$2"
  local host="$3"
  shift 3
  local excludes=()
  while [[ "$#" -gt 0 && "$1" != "--" ]]; do
    excludes+=("$1")
    shift
  done
  if [[ "$#" -gt 0 && "$1" == "--" ]]; then
    shift
  fi
  local paths=("$@")
  local output="${OUT_DIR}/${label}.tar.gz"
  local tar_args=(-czf -)
  local exclude
  for exclude in "${excludes[@]}"; do
    tar_args+=("--exclude=${exclude}")
  done
  tar_args+=("${paths[@]}")
  local tar_arg_string
  tar_arg_string="$(printf ' %q' "${tar_args[@]}")"
  local exclude_note="none"
  if [[ "${#excludes[@]}" -gt 0 ]]; then
    exclude_note="${excludes[*]}"
  fi

  log "Collecting ${label} from ${user}@${host}: ${paths[*]} (excluding: ${exclude_note})"
  if [[ "${user}" == "root" ]]; then
    run_ssh "${user}" "${host}" "tar${tar_arg_string} 2>/dev/null" > "${output}" \
      && log "OK ${label} ${output}" \
      || { rm -f "${output}"; log "FAILED ${label}"; }
  else
    run_ssh "${user}" "${host}" "sudo -n tar${tar_arg_string} 2>/dev/null" > "${output}" \
      && log "OK ${label} ${output}" \
      || { rm -f "${output}"; log "FAILED ${label}"; }
  fi
}

collect_tar() {
  local label="$1"
  local user="$2"
  local host="$3"
  shift 3
  collect_tar_excluding "${label}" "${user}" "${host}" -- "$@"
}

collect_text() {
  local label="$1"
  local user="$2"
  local host="$3"
  local command="$4"
  local output="${OUT_DIR}/${label}.txt"

  log "Collecting text ${label} from ${user}@${host}"
  run_ssh "${user}" "${host}" "${command}" > "${output}" 2>&1 \
    && log "OK ${label} ${output}" \
    || { log "FAILED ${label}; see ${output}"; }
}

collect_local_text "9950x-local-baseline"

PIHOLE_VOLATILE_EXCLUDES=(
  "/etc/pihole/pihole-FTL.db*"
  "etc/pihole/pihole-FTL.db*"
)

collect_tar "edcore-dnsmasq" "jeremy" "192.168.50.1" "/etc/dnsmasq.d" "/var/lib/misc/dnsmasq.leases" "/etc/hostname" "/etc/hosts"
collect_tar_excluding "pihole-primary-config" "jeremy" "192.168.50.5" "${PIHOLE_VOLATILE_EXCLUDES[@]}" -- "/etc/pihole" "/etc/dnsmasq.d" "/etc/hostname" "/etc/hosts"
collect_tar_excluding "pihole-secondary-config" "jeremy" "192.168.50.6" "${PIHOLE_VOLATILE_EXCLUDES[@]}" -- "/etc/pihole" "/etc/dnsmasq.d" "/etc/hostname" "/etc/hosts"

collect_tar "pve-node0-config" "root" "192.168.50.51" "/etc/pve" "/etc/network/interfaces" "/etc/hosts" "/etc/hostname"
collect_tar "pve-node1-config" "root" "192.168.50.52" "/etc/pve" "/etc/network/interfaces" "/etc/hosts" "/etc/hostname"
collect_tar "pve-node2-config" "root" "192.168.50.53" "/etc/pve" "/etc/network/interfaces" "/etc/hosts" "/etc/hostname"

collect_text "arr-server-docker-baseline" "jeremy" "192.168.50.201" "hostnamectl; docker ps --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}' 2>/dev/null || true; findmnt"
collect_text "family-services-docker-baseline" "jeremy" "192.168.50.78" "hostnamectl; docker ps --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}' 2>/dev/null || true; findmnt"
collect_text "edsys-voice-docker-baseline" "jeremy" "192.168.50.12" "hostnamectl; docker ps --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}' 2>/dev/null || true; findmnt"
collect_text "edsys-ingress-docker-baseline" "jeremy" "192.168.50.4" "hostnamectl; docker ps --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}' 2>/dev/null || true; findmnt"

log "Remote collection complete: ${OUT_DIR}"
