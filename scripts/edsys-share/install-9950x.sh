#!/usr/bin/env bash
set -Eeuo pipefail

[[ "${EUID}" -eq 0 ]] || { echo "Run with sudo" >&2; exit 2; }
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENABLE_BACKUP=false
[[ "${1:-}" == "--enable-backup" ]] && ENABLE_BACKUP=true
[[ $# -eq 0 || "${1:-}" == "--enable-backup" ]] || { echo "Usage: sudo $0 [--enable-backup]" >&2; exit 2; }

# Fail closed before mutation if any retired application runtime identity has
# reappeared. Generic OS facilities are independent; app-specific identities
# must never carry forward into EdSys Share.
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
CONFIG_FILE=${CONFIG_DIR}/edsys-share.conf
install -d -m 0750 -o root -g root "${CONFIG_DIR}"
if [[ ! -e "${CONFIG_FILE}" ]]; then
  install -m 0640 -o root -g root "${SCRIPT_DIR}/edsys-share.conf.example" "${CONFIG_FILE}"
fi
# shellcheck source=/dev/null
source "${CONFIG_FILE}"
: "${EDSYS_SHARE_RESTORE_DIR:=/mnt/ai-store/edsys-share-restore-staging}"

[[ "${EDSYS_SHARE_TARGET}" == /EdSys-Share ]] || { echo "Unexpected share target" >&2; exit 2; }
[[ "${EDSYS_SHARE_SOURCE}" == /mnt/ai-store/* ]] || { echo "Share source must be under AI Store" >&2; exit 2; }
ip -4 addr show tailscale0 | grep -q "inet ${EDSYS_SHARE_TAILNET_LISTEN_IP}/" || {
  echo "Configured Tailnet listen address is not present on tailscale0" >&2
  exit 2
}

timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
install -d -m 0700 -o root -g root /var/backups/edsys-share
cp -a /etc/fstab "/var/backups/edsys-share/fstab.${timestamp}"
cp -a /etc/samba/smb.conf "/var/backups/edsys-share/smb.conf.${timestamp}"
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
command -v cc >/dev/null || { echo "A C compiler is required for the confined Samba mount guard" >&2; exit 1; }
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

# Render a complete Samba candidate. The share is replaced idempotently and
# global Samba remains bound only to loopback plus the real LAN interface.
candidate="$(mktemp)"
cp /etc/samba/smb.conf "${candidate}"
python3 - "${candidate}" "${SCRIPT_DIR}/samba-share.conf" <<'PY'
from pathlib import Path
import re
import sys

config = Path(sys.argv[1])
fragment = Path(sys.argv[2]).read_text().strip()
text = config.read_text()
text = re.sub(r"(?m)^\s*interfaces\s*=.*$", "   interfaces = lo enp7s0", text, count=1)

global_start = text.index("[global]")
next_section = text.find("\n[", global_start + len("[global]"))
head, tail = text[:next_section], text[next_section:]
head = re.sub(r"(?m)^\s*hosts (allow|deny)\s*=.*\n?", "", head)
needle = "   bind interfaces only = yes\n"
if needle not in head:
    raise SystemExit("Samba bind-interfaces setting not found")
head = head.replace(needle, needle + "   hosts allow = 127.0.0.1 192.168.50.0/24\n   hosts deny = 0.0.0.0/0\n", 1)
text = head + tail

text = text.replace("\n# EdSys Courier read-only Explorer access. Credential is stored outside Git.\n", "\n")
text = re.sub(r"(?ms)\n\[EdSys-Share\]\n.*?(?=\n\[[^\n]+\]\n|\Z)", "\n", text)
config.write_text(text.rstrip() + "\n\n" + fragment + "\n")
PY
testparm -s "${candidate}" >/dev/null
install -m 0644 -o root -g root "${candidate}" /etc/samba/smb.conf
rm -f "${candidate}"
systemctl restart smbd nmbd

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
