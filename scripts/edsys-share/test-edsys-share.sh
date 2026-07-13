#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
tmp="$(mktemp -d)"
trap 'rm -rf "${tmp}"' EXIT

scripts=(
  edsys-share-mount-check
  edsys-share-tailnet-guard
  edsys-share-gdrive-sync
  edsys-share-gdrive-verify
  edsys-share-gdrive-prune
  edsys-share-gdrive-restore
  install-9950x.sh
  install-rclone-1.74.4.sh
)
for script in "${scripts[@]}"; do
  [[ -x "${SCRIPT_DIR}/${script}" ]] || { echo "Script is not executable: ${script}" >&2; exit 1; }
  bash -n "${SCRIPT_DIR}/${script}"
done
for source_file in "${SCRIPT_DIR}/README.md" "${SCRIPT_DIR}/edsys-share.conf.example" "${SCRIPT_DIR}/samba-share.conf" "${SCRIPT_DIR}"/systemd/*; do
  [[ ! -x "${source_file}" ]] || { echo "Data/unit source is unexpectedly executable: ${source_file}" >&2; exit 1; }
done
cc -std=c11 -O2 -Wall -Wextra -Werror "${SCRIPT_DIR}/edsys-share-mount-check-smb.c" -o "${tmp}/edsys-share-mount-check-smb"
"${tmp}/edsys-share-mount-check-smb"
if command -v shellcheck >/dev/null; then
  shellcheck "${scripts[@]/#/${SCRIPT_DIR}/}"
fi

cat >"${tmp}/smb.conf" <<EOF
[global]
   interfaces = lo enp7s0
   bind interfaces only = yes
   hosts allow = 127.0.0.1 192.168.50.0/24
   hosts deny = 0.0.0.0/0

$(cat "${SCRIPT_DIR}/samba-share.conf")
EOF
testparm -s "${tmp}/smb.conf" >/dev/null

cp "${SCRIPT_DIR}"/systemd/*.service "${SCRIPT_DIR}"/systemd/*.timer "${tmp}/"
sed 's/@TAILNET_LISTEN_IP@/100.87.137.47/g' "${SCRIPT_DIR}/systemd/edsys-share-tailnet-smb.socket.in" >"${tmp}/edsys-share-tailnet-smb.socket"
systemd-analyze verify "${tmp}"/*.service "${tmp}"/*.timer "${tmp}"/*.socket

if rg -n -i 'tusd|tcp 3045|CourierMedia|courier-reader|edsys-courier' \
  "${SCRIPT_DIR}" --glob '!README.md' --glob '!test-edsys-share.sh' --glob '!install-9950x.sh'; then
  echo "Retired application identifier found in active source" >&2
  exit 1
fi

grep -Fq 'path = /EdSys-Share' "${SCRIPT_DIR}/samba-share.conf"
grep -Fq 'ListenStream=@TAILNET_LISTEN_IP@:445' "${SCRIPT_DIR}/systemd/edsys-share-tailnet-smb.socket.in"
echo "EdSys Share source validation passed."
