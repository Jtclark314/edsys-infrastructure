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
: "${RESTIC_PASSWORD_FILE:=/etc/edsys-backup/restic-password}"
: "${RESTIC_CACHE_DIR:=/var/cache/edsys-backup/restic}"
: "${REPORT_DIR:=/srv/edsys-backup/reports}"

export RCLONE_CONFIG RESTIC_REPOSITORY RESTIC_PASSWORD_FILE RESTIC_CACHE_DIR

mkdir -p "${REPORT_DIR}"
RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)"
LOG_FILE="${REPORT_DIR}/restic-check-${RUN_ID}.log"

restic check --read-data-subset=5% 2>&1 | tee "${LOG_FILE}"
echo "Restic check report: ${LOG_FILE}"
