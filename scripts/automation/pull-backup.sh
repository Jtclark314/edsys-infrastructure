#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
verify_script="${script_dir}/verify-backup.py"
extract_script="${script_dir}/extract-backup.py"
readonly automation_ip="192.168.50.82"
readonly remote_user="edsys-backup"
readonly remote="${remote_user}@${automation_ip}"
readonly host_key_alias="edcore-automation-backup"
readonly secret_root="/etc/edsys-secrets/edcore-automation-backup"
readonly identity_file="${secret_root}/id_ed25519"
readonly known_hosts_file="${secret_root}/known_hosts"
destination_root="${AUTOMATION_BACKUP_DESTINATION:-/srv/edsys-backup/staging/edcore-automation}"
retention_days="${AUTOMATION_BACKUP_RETENTION_DAYS:-35}"
staging=""
current_tmp=""
incoming_archive=""

fail() {
  echo "FAIL: $*" >&2
  exit 1
}

require_no_symlink_components() {
  local path="$1"
  local current=""
  local component
  local -a components=()
  IFS='/' read -r -a components <<<"${path#/}"
  for component in "${components[@]}"; do
    [[ -n "$component" ]] || continue
    current="${current}/${component}"
    [[ ! -L "$current" ]] || fail "path contains a symlink component: $current"
  done
}

require_root_owned_directory_chain() {
  local path="$1"
  local current=""
  local component owner mode
  local -a components=()
  IFS='/' read -r -a components <<<"${path#/}"
  for component in "${components[@]}"; do
    [[ -n "$component" ]] || continue
    current="${current}/${component}"
    [[ -d "$current" && ! -L "$current" ]] || fail "required path component is not a safe directory: $current"
    owner="$(stat -c '%u:%g' -- "$current")"
    mode="$(stat -c '%a' -- "$current")"
    [[ "$owner" == "0:0" ]] || fail "required path component must be owned by root:root: $current"
    (( (8#$mode & 0022) == 0 )) || fail "required path component must not be group/world writable: $current"
  done
}

require_root_private_file() {
  local path="$1"
  local owner mode
  require_no_symlink_components "$path"
  [[ -f "$path" && ! -L "$path" ]] || fail "required private file is unavailable or unsafe: $path"
  owner="$(stat -c '%u:%g' -- "$path")"
  mode="$(stat -c '%a' -- "$path")"
  [[ "$owner" == "0:0" ]] || fail "private file must be owned by root:root: $path"
  [[ "$mode" == "600" ]] || fail "private file must have mode 0600: $path"
}

cleanup() {
  local status=$?
  if [[ -n "$staging" && -d "$staging" ]]; then
    rm -rf -- "$staging"
  fi
  if [[ -n "$current_tmp" && -L "$current_tmp" ]]; then
    rm -f -- "$current_tmp"
  fi
  if [[ -n "$incoming_archive" && -f "$incoming_archive" && ! -L "$incoming_archive" ]]; then
    rm -f -- "$incoming_archive"
  fi
  if (( status == 0 )); then
    ping_healthchecks success
  else
    ping_healthchecks fail
  fi
  exit "$status"
}
trap cleanup EXIT

ping_healthchecks() {
  [[ -n "${HC_PING_URL:-}" ]] || return 0
  local status="$1"
  local suffix=""
  local timeout="${HC_TIMEOUT_SECONDS:-10}"
  local url escaped_url
  [[ "$status" == success ]] || suffix="/${status}"
  [[ "$timeout" =~ ^[1-9][0-9]{0,2}$ ]] || timeout=10
  url="${HC_PING_URL}${suffix}"
  [[ "$url" != *$'\n'* && "$url" != *$'\r'* ]] || return 0
  escaped_url="${url//\\/\\\\}"
  escaped_url="${escaped_url//\"/\\\"}"
  printf 'url = "%s"\nmax-time = %s\nproto = "=http,https"\nnoproxy = "*"\nfail\nsilent\n' \
    "$escaped_url" "$timeout" | curl -q --config - >/dev/null 2>&1 || true
}

[[ ${EUID} -eq 0 ]] || fail "run as root on 9950x"
[[ -x "$verify_script" ]] || fail "backup verifier is unavailable: $verify_script"
[[ -x "$extract_script" ]] || fail "safe archive extractor is unavailable: $extract_script"
[[ "$destination_root" =~ ^/[A-Za-z0-9._/-]+$ && "$destination_root" != *".."* ]] || fail "invalid destination root"
[[ "$retention_days" =~ ^[1-9][0-9]{0,3}$ ]] || fail "retention days must be between 1 and 9999"

for command in awk chmod chown cmp curl find install ln mv python3 rm ssh ssh-keygen stat xargs; do
  command -v "$command" >/dev/null || fail "required command is missing: $command"
done

ping_healthchecks start

require_root_owned_directory_chain "$secret_root"
require_root_private_file "$identity_file"
require_root_private_file "$known_hosts_file"
ssh-keygen -y -f "$identity_file" >/dev/null 2>&1 || fail "dedicated backup SSH private key is invalid"
host_key_records="$(awk -v alias="$host_key_alias" '
  /^[[:space:]]*(#|$)/ { next }
  $1 == alias && $2 == "ssh-ed25519" && NF == 3 { matches += 1; next }
  { unexpected += 1 }
  END {
    if (matches == 1 && unexpected == 0) print matches
  }
' "$known_hosts_file")"
[[ "$host_key_records" == "1" ]] || fail "dedicated known_hosts must contain exactly one pinned ED25519 host key"
ssh-keygen -F "$host_key_alias" -f "$known_hosts_file" >/dev/null 2>&1 || \
  fail "pinned automation host key is unavailable"

ssh_options=(
  -4
  -p 22
  -F /dev/null
  -o IdentityFile=none
  -i "$identity_file"
  -o CertificateFile=none
  -o IdentitiesOnly=yes
  -o IdentityAgent=none
  -o HostbasedAuthentication=no
  -o GSSAPIAuthentication=no
  -o HostKeyAlgorithms=ssh-ed25519
  -o PubkeyAcceptedAlgorithms=ssh-ed25519
  -o ControlMaster=no
  -o ControlPath=none
  -o ClearAllForwardings=yes
  -o PermitLocalCommand=no
  -o ProxyCommand=none
  -o ProxyJump=none
  -o RemoteCommand=none
  -o RequestTTY=no
  -o StdinNull=yes
  -o CanonicalizeHostname=no
  -o UpdateHostKeys=no
  -o "UserKnownHostsFile=${known_hosts_file}"
  -o GlobalKnownHostsFile=/dev/null
  -o "HostKeyAlias=${host_key_alias}"
  -o CheckHostIP=no
  -o StrictHostKeyChecking=yes
  -o PasswordAuthentication=no
  -o KbdInteractiveAuthentication=no
  -o PreferredAuthentications=publickey
  -o NumberOfPasswordPrompts=0
  -o BatchMode=yes
  -o ConnectTimeout=10
  -o ServerAliveInterval=15
  -o ServerAliveCountMax=4
)

run_id="$(ssh "${ssh_options[@]}" "$remote" edsys-backup-current)"
[[ "$run_id" =~ ^20[0-9]{6}T[0-9]{6}Z$ ]] || fail "remote returned an invalid backup identifier"

require_no_symlink_components "$destination_root"
install -d -o root -g root -m 0750 "$destination_root"
[[ -d "$destination_root" && ! -L "$destination_root" ]] || fail "destination root is not a safe directory"
destination_owner="$(stat -c '%u:%g' "$destination_root")"
destination_mode="$(stat -c '%a' "$destination_root")"
[[ "$destination_owner" == "0:0" ]] || fail "destination root must be owned by root:root"
(( (8#$destination_mode & 0022) == 0 )) || fail "destination root must not be group/world writable"

staging="${destination_root}/.staging-${run_id}-$$"
final="${destination_root}/${run_id}"
incoming_archive="${destination_root}/.incoming-${run_id}-$$.tar"
[[ ! -e "$staging" && ! -L "$staging" ]] || fail "staging path already exists"
[[ ! -e "$incoming_archive" && ! -L "$incoming_archive" ]] || fail "incoming archive path already exists"
install -d -o root -g root -m 0700 "$staging"
install -o root -g root -m 0600 /dev/null "$incoming_archive"

# The forced-command protocol requires one remote command string. run_id is
# constrained above to the exact timestamp-only grammar before interpolation.
# shellcheck disable=SC2029
ssh "${ssh_options[@]}" "$remote" "edsys-backup-export ${run_id}" >"$incoming_archive"
[[ -s "$incoming_archive" ]] || fail "forced-command backup export returned an empty archive"
python3 "$extract_script" "$incoming_archive" "$staging"
rm -f -- "$incoming_archive"
incoming_archive=""

python3 "$verify_script" "$staging" --expected-run-id "$run_id"
chown -R root:root "$staging"
chmod -R go-rwx "$staging"

if [[ -e "$final" || -L "$final" ]]; then
  [[ -d "$final" && ! -L "$final" ]] || fail "existing final path is unsafe"
  python3 "$verify_script" "$final" --expected-run-id "$run_id" >/dev/null
  cmp -s -- "$staging/SHA256SUMS" "$final/SHA256SUMS" || \
    fail "remote backup changed after an already accepted run ID"
  rm -rf -- "$staging"
  staging=""
else
  mv -T -- "$staging" "$final"
  staging=""
fi
chown -R root:root "$final"
chmod -R go-rwx "$final"

if [[ -e "${destination_root}/current" && ! -L "${destination_root}/current" ]]; then
  fail "current pointer exists but is not a symlink"
fi
current_tmp="${destination_root}/.current-${run_id}-$$"
rm -f -- "$current_tmp"
ln -s -- "$run_id" "$current_tmp"
mv -Tf -- "$current_tmp" "${destination_root}/current"

find "$destination_root" -mindepth 1 -maxdepth 1 -type d \
  -name '20??????T??????Z' ! -name "$run_id" -mtime "+${retention_days}" -print0 \
  | xargs -0r --no-run-if-empty rm -rf --

printf 'Verified EdCore Automation backup pull: %s\n' "$final"
