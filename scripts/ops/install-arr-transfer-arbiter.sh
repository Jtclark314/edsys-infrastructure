#!/usr/bin/env bash
set -euo pipefail
umask 077

if [[ ${EUID} -ne 0 ]]; then
  echo "Run as root (for example: sudo $0 --enable)." >&2
  exit 2
fi

enable=0
case "${1:-}" in
  --enable) enable=1 ;;
  "") ;;
  *) echo "Usage: $0 [--enable]" >&2; exit 2 ;;
esac

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
source_script="${repo_root}/scripts/ops/arr-transfer-arbiter.py"
source_env="${repo_root}/scripts/ops/arr-transfer-arbiter.env.example"
source_units="${repo_root}/scripts/ops/systemd"
source_doc="${repo_root}/docs/ARR_TRANSFER_ARBITER.md"
runtime_script="/usr/local/sbin/arr-transfer-arbiter"
runtime_env="/etc/default/arr-transfer-arbiter"
runtime_doc="/usr/local/share/doc/arr-transfer-arbiter/README.md"
unit_dir="/etc/systemd/system"
compose_file="/opt/arr-vpn/docker-compose.yml"
sab_config="/srv/ssd1/docker/appdata/sabnzbd/sabnzbd.ini"
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
backup_dir="/var/backups/arr-transfer-arbiter/${stamp}"
manifest="${backup_dir}/install-manifest.tsv"
compose_backup=""
compose_replacement_pending=0
original_qbit_restart_policy="$(docker inspect -f '{{.HostConfig.RestartPolicy.Name}}' qbittorrent 2>/dev/null || true)"
original_service_enabled="$(systemctl is-enabled arr-transfer-arbiter.service 2>/dev/null || true)"
original_service_active="$(systemctl is-active arr-transfer-arbiter.service 2>/dev/null || true)"
original_health_timer_enabled="$(systemctl is-enabled arr-transfer-arbiter-health.timer 2>/dev/null || true)"

for required in \
  "$source_script" \
  "$source_env" \
  "$source_doc" \
  "${source_units}/arr-transfer-arbiter.service" \
  "${source_units}/arr-transfer-arbiter-health.service" \
  "${source_units}/arr-transfer-arbiter-health.timer" \
  "$compose_file" \
  "$sab_config"; do
  if [[ ! -f "$required" ]]; then
    echo "Required file is missing: $required" >&2
    exit 1
  fi
done

command -v docker >/dev/null
command -v systemctl >/dev/null
python3 -m py_compile "$source_script"

install -d -m 0700 "$backup_dir"
printf 'target\texisted\tbackup\n' >"$manifest"

backup_target() {
  local target="$1" label="$2"
  local destination="${backup_dir}/${label}"
  if [[ -e "$target" || -L "$target" ]]; then
    cp -a -- "$target" "$destination"
    printf '%s\ttrue\t%s\n' "$target" "$destination" >>"$manifest"
  else
    printf '%s\tfalse\t-\n' "$target" >>"$manifest"
  fi
}

on_error() {
  local status=$?
  trap - ERR
  if (( compose_replacement_pending == 1 )) && [[ -n "$compose_backup" ]]; then
    cp -a -- "$compose_backup" "$compose_file" || true
  fi
  if [[ -x "$runtime_script" ]]; then
    "$runtime_script" fail-safe >/dev/null 2>&1 || true
  else
    python3 "$source_script" fail-safe >/dev/null 2>&1 || true
  fi
  echo "Installation failed closed; both clients were held where dependencies allowed." >&2
  echo "Private backup material: $backup_dir" >&2
  exit "$status"
}
trap on_error ERR

# Freeze qBittorrent immediately and prevent a Docker-daemon restart from
# reviving it while the original persistent files are being captured.
docker update --restart=no qbittorrent >/dev/null
if [[ "$(docker inspect -f '{{.State.Running}} {{.State.Paused}}' qbittorrent)" == "true false" ]]; then
  docker pause qbittorrent >/dev/null
fi
qbit_safety_state="$(docker inspect -f '{{.State.Status}} {{.State.Paused}}' qbittorrent)"
case "$qbit_safety_state" in
  "running true"|"paused true"|"created false"|"exited false"|"dead false") ;;
  *) echo "qBittorrent could not be placed in a confirmed safe state." >&2; false ;;
esac
if systemctl is-active --quiet arr-transfer-arbiter.service 2>/dev/null; then
  systemctl stop arr-transfer-arbiter.service
fi

backup_target "$runtime_script" "arr-transfer-arbiter"
backup_target "$runtime_env" "arr-transfer-arbiter.env"
backup_target "$runtime_doc" "README.md"
backup_target "${unit_dir}/arr-transfer-arbiter.service" "arr-transfer-arbiter.service"
backup_target "${unit_dir}/arr-transfer-arbiter-health.service" "arr-transfer-arbiter-health.service"
backup_target "${unit_dir}/arr-transfer-arbiter-health.timer" "arr-transfer-arbiter-health.timer"
backup_target "$compose_file" "qbit-docker-compose.yml"
compose_backup="${backup_dir}/qbit-docker-compose.yml"
backup_target "$sab_config" "sabnzbd.ini"

{
  printf 'service_enabled=%s\n' "$original_service_enabled"
  printf 'service_active=%s\n' "$original_service_active"
  printf 'health_timer_enabled=%s\n' "$original_health_timer_enabled"
  printf 'qbit_restart_policy=%s\n' "$original_qbit_restart_policy"
} >"${backup_dir}/runtime-state.txt"

# With the original persistent files captured, hold SAB as well and verify the
# complete fail-safe posture before installing anything.
python3 "$source_script" fail-safe >/dev/null

# Persist SAB's safe reboot posture as soon as its original config has been
# captured.  Repeating this after installation is an intentional verification.
python3 "$source_script" configure-boot-safety >/dev/null

install -m 0755 "$source_script" "$runtime_script"
install -D -m 0644 "$source_doc" "$runtime_doc"
if [[ ! -e "$runtime_env" ]]; then
  install -m 0644 "$source_env" "$runtime_env"
fi
install -m 0644 "${source_units}/arr-transfer-arbiter.service" \
  "${unit_dir}/arr-transfer-arbiter.service"
install -m 0644 "${source_units}/arr-transfer-arbiter-health.service" \
  "${unit_dir}/arr-transfer-arbiter-health.service"
install -m 0644 "${source_units}/arr-transfer-arbiter-health.timer" \
  "${unit_dir}/arr-transfer-arbiter-health.timer"

python3 -m py_compile "$runtime_script"
systemd-analyze verify \
  "${unit_dir}/arr-transfer-arbiter.service" \
  "${unit_dir}/arr-transfer-arbiter-health.service" \
  "${unit_dir}/arr-transfer-arbiter-health.timer"

compose_replacement_pending=1
"$runtime_script" configure-compose --path "$compose_file" >/dev/null
if ! docker compose --project-directory "$(dirname "$compose_file")" \
  --file "$compose_file" config --quiet; then
  cp -a -- "$compose_backup" "$compose_file"
  compose_replacement_pending=0
  echo "Compose validation failed; restored the prior Compose file." >&2
  false
fi
compose_replacement_pending=0

docker update --restart=no qbittorrent >/dev/null
[[ "$(docker inspect -f '{{.HostConfig.RestartPolicy.Name}}' qbittorrent)" == "no" ]]
"$runtime_script" configure-boot-safety >/dev/null

systemctl daemon-reload
if (( enable == 1 )); then
  systemctl enable arr-transfer-arbiter.service arr-transfer-arbiter-health.timer >/dev/null
  systemctl start arr-transfer-arbiter.service
  systemctl start arr-transfer-arbiter-health.timer
  systemctl is-active --quiet arr-transfer-arbiter.service
  sleep 3
  "$runtime_script" status --check >/dev/null
fi

trap - ERR
echo "Installed the fail-closed ARR transfer arbiter."
if (( enable == 1 )); then
  echo "The controller and one-minute health timer are enabled and active."
else
  echo "Both clients remain held. Re-run with --enable after review."
fi
echo "Private backup material: $backup_dir"
