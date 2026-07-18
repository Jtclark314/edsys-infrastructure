#!/usr/bin/env bash
set -Eeuo pipefail

CONFIG_FILE="${EDSYS_BACKUP_CONFIG:-/etc/edsys-backup/edsys-backup.conf}"
if [[ -f "${CONFIG_FILE}" ]]; then
  # shellcheck disable=SC1090
  source "${CONFIG_FILE}"
fi

: "${BACKUP_ROOT:=/srv/edsys-backup}"
: "${REPORT_DIR:=${BACKUP_ROOT}/reports}"
: "${STATUS_DIR:=/var/lib/edsys-backup}"
: "${RCLONE_CONFIG:=/etc/edsys-backup/rclone.conf}"
: "${RCLONE_REMOTE:=edsys-gdrive}"
: "${DRIVE_BACKUP_ROOT:=EdSys Backups}"
: "${RCLONE_OFFSITE_DEST:=${RCLONE_REMOTE}:${DRIVE_BACKUP_ROOT}/restic/edsys-critical-v3}"
: "${RESTIC_REPOSITORY:=/srv/edsys-backup/restic-repo/edsys-critical}"
: "${RESTIC_PASSWORD_FILE:=/etc/edsys-backup/restic-password}"

export RCLONE_CONFIG

echo "# EdSys Backup Status"
echo
echo "Local repository: ${RESTIC_REPOSITORY}"
if [[ -d "${RESTIC_REPOSITORY}" ]]; then
  du -sh "${RESTIC_REPOSITORY}" 2>/dev/null || true
fi
echo

echo "## Latest Local Snapshot"
if [[ -r "${RESTIC_PASSWORD_FILE}" && -d "${RESTIC_REPOSITORY}" ]]; then
  # These are observational queries. Avoid creating a repository lock so an
  # interrupted status command cannot block the next scheduled backup.
  restic --no-lock --repo "${RESTIC_REPOSITORY}" --password-file "${RESTIC_PASSWORD_FILE}" snapshots --latest 3 || true
  restic --no-lock --repo "${RESTIC_REPOSITORY}" --password-file "${RESTIC_PASSWORD_FILE}" stats latest || true
else
  echo "Local restic repo or password file missing."
fi
echo

echo "## Recent Backup Logs"
ls -lt "${REPORT_DIR}"/backup-* 2>/dev/null | head -10 || true
echo

echo "## Recent Rclone Logs"
ls -lt "${REPORT_DIR}"/rclone-sync-* 2>/dev/null | head -10 || true
echo

echo "## Running Backup/Sync Processes"
ps -eo pid,ppid,etime,cmd | grep -E 'edsys-backup|edsys-offsite|rclone sync|restic backup|rclone serve restic' | grep -v grep || echo "None."
echo

echo "## Rclone Custom OAuth Check"
if [[ -r "${RCLONE_CONFIG}" ]] &&
   grep -qE '^[[:space:]]*client_id[[:space:]]*=[[:space:]]*[^[:space:]]+' "${RCLONE_CONFIG}" &&
   grep -qE '^[[:space:]]*client_secret[[:space:]]*=[[:space:]]*[^[:space:]]+' "${RCLONE_CONFIG}"; then
  echo "custom_client=yes"
else
  echo "custom_client=no"
  echo "WARNING: offsite sync is intentionally blocked until a dedicated Google OAuth client is configured."
fi
echo

echo "## Google Drive Backup Paths"
if command -v rclone >/dev/null && [[ -r "${RCLONE_CONFIG}" ]] && rclone listremotes --config "${RCLONE_CONFIG}" | grep -qx "${RCLONE_REMOTE}:"; then
  for path in \
    "${DRIVE_BACKUP_ROOT}/restic/edsys-critical" \
    "${DRIVE_BACKUP_ROOT}/restic/edsys-critical-v2" \
    "${DRIVE_BACKUP_ROOT}/restic/edsys-critical-v3"; do
    echo "${RCLONE_REMOTE}:${path}"
    rclone size --config "${RCLONE_CONFIG}" "${RCLONE_REMOTE}:${path}" --json 2>/dev/null || echo "  not found or inaccessible"
  done
else
  echo "rclone remote not configured."
fi
