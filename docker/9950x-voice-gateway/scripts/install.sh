#!/usr/bin/env bash
set -euo pipefail

readonly stack_dir="/srv/edsys/edsys-infrastructure/docker/9950x-voice-gateway"
readonly unit_dir="/etc/systemd/system"

[[ ${EUID} -eq 0 ]] || { echo "run as root" >&2; exit 1; }

install -m 0755 "${stack_dir}/scripts/firewall-apply.sh" /usr/local/sbin/edsys-voice-gateway-firewall
install -m 0755 "${stack_dir}/scripts/preflight.sh" /usr/local/sbin/edsys-voice-gateway-preflight
install -m 0644 "${stack_dir}/systemd/edsys-voice-gateway-firewall.service" \
  "${unit_dir}/edsys-voice-gateway-firewall.service"
install -m 0644 "${stack_dir}/systemd/edsys-voice-gateway-compose.service" \
  "${unit_dir}/edsys-voice-gateway-compose.service"

systemd-analyze verify \
  "${unit_dir}/edsys-voice-gateway-firewall.service" \
  "${unit_dir}/edsys-voice-gateway-compose.service"
systemctl daemon-reload

echo "voice gateway units installed but not enabled or started"
