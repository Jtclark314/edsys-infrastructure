#!/usr/bin/env bash
set -euo pipefail

TAILSCALE_IP="${EDSYS_AI_TAILSCALE_IP:-100.87.137.47}"
LAN_IP="${EDSYS_AI_LAN_IP:-192.168.50.50}"
PORTS=(3000 3002 6333 7997 8015 8020 8099 11434)
MODE=install-only

usage() {
  cat <<'EOF'
Usage: sudo install-9950x-ai-tailnet-proxy.sh [--install-only|--enable]

  --install-only  Install and validate units when no managed proxy socket is
                  active. This is the safe first install before Docker
                  Tailnet publications have been removed.
  --enable        Transactionally install/reload units, refuse listener
                  conflicts, and enable all eight approved socket instances.
EOF
}

case "${1:-}" in
  ""|--install-only)
    MODE=install-only
    ;;
  --enable)
    MODE=enable
    ;;
  -h|--help)
    usage
    exit 0
    ;;
  *)
    usage >&2
    exit 2
    ;;
esac
[[ $# -le 1 ]] || {
  usage >&2
  exit 2
}

if [[ $EUID -ne 0 ]]; then
  echo "ERROR: run this installer as root" >&2
  exit 1
fi

for command_name in install sed systemctl systemd-analyze ss python3; do
  command -v "$command_name" >/dev/null || {
    echo "ERROR: required command is missing: $command_name" >&2
    exit 1
  }
done

[[ -x /usr/lib/systemd/systemd-socket-proxyd ]] || {
  echo "ERROR: /usr/lib/systemd/systemd-socket-proxyd is unavailable" >&2
  exit 1
}

SOURCE_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
SOCKET_TEMPLATE="${SOURCE_DIR}/systemd/edsys-ai-tailnet-proxy@.socket.in"
SERVICE_TEMPLATE="${SOURCE_DIR}/systemd/edsys-ai-tailnet-proxy@.service.in"
CHECK_SOURCE="${SOURCE_DIR}/edsys-ai-tailnet-proxy-check"
for source_file in "$SOCKET_TEMPLATE" "$SERVICE_TEMPLATE" "$CHECK_SOURCE"; do
  [[ -f "$source_file" ]] || {
    echo "ERROR: missing source file: $source_file" >&2
    exit 1
  }
done

python3 - "$TAILSCALE_IP" "$LAN_IP" <<'PY'
import ipaddress
import sys

tailnet = ipaddress.ip_address(sys.argv[1])
lan = ipaddress.ip_address(sys.argv[2])
if tailnet.version != 4 or lan.version != 4:
    raise SystemExit("ERROR: the proxy installer currently requires IPv4 addresses")
if not tailnet in ipaddress.ip_network("100.64.0.0/10"):
    raise SystemExit("ERROR: EDSYS_AI_TAILSCALE_IP is not a Tailnet IPv4 address")
if lan.is_loopback or lan.is_unspecified or lan.is_multicast:
    raise SystemExit("ERROR: EDSYS_AI_LAN_IP is not a usable LAN target")
PY

port_is_allowed() {
  local candidate="$1"
  local allowed
  for allowed in "${PORTS[@]}"; do
    [[ "$candidate" == "$allowed" ]] && return 0
  done
  return 1
}

while read -r unit _; do
  if [[ "$unit" =~ ^edsys-ai-tailnet-proxy@([0-9]+)\.socket$ ]] && \
      ! port_is_allowed "${BASH_REMATCH[1]}"; then
    echo "ERROR: unexpected managed proxy instance exists: ${unit}" >&2
    echo "Review and remove it explicitly before running this installer." >&2
    exit 1
  fi
done < <(systemctl list-unit-files 'edsys-ai-tailnet-proxy@*.socket' --no-legend || true)

declare -A WAS_ACTIVE=()
declare -A WAS_ENABLED=()
for port in "${PORTS[@]}"; do
  unit="edsys-ai-tailnet-proxy@${port}.socket"
  if systemctl is-active --quiet "$unit"; then
    WAS_ACTIVE["$port"]=yes
  else
    WAS_ACTIVE["$port"]=no
  fi
  if systemctl is-enabled --quiet "$unit"; then
    WAS_ENABLED["$port"]=yes
  else
    WAS_ENABLED["$port"]=no
  fi
done

if [[ "$MODE" == install-only ]]; then
  for port in "${PORTS[@]}"; do
    if [[ "${WAS_ACTIVE[$port]}" == yes ]]; then
      echo "ERROR: managed proxy sockets are already active; use --enable for a transactional reload" >&2
      echo "No proxy file or unit state was changed." >&2
      exit 1
    fi
  done
fi

if [[ "$MODE" == enable ]]; then
  for port in "${PORTS[@]}"; do
    if ss -H -lntp "sport = :${port}" | awk -v address="${TAILSCALE_IP}:${port}" \
        '$4 == address {found=1} END {exit !found}'; then
      unit="edsys-ai-tailnet-proxy@${port}.socket"
      if [[ "${WAS_ACTIVE[$port]}" != yes ]]; then
        echo "ERROR: ${TAILSCALE_IP}:${port} is owned by a different listener" >&2
        echo "No proxy file or unit state was changed." >&2
        exit 1
      fi
    fi
  done
fi

umask 077
backup_dir="/var/backups/edsys-ai-tailnet-proxy/$(date -u +%Y%m%dT%H%M%SZ)-$$"
install -d -m 0700 "$backup_dir"
for path in \
  /etc/systemd/system/edsys-ai-tailnet-proxy@.socket \
  /etc/systemd/system/edsys-ai-tailnet-proxy@.service \
  /usr/local/sbin/edsys-ai-tailnet-proxy-check; do
  if [[ -e "$path" ]]; then
    install -m 0600 "$path" "${backup_dir}/$(basename -- "$path").previous"
  fi
done
if [[ -d /etc/edsys-ai-tailnet-proxy ]]; then
  cp -a /etc/edsys-ai-tailnet-proxy "${backup_dir}/configuration.previous"
fi
systemctl list-unit-files 'edsys-ai-tailnet-proxy@*.socket' --no-legend \
  >"${backup_dir}/unit-files.before.txt" 2>&1 || true

ROLLBACK_ARMED=true
rollback_transaction() {
  local port
  local unit
  ROLLBACK_ARMED=false
  trap - ERR INT TERM
  echo "ERROR: proxy installation or acceptance failed; restoring prior files and unit state" >&2

  for port in "${PORTS[@]}"; do
    systemctl stop "edsys-ai-tailnet-proxy@${port}.service" >/dev/null 2>&1 || true
    systemctl stop "edsys-ai-tailnet-proxy@${port}.socket" >/dev/null 2>&1 || true
  done

  if [[ -f "${backup_dir}/edsys-ai-tailnet-proxy@.socket.previous" ]]; then
    install -m 0644 "${backup_dir}/edsys-ai-tailnet-proxy@.socket.previous" \
      /etc/systemd/system/edsys-ai-tailnet-proxy@.socket
  else
    rm -f /etc/systemd/system/edsys-ai-tailnet-proxy@.socket
  fi
  if [[ -f "${backup_dir}/edsys-ai-tailnet-proxy@.service.previous" ]]; then
    install -m 0644 "${backup_dir}/edsys-ai-tailnet-proxy@.service.previous" \
      /etc/systemd/system/edsys-ai-tailnet-proxy@.service
  else
    rm -f /etc/systemd/system/edsys-ai-tailnet-proxy@.service
  fi
  if [[ -f "${backup_dir}/edsys-ai-tailnet-proxy-check.previous" ]]; then
    install -m 0755 "${backup_dir}/edsys-ai-tailnet-proxy-check.previous" \
      /usr/local/sbin/edsys-ai-tailnet-proxy-check
  else
    rm -f /usr/local/sbin/edsys-ai-tailnet-proxy-check
  fi

  rm -rf /etc/edsys-ai-tailnet-proxy
  if [[ -d "${backup_dir}/configuration.previous" ]]; then
    cp -a "${backup_dir}/configuration.previous" /etc/edsys-ai-tailnet-proxy
  fi

  systemctl daemon-reload
  for port in "${PORTS[@]}"; do
    unit="edsys-ai-tailnet-proxy@${port}.socket"
    if [[ "${WAS_ENABLED[$port]}" == yes ]]; then
      systemctl enable "$unit" >/dev/null 2>&1 || true
    else
      systemctl disable "$unit" >/dev/null 2>&1 || true
    fi
    if [[ "${WAS_ACTIVE[$port]}" == yes ]]; then
      systemctl start "$unit" >/dev/null 2>&1 || true
    else
      systemctl stop "$unit" >/dev/null 2>&1 || true
    fi
  done
  echo "Private rollback material: ${backup_dir}" >&2
}

on_failure() {
  local exit_status=$?
  if $ROLLBACK_ARMED; then
    rollback_transaction
  fi
  exit "$exit_status"
}
on_signal() {
  if $ROLLBACK_ARMED; then
    rollback_transaction
  fi
  exit 130
}
trap on_failure ERR
trap on_signal INT TERM

render_unit() {
  local source="$1"
  local destination="$2"
  sed \
    -e "s|@TAILSCALE_IP@|${TAILSCALE_IP}|g" \
    -e "s|@LAN_IP@|${LAN_IP}|g" \
    "$source" >"${destination}.new"
  chmod 0644 "${destination}.new"
  mv -f "${destination}.new" "$destination"
}

render_unit "$SOCKET_TEMPLATE" /etc/systemd/system/edsys-ai-tailnet-proxy@.socket
render_unit "$SERVICE_TEMPLATE" /etc/systemd/system/edsys-ai-tailnet-proxy@.service
install -m 0755 "$CHECK_SOURCE" /usr/local/sbin/edsys-ai-tailnet-proxy-check

install -d -m 0755 /etc/edsys-ai-tailnet-proxy/ports
find /etc/edsys-ai-tailnet-proxy/ports -mindepth 1 -maxdepth 1 -type f -delete
for port in "${PORTS[@]}"; do
  install -m 0644 /dev/null "/etc/edsys-ai-tailnet-proxy/ports/${port}"
done

for port in "${PORTS[@]}"; do
  systemd-analyze verify \
    "edsys-ai-tailnet-proxy@${port}.socket" \
    "edsys-ai-tailnet-proxy@${port}.service"
done
systemctl daemon-reload
for port in "${PORTS[@]}"; do
  systemctl reset-failed "edsys-ai-tailnet-proxy@${port}.service" 2>/dev/null || true
done

if [[ "$MODE" == install-only ]]; then
  echo "Installed and verified proxy units without starting any new Tailnet listener."
  echo "After Docker binds only loopback/LAN, run: $0 --enable"
  echo "Private rollback material: ${backup_dir}"
  ROLLBACK_ARMED=false
  trap - ERR INT TERM
  exit 0
fi

enable_and_check() {
  local port
  local unit
  for port in "${PORTS[@]}"; do
    systemctl stop "edsys-ai-tailnet-proxy@${port}.service" || return 1
    systemctl reset-failed "edsys-ai-tailnet-proxy@${port}.service" 2>/dev/null || true
  done
  for port in "${PORTS[@]}"; do
    unit="edsys-ai-tailnet-proxy@${port}.socket"
    systemctl enable "$unit" || return 1
    systemctl restart "$unit" || return 1
  done

  EDSYS_AI_TAILSCALE_IP="$TAILSCALE_IP" EDSYS_AI_LAN_IP="$LAN_IP" \
    /usr/local/sbin/edsys-ai-tailnet-proxy-check || return 1
}
enable_and_check

ROLLBACK_ARMED=false
trap - ERR INT TERM
echo "Enabled all approved EdSys AI Tailnet proxy sockets."
echo "Private rollback material: ${backup_dir}"
