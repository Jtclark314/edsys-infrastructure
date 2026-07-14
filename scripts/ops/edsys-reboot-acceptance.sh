#!/usr/bin/env bash
set -Eeuo pipefail

STATE_ROOT="${EDSYS_REBOOT_ACCEPTANCE_STATE_ROOT:-/var/lib/edsys-reboot-acceptance}"
PENDING_FILE="${STATE_ROOT}/pending"
CURRENT_FILE="${STATE_ROOT}/current.json"
TIMEOUT_SECONDS="${EDSYS_REBOOT_ACCEPTANCE_TIMEOUT_SECONDS:-900}"
RETRY_SECONDS="${EDSYS_REBOOT_ACCEPTANCE_RETRY_SECONDS:-15}"

CORE_SERVICES=(
  containerd.service
  docker.service
  ssh.service
  tailscaled.service
  smbd.service
  nmbd.service
  netdata.service
  edsys-share-tailnet-guard.service
)

CORE_ENABLED_SERVICES=(
  containerd.service
  docker.service
  tailscaled.service
  smbd.service
  nmbd.service
  netdata.service
  edsys-share-tailnet-guard.service
)

CRITICAL_TIMERS=(
  edsys-backup.timer
  edsys-backup-check.timer
  edsys-offsite-sync.timer
  edsys-container-recovery-audit.timer
  edsys-git-sync.timer
  edsys-litellm-broker-smoke.timer
  edsys-morning-brief.timer
  edsys-weekly-codex-maintenance.timer
  edsys-codex-state-stage.timer
  edsys-codex-state-restore-test.timer
  edsys-share-gdrive-sync.timer
  edsys-share-gdrive-verify.timer
  edsys-share-gdrive-verify-checksum.timer
  edsys-share-gdrive-prune.timer
)

AI_PROXY_PORTS=(3000 3002 6333 7997 8015 8020 8099 11434)
PACKAGES=(containerd.io netdata-user trivy vivaldi-stable)
SHARE_MOUNT_CHECK="${EDSYS_SHARE_MOUNT_CHECK:-/usr/local/libexec/edsys-share/edsys-share-mount-check}"
AI_PROXY_CHECK="${EDSYS_AI_PROXY_CHECK:-/usr/local/sbin/edsys-ai-tailnet-proxy-check}"
CONTAINER_RECOVERY="${EDSYS_CONTAINER_RECOVERY:-/usr/local/sbin/edsys-container-recovery}"

usage() {
  cat <<'EOF'
Usage:
  sudo edsys-reboot-acceptance arm --run RUN_ID
  sudo edsys-reboot-acceptance check
  sudo edsys-reboot-acceptance status

arm records a private pre-boot baseline and creates the one-shot pending marker.
check is normally invoked by systemd after the next boot. It refuses the same
boot, waits for ordered recovery, and records a private atomic result.
EOF
}

die() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

require_root() {
  [[ ${EUID} -eq 0 ]] || die "run as root"
}

require_commands() {
  local command_name
  for command_name in "$@"; do
    command -v "${command_name}" >/dev/null || die "missing command: ${command_name}"
  done
}

validate_run_id() {
  [[ ${1:-} =~ ^[0-9]{8}T[0-9]{6}Z$ ]] || die "run ID must use UTC YYYYMMDDTHHMMSSZ"
}

atomic_write() {
  local destination="$1"
  local mode="$2"
  local temporary
  temporary="$(mktemp "${destination}.tmp.XXXXXX")"
  cat >"${temporary}"
  chmod "${mode}" "${temporary}"
  mv -f "${temporary}" "${destination}"
}

capture_failed_units() {
  systemctl --failed --no-legend --plain 2>/dev/null \
    | awk '$1 ~ /\.service$/ {print $1}' \
    | LC_ALL=C sort -u
}

capture_docker_identities() {
  local -a ids=()
  mapfile -t ids < <(docker ps -aq)
  ((${#ids[@]} > 0)) || return 0
  # Container identity, not start time, is the stable reboot boundary.
  docker inspect "${ids[@]}" --format '{{.Name}}|{{.Id}}' \
    | sed 's#^/##' \
    | LC_ALL=C sort
}

capture_package_versions() {
  local package_name
  for package_name in "${PACKAGES[@]}"; do
    dpkg-query -W -f='${Package}|${Version}|${Status}\n' "${package_name}"
  done
}

write_json_status() {
  local run_id="$1"
  local result="$2"
  local detail="$3"
  local boot_id="$4"
  python3 - "${run_id}" "${result}" "${detail}" "${boot_id}" "${CURRENT_FILE}" <<'PY'
import json
import os
from pathlib import Path
import sys
from datetime import datetime, timezone

run_id, result, detail, boot_id, destination = sys.argv[1:]
path = Path(destination)
payload = {
    "schema": 1,
    "run_id": run_id,
    "result": result,
    "detail": detail,
    "boot_id": boot_id,
    "completed_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
}
temporary = path.with_name(path.name + ".tmp")
temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
os.chmod(temporary, 0o600)
os.replace(temporary, path)
PY
}

arm() {
  require_root
  require_commands docker dpkg-query systemctl python3

  local run_id=""
  while (($#)); do
    case "$1" in
      --run)
        (($# >= 2)) || die "--run requires a value"
        run_id="$2"
        shift 2
        ;;
      *) die "unknown arm argument: $1" ;;
    esac
  done
  validate_run_id "${run_id}"

  install -d -m 0700 -o root -g root "${STATE_ROOT}/runs"
  [[ ! -e ${PENDING_FILE} ]] || die "a reboot acceptance run is already pending"

  local run_dir="${STATE_ROOT}/runs/${run_id}"
  [[ ! -e ${run_dir} ]] || die "run already exists: ${run_id}"
  install -d -m 0700 -o root -g root "${run_dir}"

  cat /proc/sys/kernel/random/boot_id >"${run_dir}/pre-boot-id"
  date -u +%FT%TZ >"${run_dir}/armed-at"
  capture_failed_units >"${run_dir}/failed-units.before"
  capture_docker_identities >"${run_dir}/docker-identities.before"
  capture_package_versions >"${run_dir}/packages.before"
  /home/jeremy/.local/bin/codex --version >"${run_dir}/codex.before"
  chmod 0600 "${run_dir}"/*

  printf '%s\n' "${run_id}" | atomic_write "${PENDING_FILE}" 0600
  write_json_status "${run_id}" "armed" "awaiting a different boot ID" \
    "$(cat "${run_dir}/pre-boot-id")"
  printf 'ARMED run=%s pre_boot_id=%s\n' "${run_id}" "$(cat "${run_dir}/pre-boot-id")"
}

readiness_probe() {
  local unit port
  mountpoint -q /mnt/data-500g || return 1
  mountpoint -q /mnt/ai-store || return 1
  mountpoint -q /EdSys-Share || return 1
  for unit in "${CORE_SERVICES[@]}"; do
    systemctl is-active --quiet "${unit}" || return 1
  done
  systemctl is-active --quiet edsys-container-recovery.service || \
    [[ $(systemctl show edsys-container-recovery.service -p Result --value) == success ]] || return 1
  docker info >/dev/null 2>&1 || return 1
  for port in "${AI_PROXY_PORTS[@]}"; do
    systemctl is-active --quiet "edsys-ai-tailnet-proxy@${port}.socket" || return 1
  done
}

full_acceptance() {
  local run_dir="$1"
  local unit port

  [[ -x ${SHARE_MOUNT_CHECK} ]] || {
    echo "missing Share mount validator: ${SHARE_MOUNT_CHECK}" >&2
    return 1
  }
  [[ -x ${AI_PROXY_CHECK} ]] || {
    echo "missing AI proxy validator: ${AI_PROXY_CHECK}" >&2
    return 1
  }
  [[ -x ${CONTAINER_RECOVERY} ]] || {
    echo "missing container recovery validator: ${CONTAINER_RECOVERY}" >&2
    return 1
  }
  "${SHARE_MOUNT_CHECK}"
  "${AI_PROXY_CHECK}"
  "${CONTAINER_RECOVERY}" audit
  [[ $(docker info --format '{{.LiveRestoreEnabled}}') == false ]] || {
    echo "Docker live restore must remain disabled for deterministic host shutdown" >&2
    return 1
  }

  for unit in "${CORE_ENABLED_SERVICES[@]}"; do
    systemctl is-enabled --quiet "${unit}"
    systemctl is-active --quiet "${unit}"
  done
  # Ubuntu 24.04 can use systemd socket activation for OpenSSH.  In that
  # supported posture ssh.service is active but intentionally not enabled;
  # ssh.socket is the enabled boot unit.
  systemctl is-enabled --quiet ssh.socket
  systemctl is-active --quiet ssh.socket
  systemctl is-active --quiet ssh.service
  systemctl is-enabled --quiet edsys-share-tailnet-smb.socket
  systemctl is-active --quiet edsys-share-tailnet-smb.socket

  for port in "${AI_PROXY_PORTS[@]}"; do
    systemctl is-enabled --quiet "edsys-ai-tailnet-proxy@${port}.socket"
    systemctl is-active --quiet "edsys-ai-tailnet-proxy@${port}.socket"
  done

  for unit in "${CRITICAL_TIMERS[@]}"; do
    systemctl is-enabled --quiet "${unit}"
    systemctl is-active --quiet "${unit}"
  done

  local unhealthy
  unhealthy="$(docker ps --filter health=unhealthy --format '{{.Names}}')"
  [[ -z ${unhealthy} ]] || {
    echo "unhealthy containers: ${unhealthy//$'\n'/,}" >&2
    return 1
  }

  capture_docker_identities >"${run_dir}/docker-identities.after"
  cmp -s "${run_dir}/docker-identities.before" "${run_dir}/docker-identities.after" || {
    echo "Docker container identity set changed across reboot" >&2
    return 1
  }

  capture_package_versions >"${run_dir}/packages.after"
  cmp -s "${run_dir}/packages.before" "${run_dir}/packages.after" || {
    echo "accepted package versions changed across reboot" >&2
    return 1
  }

  /home/jeremy/.local/bin/codex --version >"${run_dir}/codex.after"
  cmp -s "${run_dir}/codex.before" "${run_dir}/codex.after" || {
    echo "Codex version changed before reboot acceptance" >&2
    return 1
  }

  capture_failed_units >"${run_dir}/failed-units.after"
  comm -13 "${run_dir}/failed-units.before" "${run_dir}/failed-units.after" \
    >"${run_dir}/failed-units.new"
  [[ ! -s ${run_dir}/failed-units.new ]] || {
    echo "new failed systemd units appeared after reboot" >&2
    return 1
  }

  tailscale ip -4 | grep -Fxq '100.87.137.47'
  nvidia-smi --query-gpu=name,driver_version --format=csv,noheader >/dev/null
  findmnt -rn -M /mnt/data-500g >/dev/null
  findmnt -rn -M /mnt/ai-store >/dev/null
  findmnt -rn -M /EdSys-Share >/dev/null
  [[ ! -e /var/run/reboot-required ]]
  chmod 0600 "${run_dir}"/*
}

check() {
  require_root
  require_commands cmp comm docker dpkg-query findmnt grep mountpoint nvidia-smi \
    python3 systemctl tailscale
  [[ -r ${PENDING_FILE} ]] || die "no reboot acceptance run is pending"

  local run_id run_dir previous_boot current_boot deadline
  run_id="$(<"${PENDING_FILE}")"
  validate_run_id "${run_id}"
  run_dir="${STATE_ROOT}/runs/${run_id}"
  [[ -d ${run_dir} ]] || die "missing private run directory: ${run_dir}"
  previous_boot="$(<"${run_dir}/pre-boot-id")"
  current_boot="$(</proc/sys/kernel/random/boot_id)"
  [[ ${current_boot} != "${previous_boot}" ]] || die "refusing acceptance on the pre-reboot boot ID"

  exec > >(tee -a "${run_dir}/acceptance.log") 2>&1
  printf 'START run=%s boot_id=%s at=%s\n' "${run_id}" "${current_boot}" "$(date -u +%FT%TZ)"

  deadline=$((SECONDS + TIMEOUT_SECONDS))
  until readiness_probe; do
    if ((SECONDS >= deadline)); then
      write_json_status "${run_id}" "failed" "recovery readiness timed out" "${current_boot}"
      printf 'FAIL recovery readiness timed out\n'
      exit 1
    fi
    sleep "${RETRY_SECONDS}"
  done

  on_acceptance_error() {
    local exit_code=$?
    write_json_status "${run_id}" "failed" "one or more host-local acceptance checks failed" "${current_boot}"
    printf 'FAIL run=%s boot_id=%s\n' "${run_id}" "${current_boot}"
    exit "${exit_code}"
  }
  trap on_acceptance_error ERR
  full_acceptance "${run_dir}"
  trap - ERR

  write_json_status "${run_id}" "passed" "host-local reboot acceptance passed" "${current_boot}"
  rm -f "${PENDING_FILE}"
  printf 'PASS run=%s boot_id=%s\n' "${run_id}" "${current_boot}"
}

status() {
  require_root
  if [[ -r ${CURRENT_FILE} ]]; then
    cat "${CURRENT_FILE}"
  else
    echo '{"result":"never-run"}'
  fi
  if [[ -r ${PENDING_FILE} ]]; then
    printf 'pending_run=%s\n' "$(<"${PENDING_FILE}")"
  fi
}

action="${1:-}"
shift || true
case "${action}" in
  arm) arm "$@" ;;
  check) check "$@" ;;
  status) status "$@" ;;
  -h|--help|help) usage ;;
  *) usage >&2; exit 2 ;;
esac
