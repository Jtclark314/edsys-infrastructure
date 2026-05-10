#!/usr/bin/env bash
set -Eeuo pipefail

CONFIG_FILE="${EDSYS_BACKUP_CONFIG:-/etc/edsys-backup/edsys-backup.conf}"
if [[ ! -f "${CONFIG_FILE}" ]]; then
  echo "Missing config file: ${CONFIG_FILE}" >&2
  exit 2
fi
# shellcheck disable=SC1090
source "${CONFIG_FILE}"

: "${BACKUP_ROOT:=/srv/edsys-backup}"
: "${STAGING_DIR:=${BACKUP_ROOT}/staging}"
: "${REPORT_DIR:=${BACKUP_ROOT}/reports}"
: "${STATUS_DIR:=/var/lib/edsys-backup}"
: "${RESTIC_REPOSITORY:=/srv/edsys-backup/restic-repo/edsys-critical}"
: "${RESTIC_PASSWORD_FILE:=/etc/edsys-backup/restic-password}"
: "${RESTIC_CACHE_DIR:=/var/cache/edsys-backup/restic}"
: "${INCLUDE_FILE:=/etc/edsys-backup/includes.txt}"
: "${EXCLUDE_FILE:=/etc/edsys-backup/excludes.txt}"
: "${KEEP_DAILY:=30}"
: "${KEEP_WEEKLY:=12}"
: "${KEEP_MONTHLY:=12}"
: "${REMOTE_COLLECTION_ENABLED:=true}"

export RESTIC_REPOSITORY RESTIC_PASSWORD_FILE RESTIC_CACHE_DIR

DRY_RUN=false
RUN_COLLECTION=true
for arg in "$@"; do
  case "${arg}" in
    --dry-run) DRY_RUN=true ;;
    --local-only|--no-offsite-sync) ;;
    --no-collect) RUN_COLLECTION=false ;;
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
LOCK_FILE="/run/edsys-backup.lock"

mkdir -p "${RUN_DIR}" "${REPORT_DIR}" "${STATUS_DIR}" "${RESTIC_CACHE_DIR}" "${RESTIC_REPOSITORY}"

exec 9>"${LOCK_FILE}"
if ! flock -n 9; then
  echo "Another local EdSys backup is already running." >&2
  exit 3
fi

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
command -v jq >/dev/null || fail "jq is not installed"
[[ -r "${RESTIC_PASSWORD_FILE}" ]] || fail "RESTIC_PASSWORD_FILE is missing or unreadable"
[[ -r "${INCLUDE_FILE}" ]] || fail "INCLUDE_FILE is missing or unreadable"
[[ -r "${EXCLUDE_FILE}" ]] || fail "EXCLUDE_FILE is missing or unreadable"

if ! restic snapshots >/dev/null 2>&1; then
  restic init 2>&1 | tee -a "${LOG_FILE}"
fi

{
  echo "EdSys local backup run ${RUN_ID}"
  echo "Host: ${HOSTNAME_SHORT}"
  echo "Dry run: ${DRY_RUN}"
  echo "Repository: ${RESTIC_REPOSITORY}"
  echo "Offsite sync: not run by this script"
} | tee "${LOG_FILE}"

if [[ "${RUN_COLLECTION}" == "true" && "${REMOTE_COLLECTION_ENABLED}" == "true" && -x "${BACKUP_ROOT}/scripts/edsys-collect-remotes.sh" ]]; then
  "${BACKUP_ROOT}/scripts/edsys-collect-remotes.sh" "${RUN_ID}" | tee -a "${LOG_FILE}" || true
fi

: > "${EXISTING_INCLUDE_FILE}"
: > "${MISSING_INCLUDE_FILE}"
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

SNAPSHOT_ID=""
if [[ "${DRY_RUN}" != "true" ]]; then
  SNAPSHOT_ID="$(restic snapshots --latest 1 --json | jq -r '.[0].short_id // .[0].id // ""')"
  restic forget --keep-daily "${KEEP_DAILY}" --keep-weekly "${KEEP_WEEKLY}" --keep-monthly "${KEEP_MONTHLY}" --prune 2>&1 | tee -a "${LOG_FILE}"
fi

MISSING_COUNT="$(wc -l < "${MISSING_INCLUDE_FILE}" | tr -d ' ')"
INCLUDED_COUNT="$(wc -l < "${EXISTING_INCLUDE_FILE}" | tr -d ' ')"
REMOTE_FAILED_COUNT=0
if [[ -f "${REMOTE_MANIFEST}" ]]; then
  REMOTE_FAILED_COUNT="$(grep -c 'FAILED' "${REMOTE_MANIFEST}" || true)"
fi

jq -n \
  --arg status "success" \
  --arg run_id "${RUN_ID}" \
  --arg host "${HOSTNAME_SHORT}" \
  --arg timestamp "$(date -Is)" \
  --arg repository "${RESTIC_REPOSITORY}" \
  --arg snapshot_id "${SNAPSHOT_ID}" \
  --arg offsite_sync_status "not_run" \
  --argjson dry_run "${DRY_RUN}" \
  --argjson included_count "${INCLUDED_COUNT}" \
  --argjson missing_count "${MISSING_COUNT}" \
  --argjson remote_failed_count "${REMOTE_FAILED_COUNT}" \
  '{status:$status,run_id:$run_id,host:$host,timestamp:$timestamp,repository:$repository,snapshot_id:$snapshot_id,offsite_sync_status:$offsite_sync_status,dry_run:$dry_run,included_path_count:$included_count,missing_path_count:$missing_count,remote_collection_failed_count:$remote_failed_count}' \
  | tee "${STATUS_JSON}" > "${REPORT_JSON}"

{
  echo "# EdSys Local Backup Report ${RUN_ID}"
  echo
  echo "- Status: success"
  echo "- Host: ${HOSTNAME_SHORT}"
  echo "- Dry run: ${DRY_RUN}"
  echo "- Snapshot: ${SNAPSHOT_ID:-none}"
  echo "- Included paths: ${INCLUDED_COUNT}"
  echo "- Missing paths: ${MISSING_COUNT}"
  echo "- Remote collection failures: ${REMOTE_FAILED_COUNT}"
  echo "- Repository: ${RESTIC_REPOSITORY}"
  echo "- Offsite sync: not run by this script"
  echo
  echo "## Missing Paths"
  if [[ -s "${MISSING_INCLUDE_FILE}" ]]; then
    sed 's/^/- `/' "${MISSING_INCLUDE_FILE}" | sed 's/$/`/'
  else
    echo "None."
  fi
} > "${REPORT_MD}"

echo "Local backup completed: ${REPORT_JSON}"
