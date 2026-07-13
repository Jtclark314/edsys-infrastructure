#!/usr/bin/env bash
set -uo pipefail

# Pull the three authoritative EdSys checkouts forward to their exact origin/main
# revisions. This script never commits, pushes, resets, cleans, or deletes files.

umask 0027

repo_root="${EDSYS_REPO_ROOT:-/srv/edsys}"
log_file="${EDSYS_SYNC_LOG:-/srv/edsys/sync-edsys-repos.log}"
lock_file="${EDSYS_SYNC_LOCK:-/run/lock/edsys-git-sync.lock}"

readonly repo_root log_file lock_file
readonly -a repositories=(
  "EdSys-Master:main"
  "edsys-infrastructure:main"
  "edsys-infra-configs:main"
)

timestamp() {
  date -Is
}

log() {
  printf '[%s] %s\n' "$(timestamp)" "$*"
}

fail_repo() {
  local repo="$1"
  shift
  log "ERROR: ${repo}: $*"
  return 1
}

sync_repo() {
  local repo="$1"
  local branch="$2"
  local path="${repo_root}/${repo}"
  local dirty_output local_revision remote_revision

  log "SYNC: ${repo} -> origin/${branch}"

  if [[ ! -d "${path}/.git" ]]; then
    fail_repo "${repo}" "not a Git checkout at ${path}"
    return 1
  fi

  if ! dirty_output="$(git -C "${path}" status --porcelain=v1 --untracked-files=all 2>&1)"; then
    fail_repo "${repo}" "could not inspect working-tree status"
    printf '%s\n' "${dirty_output}"
    return 1
  fi
  if [[ -n "${dirty_output}" ]]; then
    fail_repo "${repo}" "working tree is not clean; refusing to change it"
    printf '%s\n' "${dirty_output}"
    return 1
  fi

  if ! git -C "${path}" fetch --quiet --prune origin \
    "+refs/heads/${branch}:refs/remotes/origin/${branch}"; then
    fail_repo "${repo}" "could not fetch origin/${branch}"
    return 1
  fi

  if ! git -C "${path}" show-ref --verify --quiet \
    "refs/remotes/origin/${branch}"; then
    fail_repo "${repo}" "origin/${branch} does not exist"
    return 1
  fi

  if git -C "${path}" show-ref --verify --quiet "refs/heads/${branch}"; then
    if ! git -C "${path}" switch --quiet "${branch}"; then
      fail_repo "${repo}" "could not switch to ${branch}"
      return 1
    fi
  else
    if ! git -C "${path}" switch --quiet --create "${branch}" \
      --track "origin/${branch}"; then
      fail_repo "${repo}" "could not create ${branch} from origin/${branch}"
      return 1
    fi
  fi

  if ! git -C "${path}" branch --quiet \
    --set-upstream-to="origin/${branch}" "${branch}"; then
    fail_repo "${repo}" "could not set the upstream for ${branch}"
    return 1
  fi

  if ! git -C "${path}" merge --quiet --ff-only "origin/${branch}"; then
    fail_repo "${repo}" "local ${branch} cannot fast-forward to origin/${branch}"
    return 1
  fi

  if ! local_revision="$(git -C "${path}" rev-parse "refs/heads/${branch}")"; then
    fail_repo "${repo}" "could not resolve local ${branch} revision"
    return 1
  fi
  if ! remote_revision="$(git -C "${path}" rev-parse "refs/remotes/origin/${branch}")"; then
    fail_repo "${repo}" "could not resolve origin/${branch} revision"
    return 1
  fi
  if [[ "${local_revision}" != "${remote_revision}" ]]; then
    fail_repo "${repo}" "local ${branch} does not exactly match origin/${branch}"
    return 1
  fi

  log "OK: ${repo}: ${branch} at ${local_revision:0:12}"
}

main() {
  local specification repo branch
  local result=0

  if [[ ! -d "${repo_root}" ]]; then
    printf 'EdSys repository root does not exist: %s\n' "${repo_root}" >&2
    return 2
  fi

  if [[ ! -e "${log_file}" ]]; then
    if ! (umask 0027 && : >"${log_file}"); then
      printf 'Cannot create EdSys Git sync log: %s\n' "${log_file}" >&2
      return 2
    fi
  elif [[ ! -w "${log_file}" ]]; then
    printf 'EdSys Git sync log is not writable: %s\n' "${log_file}" >&2
    return 2
  fi

  if ! exec 9>"${lock_file}"; then
    printf 'Cannot open EdSys Git sync lock: %s\n' "${lock_file}" >&2
    return 2
  fi
  if ! flock -n 9; then
    printf '[%s] SKIP: another EdSys Git sync is already running\n' \
      "$(timestamp)" >>"${log_file}"
    return 0
  fi

  exec >>"${log_file}" 2>&1
  log "START: EdSys Git sync"

  for specification in "${repositories[@]}"; do
    repo="${specification%%:*}"
    branch="${specification#*:}"
    if ! sync_repo "${repo}" "${branch}"; then
      result=1
    fi
  done

  if ((result == 0)); then
    log "COMPLETE: all authoritative EdSys checkouts match origin/main"
  else
    log "FAILED: one or more EdSys checkouts require attention"
  fi

  return "${result}"
}

main "$@"
