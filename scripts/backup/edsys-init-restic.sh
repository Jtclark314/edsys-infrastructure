#!/usr/bin/env bash
set -Eeuo pipefail

CONFIG_FILE="${EDSYS_BACKUP_CONFIG:-/etc/edsys-backup/edsys-backup.conf}"
if [[ -f "${CONFIG_FILE}" ]]; then
  # shellcheck disable=SC1090
  source "${CONFIG_FILE}"
fi

: "${RCLONE_CONFIG:=/etc/edsys-backup/rclone.conf}"
: "${RCLONE_REMOTE:=edsys-gdrive}"
: "${DRIVE_BACKUP_ROOT:=EdSys Backups}"
: "${RESTIC_REPOSITORY:=/srv/edsys-backup/restic-repo/edsys-critical}"
: "${RCLONE_OFFSITE_DEST:=${RCLONE_REMOTE}:${DRIVE_BACKUP_ROOT}/restic/edsys-critical-v3}"
: "${RESTIC_PASSWORD_FILE:=/etc/edsys-backup/restic-password}"
: "${RESTIC_CACHE_DIR:=/var/cache/edsys-backup/restic}"
: "${RESTIC_LOCK_RETRY:=5m}"

export RCLONE_CONFIG RESTIC_REPOSITORY RESTIC_PASSWORD_FILE RESTIC_CACHE_DIR
mkdir -p "${RESTIC_REPOSITORY}" "${RESTIC_CACHE_DIR}"

if ! command -v restic >/dev/null; then
  echo "restic is not installed." >&2
  exit 1
fi

if command -v rclone >/dev/null && rclone listremotes --config "${RCLONE_CONFIG}" | grep -qx "${RCLONE_REMOTE}:"; then
  rclone mkdir --config "${RCLONE_CONFIG}" "${RCLONE_REMOTE}:${DRIVE_BACKUP_ROOT}/reports" || true
  rclone mkdir --config "${RCLONE_CONFIG}" "${RCLONE_REMOTE}:${DRIVE_BACKUP_ROOT}/manual-exports" || true
  rclone mkdir --config "${RCLONE_CONFIG}" "${RCLONE_REMOTE}:${DRIVE_BACKUP_ROOT}/restore-tests" || true
else
  echo "rclone remote '${RCLONE_REMOTE}:' is not configured; skipping Google Drive folder setup."
fi

if [[ -e "${RESTIC_REPOSITORY}/config" ]]; then
  restic unlock
  restic --retry-lock "${RESTIC_LOCK_RETRY}" snapshots >/dev/null
  echo "Restic repository already initialized: ${RESTIC_REPOSITORY}"
else
  restic init
  echo "Initialized restic repository: ${RESTIC_REPOSITORY}"
fi
