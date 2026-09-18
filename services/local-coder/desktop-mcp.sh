#!/usr/bin/env bash
set -euo pipefail
umask 077
export EDSYS_LOCAL_DESKTOP=1
export NO_AT_BRIDGE=1
# Only Python's protocol stream reaches stdout. D-Bus activated applications
# may otherwise print banners into the JSON-RPC transport.
exec 3>&1
exec /usr/bin/dbus-run-session -- /usr/bin/xvfb-run --auto-servernum --server-num=90 \
  --server-args='-screen 0 1280x800x24 -nolisten tcp' \
  /bin/bash -c 'exec "$@" >&3 3>&-' desktop-protocol \
  /mnt/ai-store/apps/local-coder/desktop-venv/bin/python \
  /srv/edsys/edsys-infrastructure/services/local-coder/desktop_mcp.py 1>&2
