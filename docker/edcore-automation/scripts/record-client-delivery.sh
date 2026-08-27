#!/usr/bin/env bash
set -Eeuo pipefail

readonly secret_root=/etc/edsys-secrets/edcore-automation
readonly delivery_root=/etc/edsys-escrow/client-delivery
identity=${1:-}

[[ ${EUID} -eq 0 ]] || { echo "Run as root on edcore-automation." >&2; exit 1; }
[[ $(hostname -s) == edcore-automation ]] || { echo "Refusing delivery acceptance on the wrong guest." >&2; exit 1; }
[[ $# -eq 2 && $2 == --accepted ]] || { echo "Usage: $0 homeassistant|frigate --accepted" >&2; exit 64; }
case "$identity" in
  homeassistant|frigate) ;;
  *) echo "Identity is not an external-custody client." >&2; exit 64 ;;
esac
/usr/local/sbin/edsys-automation-source-guard --runtime
cert=$secret_root/pki/clients/$identity.crt
key=$secret_root/pki/clients/$identity.key
[[ -s $cert && -s $key && ! -L $cert && ! -L $key ]] || {
  echo "The one-time certificate/key pair is not present for delivery." >&2
  exit 1
}
key_hash=$(openssl pkey -in "$key" -pubout 2>/dev/null | sha256sum | awk '{print $1}')
cert_key_hash=$(openssl x509 -in "$cert" -pubkey -noout | sha256sum | awk '{print $1}')
[[ $key_hash == "$cert_key_hash" ]] || {
  echo "The delivery private key does not match its certificate." >&2
  exit 1
}
install -d -o root -g root -m 0700 "$delivery_root"
temporary=$delivery_root/$identity.json.new
python3 - "$identity" "$(sha256sum "$cert" | awk '{print $1}')" >"$temporary" <<'PY'
from datetime import datetime, timezone
import json
import sys
print(json.dumps({
    "schema": "edsys.edcore-automation.client-delivery.v1",
    "identity": sys.argv[1],
    "certificate_sha256": sys.argv[2],
    "accepted_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
}, sort_keys=True))
PY
chown root:root "$temporary"
chmod 0600 "$temporary"
mv "$temporary" "$delivery_root/$identity.json"
printf 'Recorded explicit delivery acceptance for %s; no key content was displayed.\n' "$identity"
