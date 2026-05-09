#!/usr/bin/env bash
set -Eeuo pipefail

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run with sudo on 9950x." >&2
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

apt-get update
apt-get install -y rclone restic jq

install -d -m 0750 -o root -g root /etc/edsys-backup
install -d -m 0755 -o root -g root /srv/edsys-backup/scripts
install -d -m 0750 -o root -g root /srv/edsys-backup/staging
install -d -m 0755 -o root -g root /srv/edsys-backup/reports
install -d -m 0750 -o root -g root /srv/edsys-backup/restore-tests
install -d -m 0755 -o root -g root /var/lib/edsys-backup
install -d -m 0750 -o root -g root /var/cache/edsys-backup/restic

install -m 0755 "${SCRIPT_DIR}/edsys-init-restic.sh" /srv/edsys-backup/scripts/edsys-init-restic.sh
install -m 0755 "${SCRIPT_DIR}/edsys-backup.sh" /srv/edsys-backup/scripts/edsys-backup.sh
install -m 0755 "${SCRIPT_DIR}/edsys-collect-remotes.sh" /srv/edsys-backup/scripts/edsys-collect-remotes.sh
install -m 0755 "${SCRIPT_DIR}/edsys-restic-check.sh" /srv/edsys-backup/scripts/edsys-restic-check.sh
install -m 0755 "${SCRIPT_DIR}/edsys-restore-test.sh" /srv/edsys-backup/scripts/edsys-restore-test.sh

if [[ ! -f /etc/edsys-backup/backup.env ]]; then
  install -m 0600 "${SCRIPT_DIR}/edsys-backup.env.example" /etc/edsys-backup/backup.env
fi

if [[ ! -f /etc/edsys-backup/includes.txt ]]; then
  install -m 0644 "${SCRIPT_DIR}/includes.9950x.example" /etc/edsys-backup/includes.txt
fi

if [[ ! -f /etc/edsys-backup/excludes.txt ]]; then
  install -m 0644 "${SCRIPT_DIR}/excludes.example" /etc/edsys-backup/excludes.txt
fi

if [[ ! -f /etc/edsys-backup/restic-password ]]; then
  umask 077
  openssl rand -base64 48 > /etc/edsys-backup/restic-password
fi

if [[ ! -f /etc/edsys-backup/rclone.conf ]]; then
  install -m 0600 /dev/null /etc/edsys-backup/rclone.conf
fi

install -m 0644 "${SCRIPT_DIR}/systemd/edsys-backup.service" /etc/systemd/system/edsys-backup.service
install -m 0644 "${SCRIPT_DIR}/systemd/edsys-backup.timer" /etc/systemd/system/edsys-backup.timer
install -m 0644 "${SCRIPT_DIR}/systemd/edsys-backup-check.service" /etc/systemd/system/edsys-backup-check.service
install -m 0644 "${SCRIPT_DIR}/systemd/edsys-backup-check.timer" /etc/systemd/system/edsys-backup-check.timer
install -m 0644 "${SCRIPT_DIR}/systemd/edsys-restore-test.service" /etc/systemd/system/edsys-restore-test.service
install -m 0644 "${SCRIPT_DIR}/systemd/edsys-restore-test.timer" /etc/systemd/system/edsys-restore-test.timer
systemctl daemon-reload

echo "Installed EdSys backup framework."
echo "Next: sudo rclone --config /etc/edsys-backup/rclone.conf config"
echo "Use remote name: edsys-gdrive"
echo "Do not enable timers until edsys-init-restic.sh succeeds."
