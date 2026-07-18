#!/usr/bin/env bash
set -Eeuo pipefail

CONFIG_FILE="${EDSYS_BACKUP_CONFIG:-/etc/edsys-backup/edsys-backup.conf}"
if [[ ! -f "${CONFIG_FILE}" ]]; then
  echo "Missing config file: ${CONFIG_FILE}" >&2
  exit 2
fi
# shellcheck disable=SC1090
source "${CONFIG_FILE}"

: "${RCLONE_CONFIG:=/etc/edsys-backup/rclone.conf}"
: "${RCLONE_REMOTE:=edsys-gdrive}"
: "${DRIVE_BACKUP_ROOT:=EdSys Backups}"
: "${RCLONE_OFFSITE_DEST:=${RCLONE_REMOTE}:${DRIVE_BACKUP_ROOT}/restic/edsys-critical-v3}"
: "${RESTIC_REPOSITORY:=/srv/edsys-backup/restic-repo/edsys-critical}"
: "${RESTIC_PASSWORD_FILE:=/etc/edsys-backup/restic-password}"
: "${RESTIC_CACHE_DIR:=/var/cache/edsys-backup/restic}"
: "${RESTIC_LOCK_RETRY:=5m}"
: "${REPORT_DIR:=/srv/edsys-backup/reports}"

READ_DATA_SUBSET=""
CHECK_REMOTE=false
for arg in "$@"; do
  case "${arg}" in
    --read-data-subset=*) READ_DATA_SUBSET="${arg#--read-data-subset=}" ;;
    --remote-structure) CHECK_REMOTE=true ;;
    *) echo "Unknown argument: ${arg}" >&2; exit 2 ;;
  esac
done

export RESTIC_REPOSITORY RESTIC_PASSWORD_FILE RESTIC_CACHE_DIR RCLONE_CONFIG

mkdir -p "${REPORT_DIR}"
RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)"
LOG_FILE="${REPORT_DIR}/restic-check-${RUN_ID}.log"

RESTIC_ARGS=(check)
if [[ -n "${READ_DATA_SUBSET}" ]]; then
  RESTIC_ARGS+=("--read-data-subset=${READ_DATA_SUBSET}")
fi

restic --retry-lock "${RESTIC_LOCK_RETRY}" "${RESTIC_ARGS[@]}" 2>&1 | tee "${LOG_FILE}"

if [[ "${CHECK_REMOTE}" == "true" ]]; then
  echo "Remote structure for ${RCLONE_OFFSITE_DEST}:"
  rclone lsf --config "${RCLONE_CONFIG}" "${RCLONE_OFFSITE_DEST}" | grep -E '^(config|data/|index/|keys/|snapshots/)' || {
    echo "Remote mirror does not expose expected restic top-level structure." >&2
    exit 1
  }
fi

echo "Restic check report: ${LOG_FILE}"
