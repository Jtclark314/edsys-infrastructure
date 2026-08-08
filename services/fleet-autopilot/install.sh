#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
bin_dir="${HOME}/.local/bin"
unit_dir="${HOME}/.config/systemd/user"
state_root="${EDSYS_FLEET_STATE_ROOT:-/opt/edsys-workhorse/edsys-ai-portal/data/fleet}"
private_root="${EDSYS_FLEET_PRIVATE_ARTIFACT_ROOT:-/mnt/ai-store/private/fleet-autopilot}"
age_key="${EDSYS_FLEET_BACKUP_AGE_KEY:-${HOME}/.local/share/edsys-fleet-autopilot/backup-age-key.txt}"

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
    "${state_root}/queue/running" "${state_root}/queue/completed" \
    "${state_root}/queue/awaiting-agent" "${state_root}/recovery" \
    "${state_root}/codex-uploads"
  sudo -n chown -R "$(id -u):$(id -g)" "${state_root}"
else
  mkdir -p "${state_root}/queue/pending" "${state_root}/queue/running" "${state_root}/queue/completed" "${state_root}/queue/awaiting-agent" "${state_root}/recovery" "${state_root}/codex-uploads"
  chmod 2770 "${state_root}" "${state_root}/queue" "${state_root}/queue/pending" "${state_root}/queue/running" "${state_root}/queue/completed" "${state_root}/queue/awaiting-agent" "${state_root}/recovery" "${state_root}/codex-uploads"
fi

sudo -n install -d -o "$(id -u)" -g "$(id -g)" -m 0700 \
  "${private_root}" "${private_root}/benchmarks" "${private_root}/backups"

mkdir -p -m 0700 "$(dirname "${age_key}")"
if [[ ! -s "${age_key}" ]]; then
  age-keygen -o "${age_key}" >/dev/null
fi
chmod 0600 "${age_key}"

python3 "${root}/tools/fleet-self-update.py" apply --source "${root}"
printf 'EdSys Fleet Autopilot installed. State: %s\n' "${state_root}"
