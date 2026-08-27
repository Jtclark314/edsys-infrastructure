#!/usr/bin/env bash
set -Eeuo pipefail

readonly expected_stack=/srv/edsys/edsys-infrastructure/docker/edcore-automation
requested_phase=${1:-}

case "$requested_phase" in
  --transfer|--runtime|--coherent) ;;
  *)
    echo "Usage: $0 --transfer|--runtime|--coherent" >&2
    exit 64
    ;;
esac

phase=$requested_phase

[[ ${EUID} -eq 0 ]] || { echo "Source guard must run as root." >&2; exit 1; }
[[ -d "$expected_stack" ]] || { echo "Expected source tree is absent: $expected_stack" >&2; exit 1; }
[[ $(readlink -e -- "$expected_stack") == "$expected_stack" ]] || {
  echo "Source tree or an ancestor is a symlink; refusing root execution." >&2
  exit 1
}

fail() {
  echo "Unsafe automation source: $*" >&2
  exit 1
}

stat_triplet() {
  stat -c '%u:%g:%a' -- "$1"
}

require_triplet() {
  local path=$1 expected=$2 actual
  actual=$(stat_triplet "$path") || fail "cannot stat $path"
  [[ $actual == "$expected" ]] || fail "$path is $actual; expected $expected"
}

require_safe_chain() {
  local path=$1 current mode owner
  [[ $path == /* ]] || fail "path is not absolute: $path"
  path=$(readlink -e -- "$path") || fail "path does not resolve: $path"
  current=$path
  while :; do
    owner=$(stat -c '%u:%g' -- "$current") || fail "cannot stat path component $current"
    mode=$(stat -c '%a' -- "$current") || fail "cannot read mode for $current"
    [[ $owner == 0:0 ]] || fail "path component $current is owned by $owner, not root:root"
    (( (8#$mode & 8#022) == 0 )) || fail "path component $current is group/world writable ($mode)"
    [[ $current == / ]] && break
    current=$(dirname -- "$current")
  done
}

require_safe_executable() {
  local path=$1 mode
  [[ -f "$path" && -x "$path" ]] || fail "required executable is absent or not executable: $path"
  require_safe_chain "$path"
  mode=$(stat -c '%a' -- "$(readlink -e -- "$path")")
  (( (8#$mode & 8#111) != 0 )) || fail "required executable has no execute bit: $path"
}

# Root systemd traverses this path and reads Compose/source from it. Every
# ancestor must be root-owned and immutable to non-root users.
require_safe_chain "$expected_stack"

# Firewall protection must be able to start both before the first application
# deployment and after it. Coherent accepts either of the two complete exact
# states, never a mixed or permissive state, and still performs system-path
# checks below.
if [[ $phase == --coherent ]]; then
  phase=--transfer
  if [[ $(stat_triplet "$expected_stack/mosquitto/aclfile") == 1883:0:640 ]]; then
    phase=--runtime
  fi
fi

special=$(find "$expected_stack" -xdev \! -type d \! -type f -print -quit)
[[ -z $special ]] || fail "symlink or special file is forbidden: $special"
linked=$(find "$expected_stack" -xdev -type f -links +1 -print -quit)
[[ -z $linked ]] || fail "hard-linked source file is forbidden: $linked"

while IFS= read -r -d '' directory; do
  require_triplet "$directory" 0:0:755
done < <(find "$expected_stack" -xdev -type d -print0)

while IFS= read -r -d '' file; do
  relative=${file#"$expected_stack"/}
  expected=0:0:644
  case "$relative" in
    .env)
      expected=0:0:640
      ;;
    scripts/*.sh|node-red/entrypoint.sh)
      expected=0:0:755
      ;;
    mosquitto/mosquitto.conf|mosquitto/aclfile|mosquitto/aclfile-internal)
      if [[ $phase == --runtime ]]; then
        expected=1883:0:640
      fi
      ;;
  esac
  require_triplet "$file" "$expected"
done < <(find "$expected_stack" -xdev -type f -print0)

if [[ $phase == --runtime || $requested_phase == --coherent ]]; then
  require_safe_executable /usr/bin/docker
  require_safe_executable /usr/local/sbin/edsys-automation-firewall
  require_safe_executable /usr/local/sbin/edsys-automation-source-guard
  require_safe_executable "$expected_stack/scripts/backup.sh"
  require_safe_executable "$expected_stack/scripts/restore-test.sh"
fi

printf 'automation_source_permissions=passed phase=%s\n' "${phase#--}"
