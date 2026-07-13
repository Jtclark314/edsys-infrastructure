#!/usr/bin/env bash
set -Eeuo pipefail

[[ "${EUID}" -eq 0 ]] || { echo "Run with sudo" >&2; exit 2; }
for lock_command in chown flock install mkdir stat; do
  command -v "${lock_command}" >/dev/null \
    || { echo "Missing required command: ${lock_command}" >&2; exit 1; }
done
LOCK_DIR=/run/lock/edsys-share
LOCK_FILE=${LOCK_DIR}/install.lock
if mkdir -m 0700 "${LOCK_DIR}" 2>/dev/null; then
  chown root:root "${LOCK_DIR}"
else
  [[ -d "${LOCK_DIR}" && ! -L "${LOCK_DIR}" ]] \
    || { echo "Unsafe EdSys Share lock directory" >&2; exit 1; }
  read -r lock_dir_uid lock_dir_gid lock_dir_mode \
    < <(stat -c '%u %g %a' "${LOCK_DIR}")
  [[ "${lock_dir_uid}" == 0 && "${lock_dir_gid}" == 0 \
    && "${lock_dir_mode}" == 700 ]] \
    || { echo "Unexpected EdSys Share lock-directory ownership/mode" >&2; exit 1; }
fi
if [[ ! -e "${LOCK_FILE}" && ! -L "${LOCK_FILE}" ]]; then
  install -m 0600 -o root -g root /dev/null "${LOCK_FILE}"
fi
[[ -f "${LOCK_FILE}" && ! -L "${LOCK_FILE}" ]] \
  || { echo "Unsafe EdSys Share lock file" >&2; exit 1; }
read -r lock_uid lock_gid lock_mode < <(stat -c '%u %g %a' "${LOCK_FILE}")
[[ "${lock_uid}" == 0 && "${lock_gid}" == 0 && "${lock_mode}" == 600 ]] \
  || { echo "Unexpected EdSys Share lock-file ownership/mode" >&2; exit 1; }
exec 9<>"${LOCK_FILE}"
flock -n 9 || { echo "Another EdSys Share installation is running" >&2; exit 1; }
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENABLE_BACKUP=false
[[ "${1:-}" == "--enable-backup" ]] && ENABLE_BACKUP=true
[[ $# -eq 0 || "${1:-}" == "--enable-backup" ]] || { echo "Usage: sudo $0 [--enable-backup]" >&2; exit 2; }

# Fail closed before service/config mutation if any retired application runtime
# identity has reappeared. Generic OS facilities are independent; app-specific
# identities must never carry forward into EdSys Share.
if getent passwd courier-reader edsys-courier >/dev/null \
  || systemctl list-unit-files --no-legend 2>/dev/null | grep -Eqi '(^|[[:space:]])edsys-courier|courier-reader' \
  || ss -lntH 'sport = :3045' | grep -q . \
  || testparm -s 2>/dev/null | grep -Fqi '[CourierMedia]' \
  || tailscale serve status 2>/dev/null | grep -Eqi 'courier|:3045' \
  || docker ps -a --format '{{.Names}} {{.Image}}' 2>/dev/null | grep -Eqi 'edsys-courier|courier-reader' \
  || docker images --format '{{.Repository}}:{{.Tag}}' 2>/dev/null | grep -Eqi 'edsys-courier|courier-reader'; then
  echo "Retired Courier runtime remnant detected; refusing EdSys Share installation" >&2
  exit 1
fi
for retired_path in \
  /opt/edsys-courier /srv/edsys-courier /var/lib/edsys-courier \
  /home/jeremy/code/edsys-courier /home/jeremy/code/.worktrees/*courier*; do
  [[ ! -e "${retired_path}" ]] || {
    echo "Retired Courier path detected: ${retired_path}; refusing installation" >&2
    exit 1
  }
done

CONFIG_DIR=/etc/edsys-share
RUNTIME_CONFIG_FILE=${CONFIG_DIR}/edsys-share.conf
install_runtime_config=false
if [[ -e "${RUNTIME_CONFIG_FILE}" ]]; then
  CONFIG_FILE=${RUNTIME_CONFIG_FILE}
else
  CONFIG_FILE=${SCRIPT_DIR}/edsys-share.conf.example
  install_runtime_config=true
fi
# shellcheck source=/dev/null
source "${CONFIG_FILE}"
: "${EDSYS_SHARE_RESTORE_DIR:=/mnt/ai-store/edsys-share-restore-staging}"
RESTRICTED_SMB_USERS=(edsys-share-nimo edsys-share-dell)

[[ "${EDSYS_SHARE_TARGET}" == /EdSys-Share ]] || { echo "Unexpected share target" >&2; exit 2; }
[[ "${EDSYS_SHARE_SOURCE}" == /mnt/ai-store/* ]] || { echo "Share source must be under AI Store" >&2; exit 2; }
ip -4 addr show tailscale0 | grep -q "inet ${EDSYS_SHARE_TAILNET_LISTEN_IP}/" || {
  echo "Configured Tailnet listen address is not present on tailscale0" >&2
  exit 2
}
for required_command in \
  apparmor_parser cc getent id install mount mountpoint passwd pdbedit \
  nmbd python3 smbd sudo systemctl testparm useradd usermod; do
  command -v "${required_command}" >/dev/null \
    || { echo "Missing required command: ${required_command}" >&2; exit 1; }
done

validate_samba_launch_contract() {
  local default_file=/etc/default/samba
  local daemon environment exec_start option_variable

  for daemon in smbd nmbd; do
    if [[ "${daemon}" == smbd ]]; then
      option_variable=SMBDOPTIONS
    else
      option_variable=NMBDOPTIONS
    fi
    [[ "$(systemctl show -p FragmentPath --value "${daemon}.service")" == \
      "/usr/lib/systemd/system/${daemon}.service" ]] || {
      echo "${daemon} does not use the reviewed vendor unit" >&2
      return 1
    }
    [[ -z "$(systemctl show -p DropInPaths --value "${daemon}.service")" ]] || {
      echo "${daemon} unit drop-ins require explicit review" >&2
      return 1
    }
    [[ "$(systemctl show -p EnvironmentFiles --value "${daemon}.service")" == \
      '/etc/default/samba (ignore_errors=yes)' ]] || {
      echo "${daemon} uses an unexpected environment file" >&2
      return 1
    }

    environment="$(systemctl show -p Environment --value "${daemon}.service")"
    [[ "${environment}" != *"${option_variable}="* ]] || {
      echo "${daemon} has a manager-level ${option_variable} override" >&2
      return 1
    }

    exec_start="$(systemctl show -p ExecStart --value "${daemon}.service")"
    python3 - "${exec_start}" "${daemon}" "${option_variable}" <<'PY'
import re
import sys

value, daemon, option_variable = sys.argv[1:]
pattern = re.compile(
    rf"^\{{ path=/usr/sbin/{daemon} ; "
    rf"argv\[\]=/usr/sbin/{daemon} --foreground --no-process-group "
    rf"\${option_variable} ; ignore_errors=no ; .+ \}}$"
)
if pattern.fullmatch(value) is None:
    raise SystemExit(f"{daemon} ExecStart does not match the reviewed launch contract")
PY
  done

  if [[ -e "${default_file}" || -L "${default_file}" ]]; then
    [[ -f "${default_file}" && ! -L "${default_file}" ]] || {
      echo "Unsafe ${default_file}" >&2
      return 1
    }
    read -r default_uid default_gid default_mode \
      < <(stat -c '%u %g %a' "${default_file}")
    if ! [[ "${default_uid}" == 0 && "${default_gid}" == 0 ]] \
      || (( (8#${default_mode} & 8#022) != 0 )); then
      echo "Unexpected ${default_file} ownership/mode" >&2
      return 1
    fi
    python3 - "${default_file}" <<'PY'
from pathlib import Path
import re
import sys

for number, line in enumerate(Path(sys.argv[1]).read_text().splitlines(), start=1):
    stripped = line.strip()
    if not stripped or stripped.startswith(("#", ";")):
        continue
    option_names = ("SMBDOPTIONS", "NMBDOPTIONS")
    if any(name in stripped for name in option_names) and re.fullmatch(
        r"(?:SMBDOPTIONS|NMBDOPTIONS)\s*=", stripped
    ) is None:
        raise SystemExit(f"Unsafe Samba daemon options at {sys.argv[1]}:{number}")
PY
  fi

  [[ "$(smbd -b | awk '$1 == "CONFIGFILE:" { print $2 }')" == \
    /etc/samba/smb.conf ]] || {
    echo "smbd was compiled for an unexpected default configuration" >&2
    return 1
  }
}

validate_samba_process_contract() {
  local daemon main_pid
  for daemon in smbd nmbd; do
    main_pid="$(systemctl show -p MainPID --value "${daemon}.service")"
    [[ "${main_pid}" =~ ^[1-9][0-9]*$ ]] || {
      echo "${daemon} has no running main process" >&2
      return 1
    }
    python3 - "${main_pid}" "${daemon}" <<'PY'
from pathlib import Path
import sys

argv = Path(f"/proc/{sys.argv[1]}/cmdline").read_bytes().split(b"\0")
if argv and argv[-1] == b"":
    argv.pop()
daemon = sys.argv[2].encode()
expected = [b"/usr/sbin/" + daemon, b"--foreground", b"--no-process-group"]
if argv != expected:
    raise SystemExit(f"Unexpected live {sys.argv[2]} argv: {argv!r}")
PY
  done
}

validate_samba_launch_contract

# Prove the complete Samba candidate before mutating accounts, mounts, AppArmor,
# services, or runtime configuration. Dynamic/alternate config sources are
# rejected by the renderer so the restricted identity cannot bypass per-share
# denial rules.
candidate="$(mktemp)"
samba_stage=""
trap 'rm -f "${candidate:-}" "${samba_stage:-}"' EXIT
renderer_restricted_args=()
for restricted_smb_user in "${RESTRICTED_SMB_USERS[@]}"; do
  renderer_restricted_args+=(--restricted-user "${restricted_smb_user}")
done
python3 "${SCRIPT_DIR}/render-samba-config.py" \
  /etc/samba/smb.conf "${SCRIPT_DIR}/samba-share.conf" "${candidate}" \
  "${renderer_restricted_args[@]}"
testparm -s "${candidate}" >/dev/null

if [[ "${install_runtime_config}" == true ]]; then
  install -d -m 0750 -o root -g root "${CONFIG_DIR}"
  install -m 0640 -o root -g root "${CONFIG_FILE}" "${RUNTIME_CONFIG_FILE}"
fi

declare -A restricted_smb_states=()
for restricted_smb_user in "${RESTRICTED_SMB_USERS[@]}"; do
  if ! getent passwd "${restricted_smb_user}" >/dev/null; then
    useradd --system --user-group --no-create-home --home-dir /nonexistent \
      --shell /usr/sbin/nologin "${restricted_smb_user}"
    usermod --lock "${restricted_smb_user}"
  fi
  IFS=: read -r _ _ restricted_uid restricted_gid _ restricted_home restricted_shell \
    < <(getent passwd "${restricted_smb_user}")
  [[ "${restricted_uid}" -gt 0 && "${restricted_uid}" -lt 1000 \
    && "${restricted_home}" == /nonexistent && ! -e "${restricted_home}" \
    && "${restricted_shell}" == /usr/sbin/nologin ]] \
    || { echo "Unexpected ${restricted_smb_user} POSIX identity attributes" >&2; exit 1; }
  [[ "$(passwd -S "${restricted_smb_user}" | awk '{print $2}')" == L ]] \
    || { echo "${restricted_smb_user} POSIX password is not locked" >&2; exit 1; }
  grep -q "^${restricted_smb_user}:" /etc/passwd \
    || { echo "${restricted_smb_user} must be a local POSIX identity" >&2; exit 1; }
  IFS=: read -r restricted_group_name _ restricted_group_gid _ \
    < <(getent group "${restricted_gid}")
  [[ "${restricted_group_name}" == "${restricted_smb_user}" \
    && "${restricted_group_gid}" == "${restricted_gid}" \
    && "$(id -G "${restricted_smb_user}")" == "${restricted_gid}" ]] \
    || { echo "${restricted_smb_user} must have only its dedicated primary group" >&2; exit 1; }
  grep -q "^${restricted_smb_user}:" /etc/group \
    || { echo "${restricted_smb_user} must have a local dedicated group" >&2; exit 1; }
  if sudo -l -U "${restricted_smb_user}" 2>/dev/null | grep -Fq 'may run the following'; then
    echo "${restricted_smb_user} unexpectedly has sudo authorization" >&2
    exit 1
  fi
  restricted_smb_states["${restricted_smb_user}"]=absent
  if pdbedit -L "${restricted_smb_user}" >/dev/null 2>&1; then
    if pdbedit -L -v "${restricted_smb_user}" | grep -Eq '^Account Flags:.*D'; then
      restricted_smb_states["${restricted_smb_user}"]=disabled
    else
      restricted_smb_states["${restricted_smb_user}"]=enabled
    fi
  fi
done

timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
install -d -m 0700 -o root -g root /var/backups/edsys-share
cp -a /etc/fstab "/var/backups/edsys-share/fstab.${timestamp}"
samba_backup="/var/backups/edsys-share/smb.conf.${timestamp}"
cp -a /etc/samba/smb.conf "${samba_backup}"
if [[ -e /etc/apparmor.d/local/usr.sbin.smbd ]]; then
  cp -a /etc/apparmor.d/local/usr.sbin.smbd "/var/backups/edsys-share/apparmor-local-smbd.${timestamp}"
fi
if [[ -e /etc/apparmor.d/edsys-share-smb-preexec ]]; then
  cp -a /etc/apparmor.d/edsys-share-smb-preexec "/var/backups/edsys-share/apparmor-preexec.${timestamp}"
fi

install -d -m 2770 -o jeremy -g jeremy "${EDSYS_SHARE_SOURCE}"
if ! mountpoint -q "${EDSYS_SHARE_TARGET}"; then
  # Keep the bare root-filesystem mountpoint deliberately unwritable. Once the
  # bind mount is active, the visible ownership/mode come from the source.
  install -d -m 0000 -o root -g root "${EDSYS_SHARE_TARGET}"
fi
fstab_line="${EDSYS_SHARE_SOURCE} ${EDSYS_SHARE_TARGET} none bind,x-systemd.requires-mounts-for=/mnt/ai-store 0 0"
python3 - "${fstab_line}" <<'PY'
from pathlib import Path
import os
import sys
import tempfile

path = Path("/etc/fstab")
marker = "# EdSys Share: root-level bind mount backed by AI Store"
wanted = sys.argv[1]
lines = path.read_text().splitlines()
target_indexes = [i for i, line in enumerate(lines) if line.strip() and not line.lstrip().startswith("#") and len(line.split()) >= 2 and line.split()[1] == "/EdSys-Share"]
if len(target_indexes) > 1:
    raise SystemExit("Ambiguous duplicate /EdSys-Share fstab entries")
if target_indexes:
    i = target_indexes[0]
    if i == 0 or lines[i - 1].strip() != marker:
        raise SystemExit("Unmanaged /EdSys-Share fstab entry exists; refusing replacement")
    del lines[i - 1:i + 1]
while lines and not lines[-1].strip():
    lines.pop()
lines.extend(["", marker, wanted])
data = "\n".join(lines) + "\n"
fd, name = tempfile.mkstemp(dir=path.parent, prefix=".fstab.edsys-share.")
try:
    with os.fdopen(fd, "w") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    os.chmod(name, path.stat().st_mode & 0o7777)
    os.chown(name, path.stat().st_uid, path.stat().st_gid)
    os.replace(name, path)
finally:
    if os.path.exists(name):
        os.unlink(name)
PY
systemctl daemon-reload
mountpoint -q "${EDSYS_SHARE_TARGET}" || mount "${EDSYS_SHARE_TARGET}"

install -d -m 0755 -o root -g root /usr/local/libexec/edsys-share
install -m 0755 -o root -g root "${SCRIPT_DIR}/edsys-share-mount-check" /usr/local/libexec/edsys-share/edsys-share-mount-check
cc -std=c11 -O2 -Wall -Wextra -Werror "${SCRIPT_DIR}/edsys-share-mount-check-smb.c" -o /usr/local/libexec/edsys-share/edsys-share-mount-check-smb
chown root:root /usr/local/libexec/edsys-share/edsys-share-mount-check-smb
chmod 0755 /usr/local/libexec/edsys-share/edsys-share-mount-check-smb
install -m 0755 -o root -g root "${SCRIPT_DIR}/edsys-share-tailnet-guard" /usr/local/libexec/edsys-share/edsys-share-tailnet-guard
install -m 0755 -o root -g root "${SCRIPT_DIR}/edsys-share-gdrive-sync" /usr/local/sbin/edsys-share-gdrive-sync
install -m 0755 -o root -g root "${SCRIPT_DIR}/edsys-share-gdrive-verify" /usr/local/sbin/edsys-share-gdrive-verify
install -m 0755 -o root -g root "${SCRIPT_DIR}/edsys-share-gdrive-prune" /usr/local/sbin/edsys-share-gdrive-prune
install -m 0755 -o root -g root "${SCRIPT_DIR}/edsys-share-gdrive-restore" /usr/local/sbin/edsys-share-gdrive-restore
install -d -m 0750 -o root -g root "${EDSYS_SHARE_STATUS_DIR}" "${EDSYS_SHARE_REPORT_DIR}" "${EDSYS_SHARE_CACHE_DIR}" "${EDSYS_SHARE_RESTORE_DIR}"

/usr/local/libexec/edsys-share/edsys-share-mount-check
/usr/local/libexec/edsys-share/edsys-share-mount-check-smb

# Samba's AppArmor profile intentionally blocks arbitrary root preexec shells.
# Transition only the fixed /bin/sh invocation into a narrow profile that may
# execute the compiled, no-shell mount validator and read only its mount paths.
install -m 0644 -o root -g root "${SCRIPT_DIR}/apparmor-edsys-share-smb-preexec" /etc/apparmor.d/edsys-share-smb-preexec
python3 - "${SCRIPT_DIR}/apparmor-local-usr.sbin.smbd" <<'PY'
from pathlib import Path
import re
import sys

path = Path("/etc/apparmor.d/local/usr.sbin.smbd")
fragment = Path(sys.argv[1]).read_text().strip()
text = path.read_text() if path.exists() else ""
text = re.sub(r"(?ms)^# BEGIN EDSYS SHARE MANAGED PREEXEC\n.*?^# END EDSYS SHARE MANAGED PREEXEC\n?", "", text)
path.write_text(text.rstrip() + "\n\n" + fragment + "\n")
PY
apparmor_parser -r /etc/apparmor.d/edsys-share-smb-preexec
apparmor_parser -r /etc/apparmor.d/usr.sbin.smbd

# Atomically install the already preflighted candidate. The share is replaced
# idempotently and Samba remains bound only to loopback plus the real LAN.
samba_stage="$(mktemp /etc/samba/.smb.conf.edsys-share.XXXXXX)"
install -m 0644 -o root -g root "${candidate}" "${samba_stage}"
testparm -s "${samba_stage}" >/dev/null
mv "${samba_stage}" /etc/samba/smb.conf
samba_stage=""
rm -f "${candidate}"
if systemctl restart smbd nmbd \
  && systemctl is-active --quiet smbd.service nmbd.service \
  && validate_samba_process_contract; then
  :
else
  echo "New Samba configuration failed to start; restoring the prior file" >&2
  samba_stage="$(mktemp /etc/samba/.smb.conf.edsys-share-restore.XXXXXX)"
  cp --preserve=mode,ownership "${samba_backup}" "${samba_stage}"
  mv "${samba_stage}" /etc/samba/smb.conf
  samba_stage=""
  if ! systemctl restart smbd nmbd \
    || ! systemctl is-active --quiet smbd.service nmbd.service \
    || ! validate_samba_process_contract; then
    echo "CRITICAL: prior Samba configuration was restored but daemons did not recover" >&2
  fi
  exit 1
fi

install -m 0644 -o root -g root "${SCRIPT_DIR}/systemd/edsys-share-tailnet-guard.service" /etc/systemd/system/edsys-share-tailnet-guard.service
sed "s/@TAILNET_LISTEN_IP@/${EDSYS_SHARE_TAILNET_LISTEN_IP}/g" "${SCRIPT_DIR}/systemd/edsys-share-tailnet-smb.socket.in" \
  | install -m 0644 -o root -g root /dev/stdin /etc/systemd/system/edsys-share-tailnet-smb.socket
install -m 0644 -o root -g root "${SCRIPT_DIR}/systemd/edsys-share-tailnet-smb.service" /etc/systemd/system/edsys-share-tailnet-smb.service

for unit in \
  edsys-share-gdrive-sync.service edsys-share-gdrive-sync.timer \
  edsys-share-gdrive-verify.service edsys-share-gdrive-verify.timer \
  edsys-share-gdrive-verify-checksum.service edsys-share-gdrive-verify-checksum.timer \
  edsys-share-gdrive-prune.service edsys-share-gdrive-prune.timer; do
  install -m 0644 -o root -g root "${SCRIPT_DIR}/systemd/${unit}" "/etc/systemd/system/${unit}"
done

systemctl daemon-reload
systemctl enable --now edsys-share-tailnet-guard.service edsys-share-tailnet-smb.socket

if [[ "${ENABLE_BACKUP}" == true ]]; then
  "${EDSYS_SHARE_RCLONE_BIN}" --config "${EDSYS_SHARE_RCLONE_CONFIG}" about "${EDSYS_SHARE_RCLONE_REMOTE}:" >/dev/null
  core_status=/var/lib/edsys-backup/offsite-status.json
  if [[ ! -r "${core_status}" ]] \
    || ! jq -e '.status == "success" and .mode != "test-only" and (.dry_run // false | not)' "${core_status}" >/dev/null; then
    echo "Core encrypted offsite mirror has no verified successful status" >&2
    exit 1
  fi
  [[ -f "${EDSYS_SHARE_STATUS_DIR}/seed-complete" ]] \
    || { echo "EdSys Share initial seed has not been approved and completed" >&2; exit 1; }
  for marker in "${EDSYS_SHARE_STATUS_DIR}/last-verify-success" "${EDSYS_SHARE_STATUS_DIR}/restore-status.json"; do
    if [[ ! -f "${marker}" ]] || (( $(date +%s) - $(stat -c %Y "${marker}") > 86400 )); then
      echo "Required recent verification/restore evidence is missing: ${marker}" >&2
      exit 1
    fi
  done
  jq -e '.status == "success"' "${EDSYS_SHARE_STATUS_DIR}/restore-status.json" >/dev/null \
    || { echo "Latest staged restore did not verify successfully" >&2; exit 1; }
  systemctl enable --now \
    edsys-share-gdrive-sync.timer \
    edsys-share-gdrive-verify.timer \
    edsys-share-gdrive-verify-checksum.timer \
    edsys-share-gdrive-prune.timer
else
  echo "Drive timers installed but left disabled until OAuth, dry-run, seed, and restore checks pass."
fi

echo "EdSys Share host installation completed."
for restricted_smb_user in "${RESTRICTED_SMB_USERS[@]}"; do
  echo "${restricted_smb_user} Samba identity is ${restricted_smb_states[${restricted_smb_user}]}."
  if [[ "${restricted_smb_states[${restricted_smb_user}]}" != enabled ]]; then
    echo "Onboarding stays disabled until an operator runs: sudo smbpasswd -a ${restricted_smb_user}"
  fi
done
