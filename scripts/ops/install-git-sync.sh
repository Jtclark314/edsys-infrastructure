#!/usr/bin/env bash
set -euo pipefail

enable_timer=false
rotate_log=false

usage() {
  cat <<'EOF'
Usage: sudo install-git-sync.sh [--enable] [--rotate-log]

Installs the source-controlled EdSys Git sync script, systemd units, and
logrotate policy. Existing live files are backed up outside Git before they are
replaced. The timer is only enabled when --enable is supplied, and the current
log is only force-rotated when --rotate-log is supplied.
EOF
}

while (($#)); do
  case "$1" in
    --enable)
      enable_timer=true
      ;;
    --rotate-log)
      rotate_log=true
      ;;
    -h | --help)
      usage
      exit 0
      ;;
    *)
      printf 'Unknown argument: %s\n' "$1" >&2
      usage >&2
      exit 2
      ;;
  esac
  shift
done

if [[ ${EUID} -ne 0 ]]; then
  echo "Run as root (for example: sudo $0)." >&2
  exit 2
fi

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
source_script="${repo_root}/scripts/ops/sync-edsys-repos.sh"
source_service="${repo_root}/scripts/ops/systemd/edsys-git-sync.service"
source_timer="${repo_root}/scripts/ops/systemd/edsys-git-sync.timer"
source_logrotate="${repo_root}/scripts/ops/logrotate/edsys-git-sync"

live_script="/srv/edsys/sync-edsys-repos.sh"
live_service="/etc/systemd/system/edsys-git-sync.service"
live_timer="/etc/systemd/system/edsys-git-sync.timer"
live_logrotate="/etc/logrotate.d/edsys-git-sync"
live_log="/srv/edsys/sync-edsys-repos.log"

stamp="$(date -u +%Y%m%dT%H%M%SZ)"
backup_dir="/var/backups/edsys-git-sync/${stamp}"
unit_changed=false
anything_changed=false
validation_dir=""

cleanup() {
  if [[ -n "${validation_dir}" && -d "${validation_dir}" ]]; then
    rm -rf -- "${validation_dir}"
  fi
}
trap cleanup EXIT

install -d -m 0700 "${backup_dir}"

backup_existing() {
  local target="$1"
  local name="$2"
  if [[ -e "${target}" || -L "${target}" ]]; then
    cp -a -- "${target}" "${backup_dir}/${name}"
  fi
}

install_if_changed() {
  local source="$1"
  local target="$2"
  local mode="$3"
  local name="$4"
  local kind="${5:-file}"

  if [[ -f "${target}" ]] && cmp -s -- "${source}" "${target}" && \
    [[ "$(stat -c '%a' "${target}")" == "${mode}" ]] && \
    [[ "$(stat -c '%U:%G' "${target}")" == "root:root" ]]; then
    return 0
  fi

  backup_existing "${target}" "${name}"
  install -m "${mode}" -o root -g root "${source}" "${target}"
  anything_changed=true
  if [[ "${kind}" == "unit" ]]; then
    unit_changed=true
  fi
}

shellcheck "${source_script}" "${repo_root}/scripts/ops/install-git-sync.sh"
systemd-analyze verify "${source_service}" "${source_timer}"
validation_dir="$(mktemp -d)"
install -m 0644 -o root -g root "${source_logrotate}" \
  "${validation_dir}/edsys-git-sync"
if ! logrotate --debug --state "${validation_dir}/state" \
  "${validation_dir}/edsys-git-sync" >"${validation_dir}/logrotate-debug.txt" 2>&1; then
  cat "${validation_dir}/logrotate-debug.txt" >&2
  exit 1
fi

install_if_changed "${source_script}" "${live_script}" 755 sync-edsys-repos.sh
install_if_changed "${source_service}" "${live_service}" 644 edsys-git-sync.service unit
install_if_changed "${source_timer}" "${live_timer}" 644 edsys-git-sync.timer unit
install_if_changed "${source_logrotate}" "${live_logrotate}" 644 edsys-git-sync.logrotate

if [[ "${unit_changed}" == true ]]; then
  systemctl daemon-reload
fi

if [[ "${rotate_log}" == true && -s "${live_log}" ]]; then
  logrotate --force "${live_logrotate}"
fi

if [[ "${enable_timer}" == true ]]; then
  systemctl enable --now edsys-git-sync.timer
fi

if [[ "${anything_changed}" == true ]]; then
  printf 'Installed EdSys Git sync controls. Rollback files: %s\n' "${backup_dir}"
else
  rmdir "${backup_dir}"
  printf 'EdSys Git sync controls already match source; no files changed.\n'
fi

if [[ "${unit_changed}" == false ]]; then
  echo "systemd unit content was unchanged; daemon-reload was not needed."
fi
