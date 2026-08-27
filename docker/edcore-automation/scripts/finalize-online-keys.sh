#!/usr/bin/env bash
set -Eeuo pipefail

readonly secret_root=/etc/edsys-secrets/edcore-automation
readonly escrow_config=/etc/edsys-escrow
readonly escrow_root=/var/backups/edcore-automation-secret-escrow
readonly acceptance=$escrow_config/edcore-automation-accepted.json
readonly delivery_root=$escrow_config/client-delivery

[[ ${EUID} -eq 0 ]] || { echo "Run as root on edcore-automation." >&2; exit 1; }
[[ $(hostname -s) == edcore-automation ]] || { echo "Refusing key finalization on the wrong guest." >&2; exit 1; }
[[ $# -eq 1 && $1 == --apply ]] || { echo "Usage: $0 --apply" >&2; exit 64; }
/usr/local/sbin/edsys-automation-source-guard --runtime
[[ -f $acceptance && ! -L $acceptance && $(stat -c '%u:%g:%a' "$acceptance") == 0:0:600 ]] || {
  echo "A 9950x cold-restore acceptance is required at $acceptance." >&2
  exit 1
}
archive=$(readlink -e "$escrow_root/current")
[[ -f $archive && ! -L $archive && ${archive##*/} == edcore-automation-secrets-*.tar.age ]] || {
  echo "Accepted encrypted escrow archive is absent." >&2
  exit 1
}
python3 - "$acceptance" "$(basename "$archive")" "$(sha256sum "$archive" | awk '{print $1}')" <<'PY'
import json
import re
import sys

value = json.load(open(sys.argv[1], encoding="utf-8"))
assert set(value) == {"schema", "archive_name", "archive_sha256", "tested_utc", "tested_on"}
assert value["schema"] == "edsys.edcore-automation.secret-escrow-acceptance.v1"
assert value["archive_name"] == sys.argv[2]
assert value["archive_sha256"] == sys.argv[3]
assert re.fullmatch(r"[0-9a-f]{64}", value["archive_sha256"])
assert isinstance(value["tested_on"], str) and value["tested_on"] and value["tested_on"] != "edcore-automation"
PY

for identity in homeassistant frigate; do
  marker=$delivery_root/$identity.json
  cert=$secret_root/pki/clients/$identity.crt
  [[ -f $marker && ! -L $marker && $(stat -c '%u:%g:%a' "$marker") == 0:0:600 ]] || {
    echo "Client delivery acceptance is absent for $identity." >&2
    exit 1
  }
  python3 - "$marker" "$identity" "$(sha256sum "$cert" | awk '{print $1}')" <<'PY'
import json
import sys
value = json.load(open(sys.argv[1], encoding="utf-8"))
assert set(value) == {"schema", "identity", "certificate_sha256", "accepted_utc"}
assert value["schema"] == "edsys.edcore-automation.client-delivery.v1"
assert value["identity"] == sys.argv[2]
assert value["certificate_sha256"] == sys.argv[3]
PY
done

edge_disposition=$escrow_config/client-disposition/edsys-edge-livingroom.json
edge_ingestion=$escrow_config/client-disposition/edsys-edge-livingroom-ingestion.json
[[ -f $edge_ingestion && ! -L $edge_ingestion && \
   $(stat -c '%u:%g:%a' "$edge_ingestion") == 0:0:600 ]] || {
  echo "Synthetic ingestion acceptance is absent for edsys-edge-livingroom." >&2
  exit 1
}
[[ -f $edge_disposition && ! -L $edge_disposition && \
   $(stat -c '%u:%g:%a' "$edge_disposition") == 0:0:600 ]] || {
  echo "Explicit unused disposition is absent for edsys-edge-livingroom." >&2
  exit 1
}
python3 - "$edge_disposition" "$edge_ingestion" \
  "$(sha256sum "$secret_root/pki/clients/edsys-edge-livingroom.crt" | awk '{print $1}')" <<'PY'
import hashlib
import json
import re
import sys
value = json.load(open(sys.argv[1], encoding="utf-8"))
ingestion_raw = open(sys.argv[2], "rb").read()
ingestion = json.loads(ingestion_raw)
assert set(value) == {"schema", "identity", "disposition", "ingestion_acceptance_sha256", "recorded_utc"}
assert value["schema"] == "edsys.edcore-automation.client-disposition.v1"
assert value["identity"] == "edsys-edge-livingroom"
assert value["disposition"] == "unused-not-delivered"
assert value["ingestion_acceptance_sha256"] == hashlib.sha256(ingestion_raw).hexdigest()
assert set(ingestion) == {
    "schema", "identity", "certificate_sha256", "run_id", "source_topic",
    "sanitized_topic", "trace_sha256", "synthetic_value",
    "influx_measurement", "accepted_utc",
}
assert ingestion["schema"] == "edsys.edcore-automation.synthetic-ingestion-acceptance.v1"
assert ingestion["identity"] == "edsys-edge-livingroom"
assert ingestion["certificate_sha256"] == sys.argv[3]
assert re.fullmatch(r"edge-[0-9]{14}-[0-9a-f]{6}", ingestion["run_id"])
assert ingestion["source_topic"] == "edsys/v1/telemetry/environment/edge-livingroom/synthetic"
assert re.fullmatch(r"telemetry/environment/source-[0-9a-f]{16}", ingestion["sanitized_topic"])
assert re.fullmatch(r"[0-9a-f]{64}", ingestion["trace_sha256"])
assert ingestion["influx_measurement"] == "selected_telemetry"
PY

# Fixed paths only. The accepted encrypted archive has already proved these
# keys recoverable off-guest; they are not required by any broker container.
rm -f \
  "$secret_root/pki/ca/ca.key" "$secret_root/pki/ca/ca.srl" \
  "$secret_root/pki/clients/homeassistant.key" \
  "$secret_root/pki/clients/frigate.key" \
  "$secret_root/pki/clients/edsys-edge-livingroom.key"
finalized=$escrow_config/online-keys-finalized.json
python3 - "$(basename "$archive")" "$(sha256sum "$archive" | awk '{print $1}')" >"$finalized.new" <<'PY'
from datetime import datetime, timezone
import json
import sys
print(json.dumps({
    "schema": "edsys.edcore-automation.online-key-finalization.v1",
    "archive_name": sys.argv[1],
    "archive_sha256": sys.argv[2],
    "removed": ["automation-ca", "homeassistant", "frigate", "edsys-edge-livingroom"],
    "finalized_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
}, sort_keys=True))
PY
chown root:root "$finalized.new"
chmod 0600 "$finalized.new"
mv "$finalized.new" "$finalized"
printf 'Online CA and external-client private keys removed after escrow and delivery acceptance.\n'
