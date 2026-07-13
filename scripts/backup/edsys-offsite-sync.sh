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
: "${REPORT_DIR:=${BACKUP_ROOT}/reports}"
: "${STATUS_DIR:=/var/lib/edsys-backup}"
: "${RCLONE_CONFIG:=/etc/edsys-backup/rclone.conf}"
: "${RCLONE_BIN:=/opt/edsys-tools/rclone/current/rclone}"
: "${RCLONE_REMOTE:=edsys-gdrive}"
: "${DRIVE_BACKUP_ROOT:=EdSys Backups}"
: "${RCLONE_OFFSITE_DEST:=${RCLONE_REMOTE}:${DRIVE_BACKUP_ROOT}/restic/edsys-critical-v3}"
: "${RESTIC_REPOSITORY:=/srv/edsys-backup/restic-repo/edsys-critical}"
: "${OFFSITE_SYNC_MODE:=balanced}"
: "${RCLONE_DRIVE_PACER_MIN_SLEEP:=20ms}"
: "${RCLONE_DRIVE_PACER_BURST:=100}"
: "${RCLONE_DRIVE_CHUNK_SIZE:=64M}"
: "${RCLONE_BUFFER_SIZE:=32M}"
: "${RCLONE_RETRIES:=20}"
: "${RCLONE_LOW_LEVEL_RETRIES:=50}"
: "${RCLONE_RETRIES_SLEEP:=30s}"
: "${RCLONE_TIMEOUT:=5m}"
: "${RCLONE_CONTIMEOUT:=30s}"

DRY_RUN=false
TEST_ONLY=false
MODE="${OFFSITE_SYNC_MODE}"
for arg in "$@"; do
  case "${arg}" in
    --dry-run) DRY_RUN=true ;;
    --test-only|--small-test) TEST_ONLY=true ;;
    --mode=*) MODE="${arg#--mode=}" ;;
    *) echo "Unknown argument: ${arg}" >&2; exit 2 ;;
  esac
done

case "${MODE}" in
  conservative)
    TRANSFERS="${RCLONE_CONSERVATIVE_TRANSFERS:-1}"
    CHECKERS="${RCLONE_CONSERVATIVE_CHECKERS:-2}"
    TPSLIMIT="${RCLONE_CONSERVATIVE_TPSLIMIT:-4}"
    TPSBURST="${RCLONE_CONSERVATIVE_TPSLIMIT_BURST:-4}"
    ;;
  balanced)
    TRANSFERS="${RCLONE_BALANCED_TRANSFERS:-2}"
    CHECKERS="${RCLONE_BALANCED_CHECKERS:-4}"
    TPSLIMIT="${RCLONE_BALANCED_TPSLIMIT:-8}"
    TPSBURST="${RCLONE_BALANCED_TPSLIMIT_BURST:-8}"
    ;;
  fast)
    TRANSFERS="${RCLONE_FAST_TRANSFERS:-4}"
    CHECKERS="${RCLONE_FAST_CHECKERS:-8}"
    TPSLIMIT="${RCLONE_FAST_TPSLIMIT:-12}"
    TPSBURST="${RCLONE_FAST_TPSLIMIT_BURST:-12}"
    ;;
  *) echo "Unknown sync mode: ${MODE}" >&2; exit 2 ;;
esac

RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)"
LOG_FILE="${REPORT_DIR}/rclone-sync-${RUN_ID}.log"
STATUS_JSON="${STATUS_DIR}/offsite-status.json"
LOCK_FILE="/run/edsys-offsite-sync.lock"

mkdir -p "${REPORT_DIR}" "${STATUS_DIR}"
exec 9>"${LOCK_FILE}"
if ! flock -n 9; then
  echo "Another EdSys offsite sync is already running." >&2
  exit 3
fi

fail() {
  local msg="$1"
  jq -n --arg status "failed" --arg run_id "${RUN_ID}" --arg message "${msg}" --arg timestamp "$(date -Is)" \
    '{status:$status,run_id:$run_id,message:$message,timestamp:$timestamp}' | tee "${STATUS_JSON}" >/dev/null
  echo "${msg}" >&2
  exit 1
}

command -v "${RCLONE_BIN}" >/dev/null || fail "pinned rclone is not installed: ${RCLONE_BIN}"
command -v jq >/dev/null || fail "jq is not installed"
[[ -d "${RESTIC_REPOSITORY}" ]] || fail "local restic repository is missing: ${RESTIC_REPOSITORY}"
[[ -r "${RCLONE_CONFIG}" ]] || fail "rclone config is missing or unreadable: ${RCLONE_CONFIG}"

if ! "${RCLONE_BIN}" listremotes --config "${RCLONE_CONFIG}" | grep -qx "${RCLONE_REMOTE}:"; then
  fail "rclone remote '${RCLONE_REMOTE}:' is not configured"
fi

if ! grep -qE '^[[:space:]]*client_id[[:space:]]*=[[:space:]]*[^[:space:]]+' "${RCLONE_CONFIG}" ||
   ! grep -qE '^[[:space:]]*client_secret[[:space:]]*=[[:space:]]*[^[:space:]]+' "${RCLONE_CONFIG}"; then
  fail "refusing offsite sync: ${RCLONE_REMOTE} does not have a custom Google OAuth client_id and client_secret"
fi

"${RCLONE_BIN}" about --config "${RCLONE_CONFIG}" "${RCLONE_REMOTE}:" >/dev/null || fail "rclone remote '${RCLONE_REMOTE}:' failed connectivity check"

if [[ "${TEST_ONLY}" == "true" ]]; then
  TEST_FILE="$(mktemp)"
  printf 'EdSys offsite test %s\n' "${RUN_ID}" > "${TEST_FILE}"
  "${RCLONE_BIN}" copyto --config "${RCLONE_CONFIG}" "${TEST_FILE}" "${RCLONE_REMOTE}:${DRIVE_BACKUP_ROOT}/manual-exports/offsite-test/${RUN_ID}.txt" \
    --log-file "${LOG_FILE}" --log-level INFO
  rm -f "${TEST_FILE}"
  jq -n --arg status "success" --arg run_id "${RUN_ID}" --arg mode "test-only" --arg timestamp "$(date -Is)" \
    '{status:$status,run_id:$run_id,mode:$mode,timestamp:$timestamp}' | tee "${STATUS_JSON}" >/dev/null
  echo "Small offsite test succeeded: ${LOG_FILE}"
  exit 0
fi

RCLONE_ARGS=(
  sync "${RESTIC_REPOSITORY}" "${RCLONE_OFFSITE_DEST}"
  --config "${RCLONE_CONFIG}"
  --exclude "locks/**"
  --transfers "${TRANSFERS}"
  --checkers "${CHECKERS}"
  --tpslimit "${TPSLIMIT}"
  --tpslimit-burst "${TPSBURST}"
  --drive-pacer-min-sleep "${RCLONE_DRIVE_PACER_MIN_SLEEP}"
  --drive-pacer-burst "${RCLONE_DRIVE_PACER_BURST}"
  --drive-chunk-size "${RCLONE_DRIVE_CHUNK_SIZE}"
  --buffer-size "${RCLONE_BUFFER_SIZE}"
  --retries "${RCLONE_RETRIES}"
  --low-level-retries "${RCLONE_LOW_LEVEL_RETRIES}"
  --retries-sleep "${RCLONE_RETRIES_SLEEP}"
  --timeout "${RCLONE_TIMEOUT}"
  --contimeout "${RCLONE_CONTIMEOUT}"
  --stats 30s
  --stats-one-line
  --fast-list
  --log-file "${LOG_FILE}"
  --log-level INFO
)

if [[ "${DRY_RUN}" == "true" ]]; then
  RCLONE_ARGS+=(--dry-run)
fi

set +e
"${RCLONE_BIN}" "${RCLONE_ARGS[@]}"
SYNC_STATUS="$?"
set -e
if [[ "${SYNC_STATUS}" -ne 0 ]]; then
  fail "rclone offsite sync failed with exit code ${SYNC_STATUS}"
fi

jq -n \
  --arg status "success" \
  --arg run_id "${RUN_ID}" \
  --arg mode "${MODE}" \
  --arg destination "${RCLONE_OFFSITE_DEST}" \
  --arg log_file "${LOG_FILE}" \
  --arg timestamp "$(date -Is)" \
  --argjson dry_run "${DRY_RUN}" \
  '{status:$status,run_id:$run_id,mode:$mode,destination:$destination,log_file:$log_file,timestamp:$timestamp,dry_run:$dry_run}' \
  | tee "${STATUS_JSON}" >/dev/null

echo "Offsite sync completed: ${LOG_FILE}"
