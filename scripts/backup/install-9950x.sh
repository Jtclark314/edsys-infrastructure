#!/usr/bin/env bash
set -Eeuo pipefail

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run with sudo on 9950x." >&2
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_BACKUP_DIR=""
TEMP_SELECTION_FILE=""

cleanup() {
  if [[ -n "${TEMP_SELECTION_FILE}" ]]; then
    rm -f -- "${TEMP_SELECTION_FILE}"
  fi
}
trap cleanup EXIT

backup_selection_file() {
  local path="$1"

  if [[ -z "${CONFIG_BACKUP_DIR}" ]]; then
    CONFIG_BACKUP_DIR="/root/edsys-config-backups/backup-selection-$(date -u +%Y%m%dT%H%M%SZ)-$$"
    install -d -m 0700 "${CONFIG_BACKUP_DIR}"
  fi
  cp -a -- "${path}" "${CONFIG_BACKUP_DIR}/$(basename "${path}").before"
}

has_no_symlink_components() {
  local path="$1"
  local component
  local current=""
  local -a components=()

  [[ "${path}" == /* ]] || return 1
  IFS='/' read -r -a components <<< "${path#/}"
  for component in "${components[@]}"; do
    [[ -z "${component}" || "${component}" == "." ]] && continue
    [[ "${component}" != ".." ]] || return 1
    current="${current}/${component}"
    [[ ! -L "${current}" ]] || return 1
  done
}

require_safe_selection_file() {
  local path="$1"
  local parent

  parent="$(dirname "${path}")"
  if [[ ! -d "${parent}" || -L "${parent}" || ! -f "${path}" || -L "${path}" ]] ||
    ! has_no_symlink_components "${path}"; then
    echo "Refusing to migrate non-regular or symlinked selection file: ${path}" >&2
    exit 2
  fi
}

ensure_exact_selection_line() {
  local path="$1"
  local required_line="$2"
  local explanation="$3"
  local temp

  require_safe_selection_file "${path}"
  if grep -Fqx -- "${required_line}" "${path}"; then
    return
  fi

  backup_selection_file "${path}"
  temp="$(mktemp --tmpdir="$(dirname "${path}")" ".$(basename "${path}").tmp.XXXXXX")"
  TEMP_SELECTION_FILE="${temp}"
  cp -a -- "${path}" "${temp}"
  if [[ -s "${temp}" && "$(tail -c 1 -- "${temp}" | wc -l)" -eq 0 ]]; then
    printf '\n' >> "${temp}"
  fi
  printf '\n# %s\n%s\n' "${explanation}" "${required_line}" >> "${temp}"
  grep -Fqx -- "${required_line}" "${temp}" || {
    echo "Could not safely migrate selection file: ${path}" >&2
    exit 2
  }
  mv -fT -- "${temp}" "${path}"
  TEMP_SELECTION_FILE=""
}

apt-get update
apt-get install -y rclone restic jq

install -d -m 0750 -o root -g root /etc/edsys-backup
install -d -m 0755 -o root -g root /srv/edsys-backup/scripts
install -d -m 0750 -o root -g root /srv/edsys-backup/staging
install -d -m 0700 -o root -g root /srv/edsys-backup/staging/codex-state
install -d -m 0755 -o root -g root /srv/edsys-backup/reports
install -d -m 0700 -o root -g root /srv/edsys-backup/reports/codex-state
install -d -m 0750 -o root -g root /srv/edsys-backup/restore-tests
install -d -m 0700 -o root -g root /srv/edsys-backup/restore-tests/codex-state
install -d -m 0750 -o root -g root /srv/edsys-backup/restic-repo
install -d -m 0755 -o root -g root /var/lib/edsys-backup
install -d -m 0750 -o root -g root /var/cache/edsys-backup/restic

install -m 0755 "${SCRIPT_DIR}/edsys-init-restic.sh" /srv/edsys-backup/scripts/edsys-init-restic.sh
install -m 0755 "${SCRIPT_DIR}/edsys-backup.sh" /srv/edsys-backup/scripts/edsys-backup.sh
install -m 0755 "${SCRIPT_DIR}/edsys-offsite-sync.sh" /srv/edsys-backup/scripts/edsys-offsite-sync.sh
install -m 0755 "${SCRIPT_DIR}/edsys-backup-status.sh" /srv/edsys-backup/scripts/edsys-backup-status.sh
install -m 0755 "${SCRIPT_DIR}/edsys-backup-check.sh" /srv/edsys-backup/scripts/edsys-backup-check.sh
install -m 0755 "${SCRIPT_DIR}/edsys-collect-remotes.sh" /srv/edsys-backup/scripts/edsys-collect-remotes.sh
install -m 0755 "${SCRIPT_DIR}/edsys-restic-check.sh" /srv/edsys-backup/scripts/edsys-restic-check.sh
install -m 0755 "${SCRIPT_DIR}/edsys-restore-test.sh" /srv/edsys-backup/scripts/edsys-restore-test.sh
install -m 0755 "${SCRIPT_DIR}/edsys-codex-state-stage.py" /srv/edsys-backup/scripts/edsys-codex-state-stage.py
install -m 0755 "${SCRIPT_DIR}/edsys-codex-state-restore-test.sh" /srv/edsys-backup/scripts/edsys-codex-state-restore-test.sh

if [[ ! -f /etc/edsys-backup/edsys-backup.conf ]]; then
  install -m 0600 "${SCRIPT_DIR}/edsys-backup.conf.example" /etc/edsys-backup/edsys-backup.conf
fi

if [[ ! -f /etc/edsys-backup/includes.txt ]]; then
  install -m 0644 "${SCRIPT_DIR}/includes.9950x.example" /etc/edsys-backup/includes.txt
fi

if [[ ! -f /etc/edsys-backup/excludes.txt ]]; then
  install -m 0644 "${SCRIPT_DIR}/excludes.example" /etc/edsys-backup/excludes.txt
fi

# These are required even on an upgrade from an older, locally customized
# selection set. Exact-line checks make the migration repeatable. Any edit is
# backed up privately before an atomic replacement; unsafe files hard-fail.
require_safe_selection_file /etc/edsys-backup/includes.txt
require_safe_selection_file /etc/edsys-backup/excludes.txt
ensure_exact_selection_line \
  /etc/edsys-backup/includes.txt \
  /srv/edsys-backup/staging \
  "SQLite-consistent backup staging is part of the critical backup set."
ensure_exact_selection_line \
  /etc/edsys-backup/excludes.txt \
  '/home/jeremy/.codex/*.sqlite*' \
  "Live Codex SQLite files are replaced by the consistent staging copy."

if [[ -n "${CONFIG_BACKUP_DIR}" ]]; then
  echo "Previous backup selections saved under ${CONFIG_BACKUP_DIR}."
fi

if [[ ! -f /etc/edsys-backup/restic-password ]]; then
  umask 077
  openssl rand -base64 48 > /etc/edsys-backup/restic-password
fi

if [[ ! -f /etc/edsys-backup/rclone.conf ]]; then
  install -m 0600 /dev/null /etc/edsys-backup/rclone.conf
fi

if [[ ! -f /etc/edsys-backup/known_hosts ]]; then
  install -m 0644 /dev/null /etc/edsys-backup/known_hosts
fi

install -m 0644 "${SCRIPT_DIR}/systemd/edsys-backup.service" /etc/systemd/system/edsys-backup.service
install -m 0644 "${SCRIPT_DIR}/systemd/edsys-backup.timer" /etc/systemd/system/edsys-backup.timer
install -m 0644 "${SCRIPT_DIR}/systemd/edsys-offsite-sync.service" /etc/systemd/system/edsys-offsite-sync.service
install -m 0644 "${SCRIPT_DIR}/systemd/edsys-offsite-sync.timer" /etc/systemd/system/edsys-offsite-sync.timer
install -m 0644 "${SCRIPT_DIR}/systemd/edsys-backup-check.service" /etc/systemd/system/edsys-backup-check.service
install -m 0644 "${SCRIPT_DIR}/systemd/edsys-backup-check.timer" /etc/systemd/system/edsys-backup-check.timer
install -m 0644 "${SCRIPT_DIR}/systemd/edsys-restore-test.service" /etc/systemd/system/edsys-restore-test.service
install -m 0644 "${SCRIPT_DIR}/systemd/edsys-restore-test.timer" /etc/systemd/system/edsys-restore-test.timer
install -m 0644 "${SCRIPT_DIR}/systemd/edsys-codex-state-stage.service" /etc/systemd/system/edsys-codex-state-stage.service
install -m 0644 "${SCRIPT_DIR}/systemd/edsys-codex-state-stage.timer" /etc/systemd/system/edsys-codex-state-stage.timer
install -m 0644 "${SCRIPT_DIR}/systemd/edsys-codex-state-restore-test.service" /etc/systemd/system/edsys-codex-state-restore-test.service
install -m 0644 "${SCRIPT_DIR}/systemd/edsys-codex-state-restore-test.timer" /etc/systemd/system/edsys-codex-state-restore-test.timer
systemctl daemon-reload

echo "Installed EdSys backup framework."
echo "Next: configure a custom Google OAuth client, then run sudo rclone --config /etc/edsys-backup/rclone.conf config"
echo "Use remote name: edsys-gdrive"
echo "Do not enable offsite timer until edsys-offsite-sync.sh --test-only succeeds."
