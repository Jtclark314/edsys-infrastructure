#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
TARGET_BIN="${TARGET_BIN:-$HOME/bin}"
UNIT_DIR="${UNIT_DIR:-$HOME/.config/systemd/user}"
BACKUP_ROOT="${BACKUP_ROOT:-$HOME/.local/state/edsys-rag-sync/backups}"
ENABLE=0

if [ "${1:-}" = "--enable" ]; then
  ENABLE=1
elif [ "$#" -gt 0 ]; then
  echo "usage: $0 [--enable]" >&2
  exit 2
fi

for required in \
  "$SCRIPT_DIR/anythingllm-upload-edsys-rag" \
  "$SCRIPT_DIR/anythingllm-rag-verify.py" \
  "$SCRIPT_DIR/systemd/edsys-rag-sync.service" \
  "$SCRIPT_DIR/systemd/edsys-rag-sync.timer"; do
  [ -f "$required" ] || {
    echo "missing deployment source: $required" >&2
    exit 1
  }
done

for prerequisite in "$HOME/bin/edsys-rag-sync" "$HOME/bin/edsys-grounding-index"; do
  [ -x "$prerequisite" ] || {
    echo "missing runtime prerequisite: $prerequisite" >&2
    exit 1
  }
done

timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
backup_dir="$BACKUP_ROOT/$timestamp"
mkdir -p "$TARGET_BIN" "$UNIT_DIR"

install_file() {
  local source="$1"
  local target="$2"
  local mode="$3"

  if [ -e "$target" ] && ! cmp -s "$source" "$target"; then
    mkdir -p "$backup_dir"
    cp -a "$target" "$backup_dir/$(basename "$target")"
  fi
  install -m "$mode" "$source" "$target"
}

install_file "$SCRIPT_DIR/anythingllm-upload-edsys-rag" "$TARGET_BIN/anythingllm-upload-edsys-rag" 0755
install_file "$SCRIPT_DIR/anythingllm-rag-verify.py" "$TARGET_BIN/anythingllm-rag-verify.py" 0755
install_file "$SCRIPT_DIR/systemd/edsys-rag-sync.service" "$UNIT_DIR/edsys-rag-sync.service" 0644
install_file "$SCRIPT_DIR/systemd/edsys-rag-sync.timer" "$UNIT_DIR/edsys-rag-sync.timer" 0644

systemctl --user daemon-reload
if [ "$ENABLE" -eq 1 ]; then
  systemctl --user enable --now edsys-rag-sync.timer
fi

if [ -d "$backup_dir" ]; then
  printf 'backup=%s\n' "$backup_dir"
fi
printf 'installed=%s,%s,%s,%s\n' \
  "$TARGET_BIN/anythingllm-upload-edsys-rag" \
  "$TARGET_BIN/anythingllm-rag-verify.py" \
  "$UNIT_DIR/edsys-rag-sync.service" \
  "$UNIT_DIR/edsys-rag-sync.timer"
