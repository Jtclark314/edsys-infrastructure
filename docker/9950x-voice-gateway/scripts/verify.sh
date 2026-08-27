#!/usr/bin/env bash
set -euo pipefail

readonly ca_file="/etc/edsys-secrets/voice-gateway/tls/ca.crt"

curl --fail --silent --show-error --cacert "${ca_file}" https://127.0.0.1:8055/health/live \
  | python3 -c 'import json,sys; d=json.load(sys.stdin); assert d["status"] == "live"'

curl --fail --silent --show-error http://127.0.0.1:8056/metrics \
  | grep -Fq 'edsys_voice_requests_total'

docker inspect --format '{{.State.Health.Status}}' edsys-voice-gateway | grep -Fxq healthy
ss -H -ltn | awk '{print $4}' | grep -Fxq '127.0.0.1:8055'
ss -H -ltn | awk '{print $4}' | grep -Fxq '192.168.50.50:8055'
ss -H -ltn | awk '{print $4}' | grep -Fxq '127.0.0.1:8056'

if ss -H -ltn | awk '{print $4}' | grep -Eq '(^|:)0\.0\.0\.0:8055$|\[::\]:8055$'; then
  echo "wildcard voice listener detected" >&2
  exit 1
fi

echo "voice gateway listeners, health, and loopback metrics verified"
