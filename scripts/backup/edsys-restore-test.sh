#!/usr/bin/env bash
set -Eeuo pipefail

CONFIG_FILE="${EDSYS_BACKUP_CONFIG:-/etc/edsys-backup/edsys-backup.conf}"
if [[ -f "${CONFIG_FILE}" ]]; then
  # shellcheck disable=SC1090
  source "${CONFIG_FILE}"
fi

: "${RCLONE_CONFIG:=/etc/edsys-backup/rclone.conf}"
: "${RCLONE_REMOTE:=edsys-gdrive}"
: "${DRIVE_BACKUP_ROOT:=EdSys Backups}"
: "${RESTIC_REPOSITORY:=/srv/edsys-backup/restic-repo/edsys-critical}"
: "${RESTIC_PASSWORD_FILE:=/etc/edsys-backup/restic-password}"
: "${RESTIC_CACHE_DIR:=/var/cache/edsys-backup/restic}"
: "${RESTORE_TEST_DIR:=/srv/edsys-backup/restore-tests}"
: "${REPORT_DIR:=/srv/edsys-backup/reports}"

export RCLONE_CONFIG RESTIC_REPOSITORY RESTIC_PASSWORD_FILE RESTIC_CACHE_DIR

RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)"
TARGET="${RESTORE_TEST_DIR}/${RUN_ID}"
REPORT="${REPORT_DIR}/restore-test-${RUN_ID}.md"
mkdir -p "${TARGET}" "${REPORT_DIR}"

restic restore latest --target "${TARGET}" --include "/srv/edsys/EdSys-Master/data" --include "/srv/ssd1/docker/stacks/factorio/data/config" --include "/srv/homepage/config" >/tmp/edsys-restore-test.out 2>&1

{
  echo "# EdSys Restore Test ${RUN_ID}"
  echo
  echo "- Target: ${TARGET}"
  echo "- Result: success"
  echo
  echo "## Restored Top-Level Paths"
  find "${TARGET}" -maxdepth 5 -type d | sed "s#${TARGET}#.#" | sort | head -80 | sed 's/^/- /'
} > "${REPORT}"

if command -v python3 >/dev/null && [[ -f "${TARGET}/srv/edsys/EdSys-Master/data/service-catalog.yml" ]]; then
  python3 - <<PY >> "${REPORT}"
import yaml
from pathlib import Path
for name in ["network-map.yml", "service-catalog.yml", "backup-catalog.yml"]:
    path = Path("${TARGET}/srv/edsys/EdSys-Master/data") / name
    if path.exists():
        yaml.safe_load(path.read_text(encoding="utf-8"))
        print(f"- YAML OK: {name}")
PY
fi

echo "Restore test report: ${REPORT}"
