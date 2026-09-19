#!/usr/bin/env bash
set -euo pipefail
umask 077
source_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
mountpoint -q /mnt/ai-store
python3 "$source_dir/install-integrations.py"
"$source_dir/install-context.sh"
if systemctl --user is-active --quiet edsys-local-coder-web-proxy.service; then
  systemctl --user restart edsys-local-coder-web-proxy.service
fi
install -m 0644 "$source_dir/edsys-local-coder-archive.service" "$HOME/.config/systemd/user/"
install -m 0644 "$source_dir/edsys-local-coder-archive.timer" "$HOME/.config/systemd/user/"
systemctl --user daemon-reload
systemctl --user enable --now edsys-local-coder-archive.timer
systemctl --user start edsys-local-coder-archive.service
sudo -n install -d -m 0755 /etc/systemd/system/edsys-backup.service.d
sudo -n install -m 0644 "$source_dir/edsys-backup-local-coder.conf" /etc/systemd/system/edsys-backup.service.d/50-local-coder.conf
sudo -n systemctl daemon-reload
echo 'Private archive timer and existing encrypted-backup staging hook installed'
