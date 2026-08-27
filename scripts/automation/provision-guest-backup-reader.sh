#!/usr/bin/env bash
# Provision the EdCore Automation guest's forced-command, read-only backup account.
set -Eeuo pipefail
umask 077

readonly account="edsys-backup"
readonly account_home="/var/lib/edsys-backup"
readonly install_root="/usr/local/libexec"
readonly exporter="${install_root}/edsys-edcore-automation-backup-export"
readonly launcher="${install_root}/edsys-edcore-automation-backup-ssh"
readonly verifier="${install_root}/edsys-edcore-automation-backup-verify"
readonly sudoers_file="/etc/sudoers.d/edsys-edcore-automation-backup"
readonly sshd_dropin="/etc/ssh/sshd_config.d/05-edsys-backup-reader.conf"
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
sudoers_tmp=""
authorized_tmp=""
sshd_candidate=""
sshd_backup=""
sshd_policy_mutated=0
sshd_policy_had_previous=0

fail() {
  echo "FAIL: $*" >&2
  exit 1
}

cleanup() {
  local status=$?
  trap - EXIT
  [[ -z "$sudoers_tmp" ]] || rm -f -- "$sudoers_tmp"
  [[ -z "$authorized_tmp" ]] || rm -f -- "$authorized_tmp"
  [[ -z "$sshd_candidate" ]] || rm -f -- "$sshd_candidate"
  if (( status != 0 && sshd_policy_mutated == 1 )); then
    if (( sshd_policy_had_previous == 1 )); then
      cp -a -- "$sshd_backup" "$sshd_dropin" || true
    else
      rm -f -- "$sshd_dropin" || true
    fi
    sshd -t && systemctl reload ssh || \
      echo "WARNING: SSH policy rollback needs console review before opening a new session." >&2
  fi
  [[ -z "$sshd_backup" ]] || rm -f -- "$sshd_backup"
  exit "$status"
}
trap cleanup EXIT

usage() {
  echo "Usage: $0 /root/path/to/9950x-dedicated-id_ed25519.pub" >&2
  exit 2
}

require_root_controlled_file() {
  local path="$1" owner mode
  [[ -f "$path" && ! -L "$path" ]] || fail "required input is not a regular file: $path"
  owner="$(stat -c '%u:%g' -- "$path")"
  mode="$(stat -c '%a' -- "$path")"
  [[ "$owner" == "0:0" ]] || fail "required input must be root:root: $path"
  (( (8#$mode & 0022) == 0 )) || fail "required input must not be group/world writable: $path"
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

[[ ${EUID} -eq 0 ]] || fail "run as root on edcore-automation"
[[ $# -eq 1 ]] || usage
readonly client_public_key="$1"
[[ "$client_public_key" == /* ]] || fail "client public-key path must be absolute"
for command in awk cat chmod chown cp dirname getent grep id install mv passwd rm sort \
  sshd ssh-keygen stat systemctl useradd visudo; do
  command -v "$command" >/dev/null || fail "required command is missing: $command"
done

require_root_controlled_directory_chain "$script_dir"
require_root_controlled_directory_chain "$(dirname "$client_public_key")"
for source in \
  "$script_dir/provision-guest-backup-reader.sh" \
  "$script_dir/guest-backup-export.py" \
  "$script_dir/guest-backup-ssh.sh" \
  "$script_dir/verify-backup.py" \
  "$client_public_key"; do
  require_root_controlled_file "$source"
done
mapfile -t public_key_lines < <(awk 'NF { print }' "$client_public_key")
[[ ${#public_key_lines[@]} -eq 1 ]] || fail "client public key must contain exactly one nonblank record"
read -r key_type key_blob _ <<<"${public_key_lines[0]}"
[[ "$key_type" == "ssh-ed25519" && -n "$key_blob" ]] || fail "client key must be one ED25519 public key"
ssh-keygen -lf "$client_public_key" -E sha256 >/dev/null || fail "client public key is invalid"

if getent passwd "$account" >/dev/null; then
  IFS=: read -r existing_name _ existing_uid _ _ existing_home existing_shell \
    < <(getent passwd "$account")
  [[ "$existing_name" == "$account" && "$existing_uid" != 0 ]] || fail "existing backup account is unsafe"
  [[ "$existing_home" == "$account_home" && "$existing_shell" == "/bin/sh" ]] || \
    fail "existing backup account has an unexpected home or shell"
else
  useradd --system --user-group --home-dir "$account_home" --shell /bin/sh "$account"
fi
passwd --lock "$account" >/dev/null
primary_gid="$(id -g "$account")"
for member_gid in $(id -G "$account"); do
  [[ "$member_gid" == "$primary_gid" ]] || fail "backup account must not have supplementary groups"
done

require_root_controlled_directory_chain "$(dirname "$install_root")"
if [[ -e "$install_root" ]]; then
  [[ -d "$install_root" && ! -L "$install_root" ]] || fail "install root is unsafe: $install_root"
fi
install -d -o root -g root -m 0755 "$install_root"
require_root_controlled_directory_chain "$install_root"
for destination in "$exporter" "$launcher" "$verifier"; do
  [[ ! -L "$destination" ]] || fail "refusing linked install target: $destination"
done
install -o root -g root -m 0755 "$script_dir/guest-backup-export.py" "$exporter"
install -o root -g root -m 0755 "$script_dir/guest-backup-ssh.sh" "$launcher"
install -o root -g root -m 0755 "$script_dir/verify-backup.py" "$verifier"
for destination in "$exporter" "$launcher" "$verifier"; do
  [[ "$(stat -c '%u:%g:%a' -- "$destination")" == "0:0:755" ]] || \
    fail "installed backup reader program has an unsafe owner or mode: $destination"
done

require_root_controlled_directory_chain "$(dirname "$account_home")"
if [[ -e "$account_home" ]]; then
  [[ -d "$account_home" && ! -L "$account_home" ]] || fail "backup account home is unsafe"
fi
install -d -o root -g root -m 0755 "$account_home"
if [[ -e "$account_home/.ssh" ]]; then
  [[ -d "$account_home/.ssh" && ! -L "$account_home/.ssh" ]] || fail "backup account SSH directory is unsafe"
fi
install -d -o root -g "$primary_gid" -m 0750 "$account_home/.ssh"
[[ "$(stat -c '%u:%g:%a' -- "$account_home/.ssh")" == "0:${primary_gid}:750" ]] || \
  fail "backup account SSH directory has an unsafe owner or mode"
authorized_tmp="${account_home}/.ssh/.authorized_keys.$$"
printf 'restrict,no-user-rc,command="%s" %s %s edsys-edcore-automation-backup\n' \
  "$launcher" "$key_type" "$key_blob" >"$authorized_tmp"
chown root:"$primary_gid" "$authorized_tmp"
chmod 0640 "$authorized_tmp"
mv -fT -- "$authorized_tmp" "$account_home/.ssh/authorized_keys"
authorized_tmp=""

require_root_controlled_directory_chain "$(dirname "$sudoers_file")"
sudoers_tmp="/etc/sudoers.d/.edsys-edcore-automation-backup.$$"
printf '%s ALL=(root) NOPASSWD: %s *\n' "$account" "$exporter" >"$sudoers_tmp"
chown root:root "$sudoers_tmp"
chmod 0440 "$sudoers_tmp"
visudo -cf "$sudoers_tmp" >/dev/null
[[ ! -L "$sudoers_file" ]] || fail "refusing linked sudoers target"
mv -fT -- "$sudoers_tmp" "$sudoers_file"
sudoers_tmp=""
[[ "$(stat -c '%u:%g:%a' -- "$account_home/.ssh/authorized_keys")" == "0:${primary_gid}:640" ]] || \
  fail "installed authorized_keys has an unsafe owner or mode"
[[ "$(stat -c '%u:%g:%a' -- "$sudoers_file")" == "0:0:440" ]] || \
  fail "installed sudoers fragment has an unsafe owner or mode"

# Preserve the existing AllowUsers boundary while adding only the dedicated
# reader. The Match block independently enforces public-key-only forced access.
require_root_controlled_directory_chain "$(dirname "$sshd_dropin")"
[[ ! -L "$sshd_dropin" ]] || fail "refusing linked sshd drop-in target"
sshd_candidate="$(dirname "$sshd_dropin")/.05-edsys-backup-reader.$$.conf"
cat >"$sshd_candidate" <<EOF
# Managed by provision-guest-backup-reader.sh.
AllowUsers jeremy edsys-backup

Match User edsys-backup
    AuthenticationMethods publickey
    PubkeyAuthentication yes
    PasswordAuthentication no
    KbdInteractiveAuthentication no
    PermitTTY no
    DisableForwarding yes
    AllowAgentForwarding no
    AllowTcpForwarding no
    X11Forwarding no
    PermitTunnel no
    PermitOpen none
    PermitListen none
    PermitUserRC no
    MaxSessions 1
    ForceCommand $launcher

Match all
EOF
chown root:root "$sshd_candidate"
chmod 0644 "$sshd_candidate"
sshd -t -f "$sshd_candidate"
if [[ -e "$sshd_dropin" ]]; then
  [[ -f "$sshd_dropin" && ! -L "$sshd_dropin" ]] || fail "existing sshd drop-in is unsafe"
  sshd_backup="/run/edsys-backup-reader-sshd.$$.before"
  cp -a -- "$sshd_dropin" "$sshd_backup"
  chmod 0600 "$sshd_backup"
  sshd_policy_had_previous=1
fi
mv -fT -- "$sshd_candidate" "$sshd_dropin"
sshd_candidate=""
sshd_policy_mutated=1
sshd -t
effective_sshd="$(sshd -T -C user=edsys-backup,host=edcore-automation,addr=192.168.50.50)"
mapfile -t effective_allow_users < <(
  awk '$1 == "allowusers" { for (i = 2; i <= NF; i++) print $i }' <<<"$effective_sshd" | sort -u
)
[[ ${#effective_allow_users[@]} -eq 2 && \
   "${effective_allow_users[0]}" == "edsys-backup" && \
   "${effective_allow_users[1]}" == "jeremy" ]] || \
  fail "effective AllowUsers is broader or narrower than exactly jeremy and edsys-backup"
for required_setting in \
  'authenticationmethods publickey' \
  'pubkeyauthentication yes' \
  'passwordauthentication no' \
  'kbdinteractiveauthentication no' \
  'permittty no' \
  'disableforwarding yes' \
  'allowagentforwarding no' \
  'allowtcpforwarding no' \
  'x11forwarding no' \
  'permittunnel no' \
  'permituserrc no' \
  'maxsessions 1' \
  "forcecommand $launcher"; do
  grep -Fqx -- "$required_setting" <<<"$effective_sshd" || \
    fail "effective edsys-backup sshd policy is missing: $required_setting"
done
systemctl reload ssh
sshd_policy_mutated=0
[[ -z "$sshd_backup" ]] || rm -f -- "$sshd_backup"
sshd_backup=""

printf 'PASS forced-command backup reader installed for %s; no shell, PTY, forwarding, or general sudo is authorized\n' \
  "$account"
