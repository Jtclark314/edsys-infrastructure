#!/usr/bin/env bash
set -euo pipefail

# Generic local Healthchecks ping helper.
# Configure HC_PING_URL in a systemd EnvironmentFile or drop-in; this script prints no secrets.
if [[ -z "${HC_PING_URL:-}" ]]; then
  echo "HC_PING_URL is not set; skipping Healthchecks ping." >&2
  exit 0
fi
status="${1:-success}"
case "$status" in
  success) suffix="" ;;
  start|fail) suffix="/$status" ;;
  [0-9]*) suffix="/$status" ;;
  *) suffix="" ;;
esac
curl -fsS --max-time "${HC_TIMEOUT_SECONDS:-10}" "${HC_PING_URL}${suffix}" >/dev/null
