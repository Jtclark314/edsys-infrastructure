#!/usr/bin/env bash
set -Eeuo pipefail

CONFIG_FILE="${EDSYS_BACKUP_ENV:-/etc/edsys-backup/backup.env}"
if [[ -f "${CONFIG_FILE}" ]]; then
  # shellcheck disable=SC1090
  source "${CONFIG_FILE}"
fi

: "${BACKUP_ROOT:=/srv/edsys-backup}"
: "${STAGING_DIR:=${BACKUP_ROOT}/staging}"
: "${REPORT_DIR:=${BACKUP_ROOT}/reports}"
: "${STATUS_DIR:=/var/lib/edsys-backup}"
: "${RCLONE_CONFIG:=/etc/edsys-backup/rclone.conf}"
: "${RCLONE_REMOTE:=edsys-gdrive}"
: "${DRIVE_BACKUP_ROOT:=EdSys Backups}"
: "${RESTIC_REPOSITORY:=/srv/edsys-backup/restic-repo/edsys-critical}"
: "${RESTIC_PASSWORD_FILE:=/etc/edsys-backup/restic-password}"
: "${RESTIC_CACHE_DIR:=/var/cache/edsys-backup/restic}"
: "${RCLONE_OFFSITE_DEST:=${RCLONE_REMOTE}:${DRIVE_BACKUP_ROOT}/restic/edsys-critical-v2}"
: "${INCLUDE_FILE:=/etc/edsys-backup/includes.txt}"
: "${EXCLUDE_FILE:=/etc/edsys-backup/excludes.txt}"
: "${KEEP_DAILY:=30}"
: "${KEEP_WEEKLY:=12}"
: "${KEEP_MONTHLY:=12}"
: "${REMOTE_COLLECTION_ENABLED:=true}"
: "${REPORT_TO_DRIVE:=true}"
: "${OFFSITE_SYNC_ENABLED:=true}"
: "${RCLONE_TRANSFERS:=1}"
: "${RCLONE_CHECKERS:=2}"
: "${RCLONE_TPSLIMIT:=4}"
: "${RCLONE_DRIVE_PACER_MIN_SLEEP:=500ms}"
: "${RCLONE_DRIVE_PACER_BURST:=1}"

export RCLONE_CONFIG RESTIC_REPOSITORY RESTIC_PASSWORD_FILE RESTIC_CACHE_DIR

DRY_RUN=false
RUN_COLLECTION=true
RUN_PRUNE=true
RUN_OFFSITE_SYNC=true
OFFSITE_ONLY=false

for arg in "$@"; do
  case "${arg}" in
    --dry-run) DRY_RUN=true ;;
    --no-collect) RUN_COLLECTION=false ;;
    --no-prune) RUN_PRUNE=false ;;
    --no-offsite-sync) RUN_OFFSITE_SYNC=false ;;
    --local-only) RUN_OFFSITE_SYNC=false ;;
    --offsite-only) OFFSITE_ONLY=true; RUN_COLLECTION=false; RUN_PRUNE=false ;;
    *) echo "Unknown argument: ${arg}" >&2; exit 2 ;;
  esac
done

RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)"
HOSTNAME_SHORT="$(hostname -s)"
RUN_DIR="${STAGING_DIR}/runs/${RUN_ID}"
REPORT_JSON="${REPORT_DIR}/backup-${RUN_ID}.json"
REPORT_MD="${REPORT_DIR}/backup-${RUN_ID}.md"
STATUS_JSON="${STATUS_DIR}/status.json"
LOG_FILE="${REPORT_DIR}/backup-${RUN_ID}.log"
EXISTING_INCLUDE_FILE="${RUN_DIR}/existing-includes.txt"
MISSING_INCLUDE_FILE="${RUN_DIR}/missing-includes.txt"
REMOTE_MANIFEST="${STAGING_DIR}/remote-exports/${RUN_ID}/MANIFEST.txt"

mkdir -p "${RUN_DIR}" "${REPORT_DIR}" "${STATUS_DIR}" "${RESTIC_CACHE_DIR}"

fail() {
  local msg="$1"
  jq -n \
    --arg status "failed" \
    --arg run_id "${RUN_ID}" \
    --arg host "${HOSTNAME_SHORT}" \
    --arg message "${msg}" \
    --arg timestamp "$(date -Is)" \
    '{status:$status,run_id:$run_id,host:$host,message:$message,timestamp:$timestamp}' | tee "${STATUS_JSON}" > "${REPORT_JSON}"
  echo "${msg}" >&2
  exit 1
}

command -v restic >/dev/null || fail "restic is not installed"
command -v rclone >/dev/null || fail "rclone is not installed"
command -v jq >/dev/null || fail "jq is not installed"
[[ -r "${RESTIC_PASSWORD_FILE}" ]] || fail "RESTIC_PASSWORD_FILE is missing or unreadable"
[[ -r "${INCLUDE_FILE}" ]] || fail "INCLUDE_FILE is missing or unreadable"
[[ -r "${EXCLUDE_FILE}" ]] || fail "EXCLUDE_FILE is missing or unreadable"
mkdir -p "${RESTIC_REPOSITORY}" "${RESTIC_CACHE_DIR}"

if ! rclone listremotes --config "${RCLONE_CONFIG}" | grep -qx "${RCLONE_REMOTE}:"; then
  fail "rclone remote '${RCLONE_REMOTE}:' is not configured in ${RCLONE_CONFIG}"
fi

if ! restic snapshots >/dev/null 2>&1; then
  fail "restic repository is not initialized; run edsys-init-restic.sh"
fi

{
  echo "EdSys backup run ${RUN_ID}"
  echo "Host: ${HOSTNAME_SHORT}"
  echo "Dry run: ${DRY_RUN}"
  echo "Repository: ${RESTIC_REPOSITORY}"
  echo "Offsite destination: ${RCLONE_OFFSITE_DEST}"
} | tee "${LOG_FILE}"

if [[ "${OFFSITE_ONLY}" == "false" && "${RUN_COLLECTION}" == "true" && "${REMOTE_COLLECTION_ENABLED}" == "true" && -x "${BACKUP_ROOT}/scripts/edsys-collect-remotes.sh" ]]; then
  "${BACKUP_ROOT}/scripts/edsys-collect-remotes.sh" "${RUN_ID}" | tee -a "${LOG_FILE}" || true
fi

: > "${EXISTING_INCLUDE_FILE}"
: > "${MISSING_INCLUDE_FILE}"
SNAPSHOT_ID=""
if [[ "${OFFSITE_ONLY}" == "false" ]]; then
  while IFS= read -r path || [[ -n "${path}" ]]; do
    [[ -z "${path}" || "${path}" =~ ^[[:space:]]*# ]] && continue
    if [[ -e "${path}" ]]; then
      printf '%s\n' "${path}" >> "${EXISTING_INCLUDE_FILE}"
    else
      printf '%s\n' "${path}" >> "${MISSING_INCLUDE_FILE}"
    fi
  done < "${INCLUDE_FILE}"

  if [[ ! -s "${EXISTING_INCLUDE_FILE}" ]]; then
    fail "no include paths exist; refusing to run empty backup"
  fi

  RESTIC_ARGS=(backup --files-from "${EXISTING_INCLUDE_FILE}" --exclude-file "${EXCLUDE_FILE}" --tag edsys-critical --tag "${HOSTNAME_SHORT}")
  if [[ "${DRY_RUN}" == "true" ]]; then
    RESTIC_ARGS+=(--dry-run)
  fi

  set +e
  restic "${RESTIC_ARGS[@]}" 2>&1 | tee -a "${LOG_FILE}"
  BACKUP_STATUS="${PIPESTATUS[0]}"
  set -e

  if [[ "${BACKUP_STATUS}" -ne 0 ]]; then
    fail "restic backup failed with exit code ${BACKUP_STATUS}"
  fi

  if [[ "${DRY_RUN}" != "true" ]]; then
    SNAPSHOT_ID="$(restic snapshots --latest 1 --json | jq -r '.[0].short_id // .[0].id // ""')"
    if [[ "${RUN_PRUNE}" == "true" ]]; then
      restic forget --keep-daily "${KEEP_DAILY}" --keep-weekly "${KEEP_WEEKLY}" --keep-monthly "${KEEP_MONTHLY}" --prune 2>&1 | tee -a "${LOG_FILE}"
    fi
  fi
fi

MISSING_COUNT="$(wc -l < "${MISSING_INCLUDE_FILE}" | tr -d ' ')"
INCLUDED_COUNT="$(wc -l < "${EXISTING_INCLUDE_FILE}" | tr -d ' ')"
REMOTE_FAILED_COUNT=0
if [[ -f "${REMOTE_MANIFEST}" ]]; then
  REMOTE_FAILED_COUNT="$(grep -c 'FAILED' "${REMOTE_MANIFEST}" || true)"
fi
OFFSITE_SYNC_STATUS="skipped"
if [[ "${RUN_OFFSITE_SYNC}" == "true" && "${OFFSITE_SYNC_ENABLED}" == "true" && "${DRY_RUN}" != "true" ]]; then
  OFFSITE_SYNC_STATUS="running"
  set +e
  rclone sync "${RESTIC_REPOSITORY}" "${RCLONE_OFFSITE_DEST}" \
    --config "${RCLONE_CONFIG}" \
    --exclude "locks/**" \
    --transfers "${RCLONE_TRANSFERS}" \
    --checkers "${RCLONE_CHECKERS}" \
    --tpslimit "${RCLONE_TPSLIMIT}" \
    --drive-pacer-min-sleep "${RCLONE_DRIVE_PACER_MIN_SLEEP}" \
    --drive-pacer-burst "${RCLONE_DRIVE_PACER_BURST}" \
    --retries 20 \
    --low-level-retries 50 \
    --retries-sleep 30s \
    --stats 30s \
    --log-file "${REPORT_DIR}/rclone-sync-${RUN_ID}.log" \
    --log-level INFO
  RCLONE_STATUS="$?"
  set -e
  if [[ "${RCLONE_STATUS}" -ne 0 ]]; then
    fail "rclone offsite sync failed with exit code ${RCLONE_STATUS}"
  fi
  OFFSITE_SYNC_STATUS="success"
fi

jq -n \
  --arg status "success" \
  --arg run_id "${RUN_ID}" \
  --arg host "${HOSTNAME_SHORT}" \
  --arg timestamp "$(date -Is)" \
  --arg repository "${RESTIC_REPOSITORY}" \
  --arg offsite_destination "${RCLONE_OFFSITE_DEST}" \
  --arg offsite_sync_status "${OFFSITE_SYNC_STATUS}" \
  --arg snapshot_id "${SNAPSHOT_ID}" \
  --argjson dry_run "${DRY_RUN}" \
  --argjson included_count "${INCLUDED_COUNT}" \
  --argjson missing_count "${MISSING_COUNT}" \
  --argjson remote_failed_count "${REMOTE_FAILED_COUNT}" \
  '{status:$status,run_id:$run_id,host:$host,timestamp:$timestamp,repository:$repository,offsite_destination:$offsite_destination,offsite_sync_status:$offsite_sync_status,snapshot_id:$snapshot_id,dry_run:$dry_run,included_path_count:$included_count,missing_path_count:$missing_count,remote_collection_failed_count:$remote_failed_count}' \
  | tee "${STATUS_JSON}" > "${REPORT_JSON}"

{
  echo "# EdSys Backup Report ${RUN_ID}"
  echo
  echo "- Status: success"
  echo "- Host: ${HOSTNAME_SHORT}"
  echo "- Dry run: ${DRY_RUN}"
  echo "- Snapshot: ${SNAPSHOT_ID:-none}"
  echo "- Included paths: ${INCLUDED_COUNT}"
  echo "- Missing paths: ${MISSING_COUNT}"
  echo "- Remote collection failures: ${REMOTE_FAILED_COUNT}"
  echo "- Repository: ${RESTIC_REPOSITORY}"
  echo "- Offsite destination: ${RCLONE_OFFSITE_DEST}"
  echo "- Offsite sync: ${OFFSITE_SYNC_STATUS}"
  echo
  echo "## Missing Paths"
  if [[ -s "${MISSING_INCLUDE_FILE}" ]]; then
    sed 's/^/- `/' "${MISSING_INCLUDE_FILE}" | sed 's/$/`/'
  else
    echo "None."
  fi
} > "${REPORT_MD}"

if [[ "${REPORT_TO_DRIVE}" == "true" && "${DRY_RUN}" != "true" ]]; then
  rclone copy --config "${RCLONE_CONFIG}" "${REPORT_JSON}" "${RCLONE_REMOTE}:${DRIVE_BACKUP_ROOT}/reports/" || true
  rclone copy --config "${RCLONE_CONFIG}" "${REPORT_MD}" "${RCLONE_REMOTE}:${DRIVE_BACKUP_ROOT}/reports/" || true
fi

echo "Backup completed: ${REPORT_JSON}"
