#!/usr/bin/env bash
set -Eeuo pipefail

CONFIG_FILE="${EDSYS_BACKUP_CONFIG:-/etc/edsys-backup/edsys-backup.conf}"
# shellcheck disable=SC1090
source "${CONFIG_FILE}"

: "${RCLONE_BIN:=/opt/edsys-tools/rclone/current/rclone}"
: "${RCLONE_CONFIG:=/etc/edsys-backup/rclone.conf}"
: "${RCLONE_REMOTE:=edsys-gdrive}"
: "${RCLONE_OFFSITE_DEST:=${RCLONE_REMOTE}:EdSys Backups/restic/edsys-critical-v3}"
: "${RESTIC_REPOSITORY:=/srv/edsys-backup/restic-repo/edsys-critical}"
: "${RESTIC_PASSWORD_FILE:=/etc/edsys-backup/restic-password}"
: "${STATUS_DIR:=/var/lib/edsys-backup}"

AUTH_ONLY=false
if [[ "${1:-}" == "--auth-only" ]]; then
  AUTH_ONLY=true
elif [[ $# -gt 0 ]]; then
  echo "Unknown argument: $1" >&2
  exit 2
fi

mkdir -p "${STATUS_DIR}"
STATUS_FILE="${STATUS_DIR}/gdrive-auth-status.json"
RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)"

fail() {
  local message="$1"
  jq -n \
    --arg status failed \
    --arg run_id "${RUN_ID}" \
    --arg timestamp "$(date -Is)" \
    --arg message "${message}" \
    '{status:$status,run_id:$run_id,timestamp:$timestamp,message:$message}' \
    >"${STATUS_FILE}"
  echo "${message}" >&2
  exit 1
}

"${RCLONE_BIN}" about --config "${RCLONE_CONFIG}" "${RCLONE_REMOTE}:" >/dev/null \
  || fail "Google Drive OAuth connectivity check failed"

LATEST_LOCAL=""
if [[ "${AUTH_ONLY}" == "false" ]]; then
  LATEST_LOCAL="$(
    restic --no-lock --repo "${RESTIC_REPOSITORY}" \
      --password-file "${RESTIC_PASSWORD_FILE}" snapshots --json |
      jq -r 'if length > 0 then max_by(.time).id else empty end'
  )"
  [[ "${LATEST_LOCAL}" =~ ^[0-9a-f]{64}$ ]] || fail "No current local restic snapshot was found"
  REMOTE_SNAPSHOT="$(
    "${RCLONE_BIN}" lsf --config "${RCLONE_CONFIG}" \
      "${RCLONE_OFFSITE_DEST}/snapshots" --files-only |
      grep -Fxc "${LATEST_LOCAL}" || true
  )"
  [[ "${REMOTE_SNAPSHOT}" -eq 1 ]] \
    || fail "Latest local restic snapshot is not present in Google Drive"
fi

jq -n \
  --arg status success \
  --arg run_id "${RUN_ID}" \
  --arg timestamp "$(date -Is)" \
  --arg latest_local_snapshot "${LATEST_LOCAL}" \
  --argjson auth_only "${AUTH_ONLY}" \
  '{status:$status,run_id:$run_id,timestamp:$timestamp,latest_local_snapshot:$latest_local_snapshot,auth_only:$auth_only}' \
  >"${STATUS_FILE}"
echo "Google Drive authentication and requested parity checks passed."
