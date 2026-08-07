#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
venv="${HOME}/.local/share/edsys-fleet-autopilot/venv"
bin_dir="${HOME}/.local/bin"
unit_dir="${HOME}/.config/systemd/user"
state_root="${EDSYS_FLEET_STATE_ROOT:-/opt/edsys-workhorse/edsys-ai-portal/data/fleet}"

python3 -m venv "${venv}"
"${venv}/bin/pip" install --disable-pip-version-check --upgrade pip setuptools wheel
"${venv}/bin/pip" install --disable-pip-version-check "${root}"

mkdir -p "${bin_dir}" "${unit_dir}"

# The Portal data root is owned by the image's unprivileged UID/GID. Grant the
# host operator traversal only, then use a setgid Fleet subtree shared with the
# operator GID already supplied to the Portal through Compose `group_add`. This
# keeps every other Portal runtime artifact
# private while allowing the credential-free JSON queue to work both ways.
state_parent="$(dirname "${state_root}")"
if [[ ! -w "${state_parent}" ]]; then
  sudo -n setfacl -m "u:$(id -u):rx" "$(dirname "${state_parent}")" "${state_parent}"
  sudo -n install -d -o "$(id -u)" -g "$(id -g)" -m 2770 \
    "${state_root}" "${state_root}/queue" "${state_root}/queue/pending" \
    "${state_root}/queue/running" "${state_root}/queue/completed"
  sudo -n chown -R "$(id -u):$(id -g)" "${state_root}"
else
  mkdir -p "${state_root}/queue/pending" "${state_root}/queue/running" "${state_root}/queue/completed"
  chmod 2770 "${state_root}" "${state_root}/queue" "${state_root}/queue/pending" "${state_root}/queue/running" "${state_root}/queue/completed"
fi

for command in edsys-fleet edsys-fleet-worker edsys-proxmox-mcp; do
  ln -sfn "${venv}/bin/${command}" "${bin_dir}/${command}"
done
install -m 0644 "${root}/systemd/edsys-fleet-worker.service" "${unit_dir}/edsys-fleet-worker.service"
install -m 0644 "${root}/systemd/edsys-fleet-collect.service" "${unit_dir}/edsys-fleet-collect.service"
install -m 0644 "${root}/systemd/edsys-fleet-collect.timer" "${unit_dir}/edsys-fleet-collect.timer"

systemctl --user daemon-reload
systemctl --user enable edsys-fleet-worker.service edsys-fleet-collect.timer
systemctl --user restart edsys-fleet-worker.service
systemctl --user start edsys-fleet-collect.timer
"${bin_dir}/edsys-fleet" collect >/dev/null
printf 'EdSys Fleet Autopilot installed. State: %s\n' "${state_root}"
