#!/usr/bin/env bash
set -euo pipefail

REMOTE="${KINDLE_DROP_BACKUP_REMOTE:-basecamp}"
REMOTE_DIR="${KINDLE_DROP_BACKUP_REMOTE_DIR:-C:\\EdSys\\KindleDrop\\backups}"
REMOTE_SCP_DIR="${KINDLE_DROP_BACKUP_REMOTE_SCP_DIR:-C:/EdSys/KindleDrop/backups}"
DEST_DIR="${KINDLE_DROP_BACKUP_DEST:-/mnt/ai-store/kindle-drop-basecamp-backups}"
DAILY_RETAIN="${KINDLE_DROP_BACKUP_DAILY_RETAIN:-35}"
MONTHLY_RETAIN="${KINDLE_DROP_BACKUP_MONTHLY_RETAIN:-18}"
LOCK_FILE="${KINDLE_DROP_BACKUP_LOCK:-/run/user/${UID}/kindle-drop-backup-pull.lock}"
SSH_OPTIONS=(
  -o BatchMode=yes
  -o ConnectTimeout=20
  -o ServerAliveInterval=10
  -o ServerAliveCountMax=2
)

mkdir -p "$DEST_DIR"
chmod 700 "$DEST_DIR"
exec 9>"$LOCK_FILE"
flock -n 9 || { echo "Another Kindle Drop backup pull is running." >&2; exit 75; }

remote_latest_name() {
  local kind="$1"
  # The validated kind intentionally expands into the remote command.
  # shellcheck disable=SC2029
  ssh "${SSH_OPTIONS[@]}" "$REMOTE" \
    "cmd.exe /d /c dir /b /o-d ${REMOTE_DIR}\\kindle-drop-${kind}-*.zip" \
    2>/dev/null |
    tr -d '\r' |
    grep -E "^kindle-drop-${kind}-[0-9]{8}-[0-9]{6}\\.zip$" |
    head -n 1
}

remote_sha256() {
  local filename="$1"
  # The validated filename intentionally expands into the remote command.
  # shellcheck disable=SC2029
  ssh "${SSH_OPTIONS[@]}" "$REMOTE" \
    "certutil.exe -hashfile ${REMOTE_DIR}\\${filename} SHA256" |
    tr -d '\r' |
    awk 'tolower($0) ~ /^[0-9a-f]{64}$/ { print tolower($0); exit }'
}

verify_archive() {
  local archive="$1"
  python3 - "$archive" <<'PY'
import sqlite3
import sys
import tempfile
import zipfile
from pathlib import Path

archive = Path(sys.argv[1])
with zipfile.ZipFile(archive) as source:
    names = source.namelist()
    if "state/kindle-drop.sqlite3" not in names:
        raise SystemExit("backup lacks SQLite snapshot")
    if any(name.startswith("/") or ".." in Path(name).parts for name in names):
        raise SystemExit("backup contains unsafe path")
    with tempfile.TemporaryDirectory() as temporary:
        db = Path(temporary) / "kindle-drop.sqlite3"
        db.write_bytes(source.read("state/kindle-drop.sqlite3"))
        connection = sqlite3.connect(db)
        try:
            if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise SystemExit("SQLite integrity check failed")
        finally:
            connection.close()
PY
}

prune_kind() {
  local kind="$1" retain="$2"
  mapfile -t files < <(
    find "$DEST_DIR" -maxdepth 1 -type f \
      -name "kindle-drop-${kind}-????????-??????.zip" -printf '%p\n' |
      sort -r
  )
  if ((${#files[@]} > retain)); then
    for stale in "${files[@]:retain}"; do
      rm -f -- "$stale" "${stale}.sha256"
    done
  fi
}

pull_kind() {
  local kind="$1" retain="$2" filename remote_hash final partial local_hash
  filename="$(remote_latest_name "$kind")"
  [[ -n "$filename" ]] || { echo "No ${kind} backup found on Basecamp." >&2; return 1; }
  remote_hash="$(remote_sha256 "$filename")"
  [[ "$remote_hash" =~ ^[0-9a-f]{64}$ ]] || {
    echo "Invalid Basecamp SHA-256 for ${filename}." >&2
    return 1
  }
  final="$DEST_DIR/$filename"
  if [[ ! -f "$final" ]]; then
    partial="${final}.partial.$$"
    trap 'rm -f -- "${partial:-}"' RETURN
    scp "${SSH_OPTIONS[@]}" \
      "${REMOTE}:${REMOTE_SCP_DIR}/${filename}" "$partial"
    local_hash="$(sha256sum "$partial" | awk '{print $1}')"
    [[ "$local_hash" == "$remote_hash" ]] || {
      echo "Transfer hash mismatch for ${filename}." >&2
      return 1
    }
    verify_archive "$partial"
    chmod 600 "$partial"
    mv -f -- "$partial" "$final"
    trap - RETURN
  else
    local_hash="$(sha256sum "$final" | awk '{print $1}')"
    [[ "$local_hash" == "$remote_hash" ]] || {
      echo "Existing hash mismatch for ${filename}." >&2
      return 1
    }
    verify_archive "$final"
  fi
  printf '%s  %s\n' "$remote_hash" "$filename" >"${final}.sha256"
  printf '%s\n' "$filename" >"$DEST_DIR/latest-${kind}.txt"
  prune_kind "$kind" "$retain"
  echo "${kind}: verified ${filename}"
}

pull_kind daily "$DAILY_RETAIN"
pull_kind monthly "$MONTHLY_RETAIN"
date -Is >"$DEST_DIR/last-successful-pull.txt"
