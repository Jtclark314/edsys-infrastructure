#!/usr/bin/env bash
set -euo pipefail

OUTPUT_ROOT="${1:-/tmp/edsys-audits}"
AUDIT_ID="${2:-linux-$(date +%Y%m%d-%H%M%S)}"
OUT_DIR="${OUTPUT_ROOT}/${AUDIT_ID}"
mkdir -p "$OUT_DIR"

run_capture() {
  local name="$1"
  shift
  {
    echo "# command: $*"
    echo "# captured_at: $(date -Is)"
    "$@" 2>&1 || true
  } > "${OUT_DIR}/${name}.txt"
}

cat > "${OUT_DIR}/metadata.txt" <<EOF
audit_id=${AUDIT_ID}
started_at=$(date -Is)
safety=read-only Linux metadata; no environment dumps; no .env reads; no logs
EOF

run_capture hostname hostname
run_capture hostnamectl hostnamectl
run_capture uname uname -a
run_capture ip-brief ip -br addr
run_capture ip-route ip route
run_capture df df -h
run_capture lsblk lsblk
run_capture free free -h
run_capture uptime uptime
run_capture systemctl-failed systemctl --failed --no-pager

if command -v ss >/dev/null 2>&1; then
  run_capture ss-tulpn ss -tulpn
fi

if command -v docker >/dev/null 2>&1; then
  run_capture docker-ps docker ps --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}'
  if docker compose version >/dev/null 2>&1; then
    run_capture docker-compose-ls docker compose ls
  fi
fi

echo "Linux baseline written to: ${OUT_DIR}"
echo "Review output before copying any content into Git."
