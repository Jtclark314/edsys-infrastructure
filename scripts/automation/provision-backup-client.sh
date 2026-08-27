#!/usr/bin/env bash
# Provision the 9950x half of the dedicated EdCore Automation backup transport.
set -Eeuo pipefail
umask 077

readonly secret_parent="/etc/edsys-secrets"
readonly secret_dir="${secret_parent}/edcore-automation-backup"
readonly identity_file="${secret_dir}/id_ed25519"
readonly public_file="${identity_file}.pub"
readonly known_hosts_file="${secret_dir}/known_hosts"
readonly host_key_alias="edcore-automation-backup"
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

fail() {
  echo "FAIL: $*" >&2
  exit 1
}

usage() {
  echo "Usage: $0 /root/path/to/console-verified-ssh_host_ed25519_key.pub" >&2
  exit 2
}

require_root_controlled_file() {
  local path="$1" owner mode
  [[ -f "$path" && ! -L "$path" ]] || fail "trusted public-key input is not a regular file: $path"
  owner="$(stat -c '%u:%g' -- "$path")"
  mode="$(stat -c '%a' -- "$path")"
  [[ "$owner" == "0:0" ]] || fail "trusted public-key input must be root:root: $path"
  (( (8#$mode & 0022) == 0 )) || fail "trusted public-key input must not be group/world writable: $path"
}

require_root_controlled_directory_chain() {
  local path="$1" current="" component owner mode
  local -a components=()
  IFS='/' read -r -a components <<<"${path#/}"
  for component in "${components[@]}"; do
    [[ -n "$component" ]] || continue
    current="${current}/${component}"
    [[ -d "$current" && ! -L "$current" ]] || fail "required path component is unsafe: $current"
    owner="$(stat -c '%u:%g' -- "$current")"
    mode="$(stat -c '%a' -- "$current")"
    [[ "$owner" == "0:0" ]] || fail "required path component must be root:root: $current"
    (( (8#$mode & 0022) == 0 )) || fail "required path component must not be group/world writable: $current"
  done
}

[[ ${EUID} -eq 0 ]] || fail "run as root on 9950x"
[[ $# -eq 1 ]] || usage
readonly host_public_key="$1"
[[ "$host_public_key" == /* ]] || fail "host public-key path must be absolute"
for command in awk chmod chown dirname install mv rm ssh-keygen stat; do
  command -v "$command" >/dev/null || fail "required command is missing: $command"
done

require_root_controlled_directory_chain "$script_dir"
require_root_controlled_directory_chain "$(dirname "$host_public_key")"
require_root_controlled_file "$script_dir/provision-backup-client.sh"
require_root_controlled_file "$host_public_key"
mapfile -t host_key_lines < <(awk 'NF { print }' "$host_public_key")
[[ ${#host_key_lines[@]} -eq 1 ]] || fail "host public-key input must contain exactly one nonblank record"
read -r host_key_type host_key_blob _ <<<"${host_key_lines[0]}"
[[ "$host_key_type" == "ssh-ed25519" && -n "$host_key_blob" ]] || \
  fail "host public-key input must contain one ED25519 public key"
ssh-keygen -lf "$host_public_key" -E sha256 >/dev/null || fail "host public-key input is invalid"

if [[ -e "$secret_parent" ]]; then
  [[ -d "$secret_parent" && ! -L "$secret_parent" ]] || fail "$secret_parent is not a real directory"
  [[ "$(stat -c '%u:%g' -- "$secret_parent")" == "0:0" ]] || fail "$secret_parent must be root:root"
  parent_mode="$(stat -c '%a' -- "$secret_parent")"
  (( (8#$parent_mode & 0022) == 0 )) || fail "$secret_parent must not be group/world writable"
else
  install -d -o root -g root -m 0750 "$secret_parent"
fi
if [[ -e "$secret_dir" ]]; then
  [[ -d "$secret_dir" && ! -L "$secret_dir" ]] || fail "$secret_dir is not a real directory"
fi
install -d -o root -g root -m 0700 "$secret_dir"
[[ "$(stat -c '%u:%g:%a' -- "$secret_dir")" == "0:0:700" ]] || \
  fail "$secret_dir must be root:root mode 0700"

if [[ -e "$identity_file" || -e "$public_file" ]]; then
  [[ -f "$identity_file" && ! -L "$identity_file" ]] || fail "dedicated private key is incomplete or unsafe"
  [[ -f "$public_file" && ! -L "$public_file" ]] || fail "dedicated public key is incomplete or unsafe"
else
  ssh-keygen -q -t ed25519 -a 100 -N '' -C 'edsys-edcore-automation-backup' -f "$identity_file"
fi
chown root:root "$identity_file" "$public_file"
chmod 0600 "$identity_file" "$public_file"
derived_public="$(ssh-keygen -y -f "$identity_file" | awk 'NR == 1 { print $1 " " $2 }')"
recorded_public="$(awk 'NR == 1 { print $1 " " $2 }' "$public_file")"
[[ "$derived_public" == "$recorded_public" ]] || fail "dedicated public/private key pair does not match"

known_hosts_tmp="${secret_dir}/.known-hosts.$$"
trap 'rm -f -- "$known_hosts_tmp"' EXIT
printf '%s %s %s\n' "$host_key_alias" "$host_key_type" "$host_key_blob" >"$known_hosts_tmp"
chown root:root "$known_hosts_tmp"
chmod 0600 "$known_hosts_tmp"
mv -fT -- "$known_hosts_tmp" "$known_hosts_file"
ssh-keygen -F "$host_key_alias" -f "$known_hosts_file" >/dev/null || fail "host-key pin could not be read back"

printf 'PASS dedicated backup client created; transfer only %s to the guest through a trusted channel\n' \
  "$public_file"
