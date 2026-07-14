#!/usr/bin/env bash
set -Eeuo pipefail

if [[ ${EUID} -ne 0 ]]; then
  echo "Run as root (for example: sudo $0)." >&2
  exit 2
fi

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
source_script="${repo_root}/scripts/ops/edsys-reboot-acceptance.sh"
source_unit="${repo_root}/scripts/ops/systemd/edsys-reboot-acceptance.service"
live_script="/usr/local/sbin/edsys-reboot-acceptance"
live_unit="/etc/systemd/system/edsys-reboot-acceptance.service"
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
backup_dir="/var/backups/edsys-reboot-acceptance/${stamp}"

for command_name in install shellcheck systemctl systemd-analyze; do
  command -v "${command_name}" >/dev/null || {
    echo "Required installer command is unavailable: ${command_name}" >&2
    exit 2
  }
done

shellcheck "${source_script}" "${BASH_SOURCE[0]}"

install -d -m 0700 -o root -g root "${backup_dir}"
[[ ! -e ${live_script} ]] || cp -a "${live_script}" "${backup_dir}/"
[[ ! -e ${live_unit} ]] || cp -a "${live_unit}" "${backup_dir}/"

install -m 0755 -o root -g root "${source_script}" "${live_script}"
if ! systemd-analyze verify "${source_unit}"; then
  if [[ -e ${backup_dir}/edsys-reboot-acceptance ]]; then
    cp -a "${backup_dir}/edsys-reboot-acceptance" "${live_script}"
  else
    rm -f "${live_script}"
  fi
  echo "Unit validation failed; restored the preceding live helper." >&2
  exit 1
fi
install -m 0644 -o root -g root "${source_unit}" "${live_unit}"
install -d -m 0700 -o root -g root /var/lib/edsys-reboot-acceptance/runs

systemctl daemon-reload
systemctl enable edsys-reboot-acceptance.service

echo "Installed one-shot reboot acceptance tooling."
echo "Private rollback material: ${backup_dir}"
echo "No acceptance run was armed and no reboot was requested."
