#!/usr/bin/env bash
set -euo pipefail
test "$(id -u)" = 1000
mountpoint -q /mnt/ai-store
tm_state=/opt/edsys-workhorse/edsys-ai-portal/data/time-machine
sudo -n install -d -m 2770 -o jeremy -g 1000 "$tm_state" "$tm_state/requests" "$tm_state/results" "$tm_state/observations" /mnt/ai-store/private/time-machine-lab
test -x /opt/edsys-workhorse/edsys-ai-portal/data
install -D -m 0644 /srv/edsys/edsys-infrastructure/services/time-machine/edsys-time-machine.service /home/jeremy/.config/systemd/user/edsys-time-machine.service
systemctl --user daemon-reload
systemctl --user enable --now edsys-time-machine.service
