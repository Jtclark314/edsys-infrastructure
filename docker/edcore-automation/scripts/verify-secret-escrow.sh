#!/usr/bin/env bash
set -Eeuo pipefail

PATH=/usr/sbin:/usr/bin:/sbin:/bin
export PATH
unset PYTHONHOME PYTHONPATH

readonly identity=/etc/edsys-secrets/edcore-automation-escrow/identity.txt
readonly installed_path=/usr/local/sbin/edsys-automation-verify-secret-escrow
readonly archive_helper=/usr/local/libexec/edsys-automation-secret-escrow-archive.py
readonly max_archive_bytes=$((32 * 1024 * 1024))
archive=${1:-}

[[ ${EUID} -eq 0 ]] || { echo "Run as root on the 9950x recovery host." >&2; exit 1; }
[[ $(readlink -e -- "${BASH_SOURCE[0]}") == "$installed_path" && \
   $(stat -c '%u:%g:%a:%h' "$installed_path") == 0:0:755:1 ]] || {
  echo "Install this verifier root:root 0755 at $installed_path before root execution." >&2
  exit 1
}
current=$installed_path
while :; do
  [[ $(stat -c '%u:%g' "$current") == 0:0 ]] || { echo "Unsafe verifier path owner: $current" >&2; exit 1; }
  mode=$(stat -c '%a' "$current")
  (( (8#$mode & 8#022) == 0 )) || { echo "Writable verifier path component: $current" >&2; exit 1; }
  [[ $current == / ]] && break
  current=$(dirname "$current")
done
[[ $(hostname -s) != edcore-automation ]] || { echo "The escrow identity must never be present on edcore-automation." >&2; exit 1; }
[[ $# -eq 1 && $archive == /* && \
   ${archive##*/} =~ ^edcore-automation-secrets-[0-9]{8}T[0-9]{6}Z\.tar\.age$ && \
   -f $archive && ! -L $archive ]] || {
  echo "Usage: $0 /absolute/path/to/edcore-automation-secrets-*.tar.age" >&2
  exit 64
}
[[ $(stat -c '%u:%g:%a:%h' "$archive") == 0:0:600:1 && \
   $(stat -c '%s' "$archive") -ge 1 && $(stat -c '%s' "$archive") -le $max_archive_bytes ]] || {
  echo "Encrypted archive must be a bounded root:root 0600 single-link regular file." >&2
  exit 1
}
[[ -f $identity && ! -L $identity && $(stat -c '%u:%g:%a:%h' "$identity") == 0:0:600:1 ]] || {
  echo "9950x age identity must be root:root 0600 at $identity." >&2
  exit 1
}
[[ -f $archive_helper && ! -L $archive_helper && \
   $(stat -c '%u:%g:%a:%h' "$archive_helper") == 0:0:644:1 ]] || {
  echo "Install the reviewed archive helper root:root 0644 at $archive_helper." >&2
  exit 1
}
command -v age >/dev/null || { echo "age is required." >&2; exit 1; }
command -v python3 >/dev/null || { echo "python3 is required." >&2; exit 1; }

for protected_path in "$archive" "$identity" "$archive_helper"; do
  [[ $(readlink -e -- "$protected_path") == "$protected_path" ]] || {
    echo "Recovery path contains a link or noncanonical component: $protected_path" >&2
    exit 1
  }
  current=$protected_path
  while :; do
    [[ $(stat -c '%u:%g' "$current") == 0:0 ]] || { echo "Unsafe recovery path owner: $current" >&2; exit 1; }
    mode=$(stat -c '%a' "$current")
    (( (8#$mode & 8#022) == 0 )) || { echo "Writable recovery path component: $current" >&2; exit 1; }
    [[ $current == / ]] && break
    current=$(dirname "$current")
  done
done

test_root=$(mktemp -d /dev/shm/edcore-automation-escrow.XXXXXX)
cleanup() { rm -rf -- "$test_root"; }
trap cleanup EXIT
chmod 0700 "$test_root"
plaintext_tar=$test_root/archive.tar
extract_root=$test_root/extracted

# Decrypt once into a root-private, size-limited regular file. The public age
# recipient is not archive authentication, so no tar extractor runs until the
# installed helper has inspected every member and rejected unsafe metadata.
umask 077
( ulimit -f 65536; exec age -d -i "$identity" "$archive" ) >"$plaintext_tar"
[[ -f $plaintext_tar && ! -L $plaintext_tar && \
   $(stat -c '%u:%g:%a:%h' "$plaintext_tar") == 0:0:600:1 && \
   $(stat -c '%s' "$plaintext_tar") -ge 1 && $(stat -c '%s' "$plaintext_tar") -le $max_archive_bytes ]] || {
  echo "Decrypted tar exceeded its fixed regular-file boundary." >&2
  exit 1
}
python3 -I -B "$archive_helper" "$plaintext_tar" "$extract_root"
restored=$extract_root/edcore-automation

required=(
  pki/ca/ca.key pki/ca/ca.crt pki/ca/ca.srl
  pki/servers/mosquitto.key pki/servers/mosquitto.crt
  pki/servers/node-red.key pki/servers/node-red.crt
  pki/servers/influxdb.key pki/servers/influxdb.crt
  pki/clients/homeassistant.key pki/clients/homeassistant.crt
  pki/clients/frigate.key pki/clients/frigate.crt
  pki/clients/edsys-edge-livingroom.key pki/clients/edsys-edge-livingroom.crt
  pki/clients/mqtt-health.key pki/clients/mqtt-health.crt
  pki/clients/nodered.key pki/clients/nodered.crt
  pki/clients/automation-runtime.key pki/clients/automation-runtime.crt
  pki/clients/telegraf.key pki/clients/telegraf.crt
  pki/clients/event-replay.key pki/clients/event-replay.crt
  pki/clients/command-audit.key pki/clients/command-audit.crt
  node-red/admin_password node-red/admin_password_hash node-red/credential_secret
  influxdb/admin_password influxdb/admin_token influxdb/telegraf_token influxdb/grafana_token
)
for relative in "${required[@]}"; do
  [[ -s "$restored/$relative" ]] || { echo "Escrow is missing recovery file: $relative" >&2; exit 1; }
done
openssl verify -CAfile "$restored/pki/ca/ca.crt" "$restored"/pki/servers/*.crt "$restored"/pki/clients/*.crt >/dev/null
for certificate in "$restored"/pki/servers/*.crt "$restored"/pki/clients/*.crt; do
  private_key=${certificate%.crt}.key
  key_hash=$(openssl pkey -in "$private_key" -pubout 2>/dev/null | sha256sum | awk '{print $1}')
  certificate_hash=$(openssl x509 -in "$certificate" -pubkey -noout | sha256sum | awk '{print $1}')
  [[ $key_hash == "$certificate_hash" ]] || {
    echo "Escrow private key does not match certificate: ${certificate#"$restored"/}" >&2
    exit 1
  }
done
ca_key_hash=$(openssl pkey -in "$restored/pki/ca/ca.key" -pubout 2>/dev/null | sha256sum | awk '{print $1}')
ca_cert_hash=$(openssl x509 -in "$restored/pki/ca/ca.crt" -pubkey -noout | sha256sum | awk '{print $1}')
[[ $ca_key_hash == "$ca_cert_hash" ]] || { echo "Escrow CA key does not match its certificate." >&2; exit 1; }

archive_hash=$(sha256sum "$archive" | awk '{print $1}')
python3 -I -B - "$(basename "$archive")" "$archive_hash" <<'PY'
from datetime import datetime, timezone
import json
import socket
import sys

print(json.dumps({
    "schema": "edsys.edcore-automation.secret-escrow-acceptance.v1",
    "archive_name": sys.argv[1],
    "archive_sha256": sys.argv[2],
    "tested_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    "tested_on": socket.gethostname().split(".", 1)[0],
}, sort_keys=True))
PY
