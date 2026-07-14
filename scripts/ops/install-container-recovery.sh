#!/usr/bin/env bash
set -euo pipefail

if [[ ${EUID} -ne 0 ]]; then
  echo "Run as root (for example: sudo $0)." >&2
  exit 2
fi

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
source_dir="${repo_root}/docker/container-recovery"
runtime_dir="/etc/edsys-container-recovery"
unit_dir="/etc/systemd/system"
docker_dropin_dir="${unit_dir}/docker.service.d"
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
backup_dir="/opt/edsys-container-recovery/install-backups/${stamp}"

install -d -m 0700 "${backup_dir}"
install -d -m 0755 "${runtime_dir}" "${docker_dropin_dir}"
install -d -m 0700 "${runtime_dir}/env"
install -d -m 0755 /var/lib/edsys-container-recovery

if [[ -f /etc/docker/daemon.json ]]; then
  cp -a /etc/docker/daemon.json "${backup_dir}/daemon.json"
else
  printf '{}\n' >"${backup_dir}/daemon.json"
fi

install -m 0755 "${repo_root}/scripts/ops/edsys-container-recovery.py" \
  /usr/local/sbin/edsys-container-recovery
install -m 0644 "${source_dir}/manifest.yaml" "${runtime_dir}/manifest.yaml"
install -m 0644 "${source_dir}/systemd/edsys-container-recovery.service" \
  "${unit_dir}/edsys-container-recovery.service"
install -m 0644 "${source_dir}/systemd/edsys-container-recovery-audit.service" \
  "${unit_dir}/edsys-container-recovery-audit.service"
install -m 0644 "${source_dir}/systemd/edsys-container-recovery-audit.timer" \
  "${unit_dir}/edsys-container-recovery-audit.timer"
install -m 0644 "${source_dir}/systemd/docker-recovery.conf" \
  "${docker_dropin_dir}/30-edsys-container-recovery.conf"

python3 - /etc/docker/daemon.json <<'PY'
import json
import os
from pathlib import Path
import sys
path = Path(sys.argv[1])
data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
# Host reboots must let dockerd stop containers before systemd tears down
# Docker network namespaces and the external data-root mount.  The ordered
# recovery controller and existing restart policies provide the boot recovery
# path; live restore remains deliberately disabled for host-shutdown safety.
data["live-restore"] = False
data["shutdown-timeout"] = 120
tmp = path.with_suffix(".tmp")
tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
os.chmod(tmp, 0o644)
os.replace(tmp, path)
PY

dockerd --validate --config-file=/etc/docker/daemon.json
python3 -m py_compile /usr/local/sbin/edsys-container-recovery
systemd-analyze verify \
  "${unit_dir}/edsys-container-recovery.service" \
  "${unit_dir}/edsys-container-recovery-audit.service" \
  "${unit_dir}/edsys-container-recovery-audit.timer"
systemctl daemon-reload
systemctl enable --now edsys-container-recovery-audit.timer
systemctl reload docker

echo "Installed ordered recovery controls."
echo "Private rollback material: ${backup_dir}"
echo "Docker was reloaded, not restarted."
