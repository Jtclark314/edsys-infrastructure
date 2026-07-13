#!/usr/bin/env bash
set -Eeuo pipefail

[[ "${EUID}" -eq 0 ]] || { echo "Run with sudo on 9950x." >&2; exit 1; }
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

install -d -m 0750 -o root -g root /etc/edsys-ssh
if [[ ! -f /etc/edsys-ssh/edsys-ssh-guard.conf ]]; then
  install -m 0640 -o root -g root "${SCRIPT_DIR}/edsys-ssh-guard.conf.example" /etc/edsys-ssh/edsys-ssh-guard.conf
fi
install -m 0755 -o root -g root "${SCRIPT_DIR}/edsys-ssh-guard" /usr/local/sbin/edsys-ssh-guard
install -m 0644 -o root -g root "${SCRIPT_DIR}/60-edsys-p1-hardening.conf" /etc/ssh/sshd_config.d/60-edsys-p1-hardening.conf
install -m 0644 -o root -g root "${SCRIPT_DIR}/edsys-ssh-guard.service" /etc/systemd/system/edsys-ssh-guard.service

/usr/sbin/sshd -t
/usr/local/sbin/edsys-ssh-guard
systemctl daemon-reload
systemctl enable --now edsys-ssh-guard.service
if systemctl is-active --quiet ssh.service; then
  systemctl reload ssh.service
fi

echo "Installed the 9950x EdSys SSH guard. Validate fresh controller connections before closing the maintenance session."
