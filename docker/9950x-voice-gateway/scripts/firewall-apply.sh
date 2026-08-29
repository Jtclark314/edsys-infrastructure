#!/usr/bin/env bash
set -euo pipefail

echo "The EdSys Voice Gateway firewall was retired with the former EdCore Home Assistant deployment." >&2
exit 2

readonly table_name="edsys_voice_gateway"
readonly rules_file="/srv/edsys/edsys-infrastructure/docker/9950x-voice-gateway/firewall/edsys-voice-gateway.nft"

[[ ${EUID} -eq 0 ]] || { echo "run as root" >&2; exit 1; }
[[ -r ${rules_file} ]] || { echo "missing firewall rules" >&2; exit 1; }

if ! nft list table inet "${table_name}" >/dev/null 2>&1; then
  nft add table inet "${table_name}"
fi
nft flush table inet "${table_name}"
nft -f "${rules_file}"

nft list table inet "${table_name}" | grep -Fq 'ct original proto-dst 8055'
