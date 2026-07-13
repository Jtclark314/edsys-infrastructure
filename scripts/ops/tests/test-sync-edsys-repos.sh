#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
sync_script="${script_dir}/sync-edsys-repos.sh"
temp_root="$(mktemp -d)"
trap 'rm -rf "${temp_root}"' EXIT

repos_root="${temp_root}/checkouts"
remotes_root="${temp_root}/remotes"
seed_root="${temp_root}/seed"
log_file="${temp_root}/sync.log"
lock_file="${temp_root}/sync.lock"
mkdir -p "${repos_root}" "${remotes_root}" "${seed_root}"

create_repo() {
  local name="$1"
  local bare="${remotes_root}/${name}.git"
  local seed="${seed_root}/${name}"

  git init --quiet --bare --initial-branch=main "${bare}"
  git init --quiet --initial-branch=main "${seed}"
  git -C "${seed}" config user.name "EdSys Sync Test"
  git -C "${seed}" config user.email "sync-test@example.invalid"
  printf 'initial\n' >"${seed}/state.txt"
  git -C "${seed}" add state.txt
  git -C "${seed}" commit --quiet -m initial
  git -C "${seed}" remote add origin "${bare}"
  git -C "${seed}" push --quiet -u origin main
  git clone --quiet "${bare}" "${repos_root}/${name}"
}

for name in EdSys-Master edsys-infrastructure edsys-infra-configs; do
  create_repo "${name}"
done

# Reproduce the retired live state: the remote exposes only main while this
# checkout is still on a stale local master branch.
git -C "${repos_root}/edsys-infra-configs" switch --quiet --create master
git -C "${repos_root}/edsys-infra-configs" branch -D main >/dev/null

printf 'second\n' >>"${seed_root}/edsys-infra-configs/state.txt"
git -C "${seed_root}/edsys-infra-configs" add state.txt
git -C "${seed_root}/edsys-infra-configs" commit --quiet -m second
git -C "${seed_root}/edsys-infra-configs" push --quiet

EDSYS_REPO_ROOT="${repos_root}" \
EDSYS_SYNC_LOG="${log_file}" \
EDSYS_SYNC_LOCK="${lock_file}" \
  "${sync_script}"

test "$(git -C "${repos_root}/edsys-infra-configs" rev-parse HEAD)" = \
  "$(git -C "${seed_root}/edsys-infra-configs" rev-parse HEAD)"
test "$(git -C "${repos_root}/edsys-infra-configs" branch --show-current)" = main
test "$(git -C "${repos_root}/edsys-infra-configs" rev-parse --abbrev-ref '@{upstream}')" = \
  origin/main
grep -Fq 'COMPLETE: all authoritative EdSys checkouts match origin/main' "${log_file}"

printf 'local change\n' >>"${repos_root}/EdSys-Master/state.txt"
if EDSYS_REPO_ROOT="${repos_root}" \
  EDSYS_SYNC_LOG="${log_file}" \
  EDSYS_SYNC_LOCK="${lock_file}" \
  "${sync_script}"; then
  echo "Expected a dirty authoritative checkout to fail the sync." >&2
  exit 1
fi

grep -Fq 'ERROR: EdSys-Master: working tree is not clean' "${log_file}"
grep -Fq 'FAILED: one or more EdSys checkouts require attention' "${log_file}"

echo "EdSys Git sync integration test passed."
