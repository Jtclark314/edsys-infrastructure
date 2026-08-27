#!/usr/bin/env bash
set -Eeuo pipefail

readonly disposition=/etc/edsys-escrow/client-disposition/edsys-edge-livingroom.json
readonly ingestion_acceptance=/etc/edsys-escrow/client-disposition/edsys-edge-livingroom-ingestion.json
readonly edge_certificate=/etc/edsys-secrets/edcore-automation/pki/clients/edsys-edge-livingroom.crt

[[ ${EUID} -eq 0 ]] || { echo "Run as root on edcore-automation." >&2; exit 1; }
[[ $(hostname -s) == edcore-automation ]] || { echo "Refusing disposition on the wrong guest." >&2; exit 1; }
[[ $# -eq 2 && $1 == edsys-edge-livingroom && $2 == --unused ]] || {
  echo "Usage: $0 edsys-edge-livingroom --unused" >&2
  exit 64
}
/usr/local/sbin/edsys-automation-source-guard --runtime
[[ -f $ingestion_acceptance && ! -L $ingestion_acceptance && \
   $(stat -c '%u:%g:%a' "$ingestion_acceptance") == 0:0:600 ]] || {
  echo "Synthetic ingestion/replay acceptance is required before unused disposition." >&2
  exit 1
}
[[ -s $edge_certificate ]] || { echo "Edge public certificate is absent." >&2; exit 1; }
acceptance_hash=$(sha256sum "$ingestion_acceptance" | awk '{print $1}')
certificate_hash=$(sha256sum "$edge_certificate" | awk '{print $1}')
python3 - "$ingestion_acceptance" "$certificate_hash" <<'PY'
import json
import re
import sys
value = json.load(open(sys.argv[1], encoding="utf-8"))
assert set(value) == {
    "schema", "identity", "certificate_sha256", "run_id", "source_topic",
    "sanitized_topic", "trace_sha256", "synthetic_value",
    "influx_measurement", "accepted_utc",
}
assert value["schema"] == "edsys.edcore-automation.synthetic-ingestion-acceptance.v1"
assert value["identity"] == "edsys-edge-livingroom"
assert value["certificate_sha256"] == sys.argv[2]
assert re.fullmatch(r"edge-[0-9]{14}-[0-9a-f]{6}", value["run_id"])
assert value["source_topic"] == "edsys/v1/telemetry/environment/edge-livingroom/synthetic"
assert re.fullmatch(r"telemetry/environment/source-[0-9a-f]{16}", value["sanitized_topic"])
assert re.fullmatch(r"[0-9a-f]{64}", value["trace_sha256"])
assert isinstance(value["synthetic_value"], (int, float)) and not isinstance(value["synthetic_value"], bool)
assert value["influx_measurement"] == "selected_telemetry"
PY
install -d -o root -g root -m 0700 "$(dirname "$disposition")"
temporary=$disposition.new
python3 - "$acceptance_hash" >"$temporary" <<'PY'
from datetime import datetime, timezone
import json
import sys
print(json.dumps({
    "schema": "edsys.edcore-automation.client-disposition.v1",
    "identity": "edsys-edge-livingroom",
    "disposition": "unused-not-delivered",
    "ingestion_acceptance_sha256": sys.argv[1],
    "recorded_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
}, sort_keys=True))
PY
chown root:root "$temporary"
chmod 0600 "$temporary"
mv "$temporary" "$disposition"
printf 'Recorded unused/not-delivered disposition for edsys-edge-livingroom.\n'
