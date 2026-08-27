#!/usr/bin/env bash
set -Eeuo pipefail

readonly secret_root=/etc/edsys-secrets/edcore-automation
readonly escrow_config=/etc/edsys-escrow
readonly recipient_file=$escrow_config/edcore-automation-recipient.txt
readonly escrow_root=/var/backups/edcore-automation-secret-escrow

[[ ${EUID} -eq 0 ]] || { echo "Run as root on edcore-automation." >&2; exit 1; }
[[ $(hostname -s) == edcore-automation ]] || { echo "Refusing secret escrow on the wrong guest." >&2; exit 1; }
[[ $# -eq 1 && $1 == --create ]] || { echo "Usage: $0 --create" >&2; exit 64; }
/usr/local/sbin/edsys-automation-source-guard --runtime
command -v age >/dev/null || { echo "age is required." >&2; exit 1; }
[[ ! -e /etc/edsys-secrets/edcore-automation-escrow/identity.txt && \
   ! -L /etc/edsys-secrets/edcore-automation-escrow/identity.txt ]] || {
  echo "The 9950x-only age identity is present on the guest; refusing escrow." >&2
  exit 1
}

[[ -d $secret_root && ! -L $secret_root ]] || { echo "Runtime secret root is unsafe or absent." >&2; exit 1; }
unsafe=$(find "$secret_root" -xdev \( -type l -o \! -type d \! -type f \) -print -quit)
[[ -z $unsafe ]] || { echo "Symlink or special file in secret tree: $unsafe" >&2; exit 1; }
recovery_files=(
  pki/ca/ca.key pki/ca/ca.crt pki/ca/ca.srl
  pki/servers/mosquitto.key pki/servers/mosquitto.crt
  pki/servers/node-red.key pki/servers/node-red.crt
  pki/servers/influxdb.key pki/servers/influxdb.crt
  pki/clients/mqtt-health.key pki/clients/mqtt-health.crt
  pki/clients/nodered.key pki/clients/nodered.crt
  pki/clients/automation-runtime.key pki/clients/automation-runtime.crt
  pki/clients/telegraf.key pki/clients/telegraf.crt
  pki/clients/event-replay.key pki/clients/event-replay.crt
  pki/clients/command-audit.key pki/clients/command-audit.crt
  pki/clients/homeassistant.key pki/clients/homeassistant.crt
  pki/clients/frigate.key pki/clients/frigate.crt
  pki/clients/edsys-edge-livingroom.key pki/clients/edsys-edge-livingroom.crt
  node-red/admin_password node-red/admin_password_hash node-red/credential_secret
  influxdb/admin_password influxdb/admin_token influxdb/telegraf_token influxdb/grafana_token
)
for recovery_file in "${recovery_files[@]}"; do
  [[ -s "$secret_root/$recovery_file" ]] || {
    echo "Complete recovery escrow requires recovery file: $recovery_file" >&2
    exit 1
  }
done
[[ -f $recipient_file && ! -L $recipient_file && $(stat -c '%u:%g:%a' "$recipient_file") == 0:0:644 ]] || {
  echo "Install the 9950x-held age recipient as root:root 0644 at $recipient_file." >&2
  exit 1
}
mapfile -t recipients < <(grep -Ev '^[[:space:]]*(#|$)' "$recipient_file")
[[ ${#recipients[@]} -eq 1 && ${recipients[0]} =~ ^age1[0-9a-z]{20,}$ ]] || {
  echo "Recipient file must contain exactly one native age recipient." >&2
  exit 1
}

install -d -o root -g root -m 0700 "$escrow_root"
run_id=$(date -u +%Y%m%dT%H%M%SZ)
final=$escrow_root/edcore-automation-secrets-$run_id.tar.age
temporary=$final.new
[[ ! -e $final && ! -e $temporary ]] || { echo "Escrow run ID collision." >&2; exit 1; }
umask 077
tar --format=ustar --numeric-owner --owner=0 --group=0 \
  -C /etc/edsys-secrets -cf - edcore-automation \
  | age -r "${recipients[0]}" -o "$temporary"
[[ -s $temporary ]] || { echo "Encrypted escrow output is empty." >&2; exit 1; }
grep -aq '^age-encryption.org/v1' "$temporary" || { echo "Output is not a native age archive." >&2; exit 1; }
chown root:root "$temporary"
chmod 0600 "$temporary"
mv "$temporary" "$final"
ln -sfn "$(basename "$final")" "$escrow_root/current"
sha256sum "$final" | awk '{print $1}'
