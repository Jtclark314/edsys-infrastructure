#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
install -d -m 0700 /mnt/ai-store/kindle-drop-basecamp-backups
install -d -m 0755 /home/jeremy/bin /home/jeremy/.config/systemd/user
install -m 0755 \
  "${script_dir}/kindle-drop-basecamp-backup-pull.sh" \
  /home/jeremy/bin/kindle-drop-basecamp-backup-pull
install -m 0644 \
  "${script_dir}/kindle-drop-basecamp-backup-pull.service" \
  /home/jeremy/.config/systemd/user/kindle-drop-basecamp-backup-pull.service
install -m 0644 \
  "${script_dir}/kindle-drop-basecamp-backup-pull.timer" \
  /home/jeremy/.config/systemd/user/kindle-drop-basecamp-backup-pull.timer
systemctl --user daemon-reload
systemctl --user enable --now kindle-drop-basecamp-backup-pull.timer
echo "Installed Kindle Drop Basecamp backup pull timer."
