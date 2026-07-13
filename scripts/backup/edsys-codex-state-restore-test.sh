#!/usr/bin/env bash
set -Eeuo pipefail
umask 0077

CONFIG_FILE="${EDSYS_BACKUP_CONFIG:-/etc/edsys-backup/edsys-backup.conf}"
if [[ -f "${CONFIG_FILE}" ]]; then
  # shellcheck disable=SC1090
  source "${CONFIG_FILE}"
fi

: "${BACKUP_ROOT:=/srv/edsys-backup}"
: "${RESTIC_REPOSITORY:=/srv/edsys-backup/restic-repo/edsys-critical}"
: "${RESTIC_PASSWORD_FILE:=/etc/edsys-backup/restic-password}"
: "${RESTIC_CACHE_DIR:=/var/cache/edsys-backup/restic}"
: "${RESTORE_TEST_DIR:=${BACKUP_ROOT}/restore-tests}"
: "${REPORT_DIR:=${BACKUP_ROOT}/reports}"
: "${CODEX_RESTORE_KEEP_RUNS:=3}"
: "${CODEX_RESTORE_KEEP_REPORTS:=12}"

export RESTIC_REPOSITORY RESTIC_PASSWORD_FILE RESTIC_CACHE_DIR

RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)"
TARGET_ROOT="${RESTORE_TEST_DIR}/codex-state"
TARGET="${TARGET_ROOT}/${RUN_ID}"
INCOMING="${TARGET_ROOT}/.incoming-${RUN_ID}"
REPORT_ROOT="${REPORT_DIR}/codex-state"
REPORT="${REPORT_ROOT}/restore-test-${RUN_ID}.json"
REPORT_INCOMING=""
STAGED_PATH="${INCOMING}/srv/edsys-backup/staging/codex-state/current"
LOCK_FILE="/run/edsys-codex-state-restore-test.lock"

require_positive_integer() {
  local name="$1"
  local value="$2"
  if [[ ! "${value}" =~ ^[1-9][0-9]*$ ]]; then
    echo "${name} must be a positive integer" >&2
    exit 2
  fi
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

require_plain_directory() {
  local path="$1"
  local parent="$2"
  local expected_real
  local path_real
  local parent_real

  [[ "${path}" == /* && "${parent}" == /* ]] || {
    echo "Codex restore/report paths must be absolute" >&2
    exit 2
  }
  if [[ ! -d "${parent}" || -L "${parent}" || -L "${path}" ]] ||
    ! has_no_symlink_components "${parent}"; then
    echo "Refusing missing, non-directory, or symlinked Codex restore/report path" >&2
    exit 2
  fi

  install -d -m 0700 "${path}"
  has_no_symlink_components "${path}" || {
    echo "Refusing symlink component in Codex restore/report path" >&2
    exit 2
  }
  parent_real="$(realpath -e -- "${parent}")"
  path_real="$(realpath -e -- "${path}")"
  expected_real="${parent_real}/codex-state"

  [[ "${path_real}" == "${expected_real}" ]] || {
    echo "Refusing non-canonical Codex restore/report path" >&2
    exit 2
  }
}

prune_restore_runs() {
  local root_real
  local keep="$1"
  local candidate
  local candidate_real
  local -a names=()
  local index

  root_real="$(realpath -e -- "${TARGET_ROOT}")"
  mapfile -t names < <(
    find -P "${TARGET_ROOT}" -mindepth 1 -maxdepth 1 -type d \
      -printf '%f\n' \
      | grep -E '^[0-9]{8}T[0-9]{6}Z$' \
      | LC_ALL=C sort -r
  )

  for ((index = keep; index < ${#names[@]}; index++)); do
    candidate="${TARGET_ROOT}/${names[index]}"
    [[ ! -L "${candidate}" && -d "${candidate}" ]] || {
      echo "Refusing unsafe Codex restore retention candidate" >&2
      exit 2
    }
    mountpoint -q -- "${candidate}" && {
      echo "Refusing mounted Codex restore retention candidate" >&2
      exit 2
    }
    candidate_real="$(realpath -e -- "${candidate}")"
    [[ "${candidate_real}" == "${root_real}/${names[index]}" ]] || {
      echo "Refusing non-canonical Codex restore retention candidate" >&2
      exit 2
    }
    if findmnt --raw -rn -o TARGET \
      | awk -v root="${candidate_real}" \
        '$0 == root || index($0, root "/") == 1 { found = 1 } END { exit !found }'; then
      echo "Refusing Codex restore retention candidate containing a mount" >&2
      exit 2
    fi
    rm -rf --one-file-system -- "${candidate}"
    [[ ! -e "${candidate}" ]] || {
      echo "Could not remove expired Codex restore run" >&2
      exit 2
    }
  done
}

prune_restore_reports() {
  local root_real
  local keep="$1"
  local candidate
  local candidate_real
  local -a names=()
  local index

  root_real="$(realpath -e -- "${REPORT_ROOT}")"
  mapfile -t names < <(
    find -P "${REPORT_ROOT}" -mindepth 1 -maxdepth 1 -type f \
      -printf '%f\n' \
      | grep -E '^restore-test-[0-9]{8}T[0-9]{6}Z\.json$' \
      | LC_ALL=C sort -r
  )

  for ((index = keep; index < ${#names[@]}; index++)); do
    candidate="${REPORT_ROOT}/${names[index]}"
    [[ ! -L "${candidate}" && -f "${candidate}" ]] || {
      echo "Refusing unsafe Codex restore-report retention candidate" >&2
      exit 2
    }
    candidate_real="$(realpath -e -- "${candidate}")"
    [[ "${candidate_real}" == "${root_real}/${names[index]}" ]] || {
      echo "Refusing non-canonical Codex restore-report retention candidate" >&2
      exit 2
    }
    rm -f -- "${candidate}"
    [[ ! -e "${candidate}" ]] || {
      echo "Could not remove expired Codex restore report" >&2
      exit 2
    }
  done
}

prune_stale_incoming_artifacts() {
  local target_root_real
  local report_root_real
  local candidate
  local candidate_real
  local name

  target_root_real="$(realpath -e -- "${TARGET_ROOT}")"
  report_root_real="$(realpath -e -- "${REPORT_ROOT}")"

  while IFS= read -r name; do
    candidate="${TARGET_ROOT}/${name}"
    [[ ! -L "${candidate}" && -d "${candidate}" ]] || {
      echo "Refusing unsafe stale Codex restore candidate" >&2
      exit 2
    }
    mountpoint -q -- "${candidate}" && {
      echo "Refusing mounted stale Codex restore candidate" >&2
      exit 2
    }
    candidate_real="$(realpath -e -- "${candidate}")"
    [[ "${candidate_real}" == "${target_root_real}/${name}" ]] || {
      echo "Refusing non-canonical stale Codex restore candidate" >&2
      exit 2
    }
    if findmnt --raw -rn -o TARGET \
      | awk -v root="${candidate_real}" \
        '$0 == root || index($0, root "/") == 1 { found = 1 } END { exit !found }'; then
      echo "Refusing stale Codex restore candidate containing a mount" >&2
      exit 2
    fi
    rm -rf --one-file-system -- "${candidate}"
  done < <(
    find -P "${TARGET_ROOT}" -mindepth 1 -maxdepth 1 -type d \
      -printf '%f\n' \
      | grep -E '^\.incoming-[0-9]{8}T[0-9]{6}Z$' \
      | LC_ALL=C sort
  )

  while IFS= read -r name; do
    candidate="${REPORT_ROOT}/${name}"
    [[ ! -L "${candidate}" && -f "${candidate}" ]] || {
      echo "Refusing unsafe stale Codex report candidate" >&2
      exit 2
    }
    candidate_real="$(realpath -e -- "${candidate}")"
    [[ "${candidate_real}" == "${report_root_real}/${name}" ]] || {
      echo "Refusing non-canonical stale Codex report candidate" >&2
      exit 2
    }
    rm -f -- "${candidate}"
  done < <(
    find -P "${REPORT_ROOT}" -mindepth 1 -maxdepth 1 -type f \
      -printf '%f\n' \
      | grep -E '^\.restore-test-[0-9]{8}T[0-9]{6}Z\.json\.tmp\.[A-Za-z0-9]+$' \
      | LC_ALL=C sort
  )
}

command -v restic >/dev/null || { echo "restic is not installed" >&2; exit 2; }
command -v jq >/dev/null || { echo "jq is not installed" >&2; exit 2; }
command -v realpath >/dev/null || { echo "realpath is not installed" >&2; exit 2; }
command -v mountpoint >/dev/null || { echo "mountpoint is not installed" >&2; exit 2; }
command -v findmnt >/dev/null || { echo "findmnt is not installed" >&2; exit 2; }
command -v sync >/dev/null || { echo "sync is not installed" >&2; exit 2; }
require_positive_integer CODEX_RESTORE_KEEP_RUNS "${CODEX_RESTORE_KEEP_RUNS}"
require_positive_integer CODEX_RESTORE_KEEP_REPORTS "${CODEX_RESTORE_KEEP_REPORTS}"
[[ -x "${BACKUP_ROOT}/scripts/edsys-codex-state-stage.py" ]] || {
  echo "Codex state verification helper is missing or not executable" >&2
  exit 2
}
[[ -r "${RESTIC_PASSWORD_FILE}" ]] || {
  echo "Restic password file is missing or unreadable" >&2
  exit 2
}
require_plain_directory "${TARGET_ROOT}" "${RESTORE_TEST_DIR}"
require_plain_directory "${REPORT_ROOT}" "${REPORT_DIR}"
exec 9>"${LOCK_FILE}"
if ! flock -n 9; then
  echo "Another Codex state restore test is already running." >&2
  exit 3
fi

[[ ! -e "${TARGET}" && ! -e "${INCOMING}" ]] || {
  echo "Codex restore target already exists: ${RUN_ID}" >&2
  exit 2
}
install -d -m 0700 "${INCOMING}"
cleanup() {
  if [[ -d "${INCOMING}" ]]; then
    rm -rf --one-file-system "${INCOMING}"
  fi
  if [[ -n "${REPORT_INCOMING}" && -f "${REPORT_INCOMING}" && ! -L "${REPORT_INCOMING}" ]]; then
    rm -f -- "${REPORT_INCOMING}"
  fi
}
trap cleanup EXIT

SNAPSHOT_ID="$(restic snapshots --tag edsys-critical --json | jq -er 'max_by(.time).id')"
restic restore "${SNAPSHOT_ID}" \
  --target "${INCOMING}" \
  --include "/srv/edsys-backup/staging/codex-state/current"

VERIFY_JSON="$("${BACKUP_ROOT}/scripts/edsys-codex-state-stage.py" verify "${STAGED_PATH}")"
printf '%s' "${VERIFY_JSON}" | jq -e '.status == "ok" and .database_count > 0' >/dev/null

mv "${INCOMING}" "${TARGET}"
INCOMING=""

REPORT_INCOMING="$(mktemp --tmpdir="${REPORT_ROOT}" ".restore-test-${RUN_ID}.json.tmp.XXXXXX")"
jq -n \
  --arg status "success" \
  --arg run_id "${RUN_ID}" \
  --arg restored_from "${SNAPSHOT_ID:0:12}" \
  --arg staging_path "/srv/edsys-backup/staging/codex-state/current" \
  --argjson verification "${VERIFY_JSON}" \
  '{status:$status,run_id:$run_id,restored_from:$restored_from,staging_path:$staging_path,verification:$verification}' \
  > "${REPORT_INCOMING}"
chmod 0600 "${REPORT_INCOMING}"
jq -e '.status == "success" and .verification.status == "ok"' "${REPORT_INCOMING}" >/dev/null
sync -f "${REPORT_INCOMING}"
mv -fT -- "${REPORT_INCOMING}" "${REPORT}"
REPORT_INCOMING=""
sync -f "${REPORT_ROOT}"

# Retention runs only after the new restore and report have both succeeded.
# Unexpected names and symlinks are intentionally ignored rather than removed.
prune_stale_incoming_artifacts
prune_restore_runs "${CODEX_RESTORE_KEEP_RUNS}"
prune_restore_reports "${CODEX_RESTORE_KEEP_REPORTS}"
trap - EXIT

echo "Codex state restore drill succeeded: ${RUN_ID}"
