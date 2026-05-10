#!/usr/bin/env bash
set -Eeuo pipefail

CONFIG_FILE="${EDSYS_BACKUP_ENV:-/etc/edsys-backup/backup.env}"
if [[ -f "${CONFIG_FILE}" ]]; then
  # shellcheck disable=SC1090
  source "${CONFIG_FILE}"
fi

: "${RCLONE_CONFIG:=/etc/edsys-backup/rclone.conf}"
: "${RCLONE_REMOTE:=edsys-gdrive}"
: "${DRIVE_BACKUP_ROOT:=EdSys Backups}"
: "${RESTIC_REPOSITORY:=/srv/edsys-backup/restic-repo/edsys-critical}"
: "${RCLONE_OFFSITE_DEST:=${RCLONE_REMOTE}:${DRIVE_BACKUP_ROOT}/restic/edsys-critical-v2}"
: "${RESTIC_PASSWORD_FILE:=/etc/edsys-backup/restic-password}"
: "${RESTIC_CACHE_DIR:=/var/cache/edsys-backup/restic}"

export RCLONE_CONFIG RESTIC_REPOSITORY RESTIC_PASSWORD_FILE RESTIC_CACHE_DIR
mkdir -p "${RESTIC_REPOSITORY}" "${RESTIC_CACHE_DIR}"

if ! command -v rclone >/dev/null; then
  echo "rclone is not installed." >&2
  exit 1
fi

if ! command -v restic >/dev/null; then
  echo "restic is not installed." >&2
  exit 1
fi

if ! rclone listremotes --config "${RCLONE_CONFIG}" | grep -qx "${RCLONE_REMOTE}:"; then
  echo "Missing rclone remote '${RCLONE_REMOTE}:' in ${RCLONE_CONFIG}." >&2
  echo "Run: sudo rclone --config ${RCLONE_CONFIG} config" >&2
  exit 2
fi

rclone mkdir --config "${RCLONE_CONFIG}" "${RCLONE_OFFSITE_DEST}"
rclone mkdir --config "${RCLONE_CONFIG}" "${RCLONE_REMOTE}:${DRIVE_BACKUP_ROOT}/reports"
rclone mkdir --config "${RCLONE_CONFIG}" "${RCLONE_REMOTE}:${DRIVE_BACKUP_ROOT}/manual-exports"
rclone mkdir --config "${RCLONE_CONFIG}" "${RCLONE_REMOTE}:${DRIVE_BACKUP_ROOT}/restore-tests"

if restic snapshots >/dev/null 2>&1; then
  echo "Restic repository already initialized: ${RESTIC_REPOSITORY}"
else
  restic init
  echo "Initialized restic repository: ${RESTIC_REPOSITORY}"
fi
