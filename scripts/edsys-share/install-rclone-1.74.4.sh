#!/usr/bin/env bash
set -Eeuo pipefail

[[ "${EUID}" -eq 0 ]] || { echo "Run with sudo" >&2; exit 2; }
version=1.74.4
archive="rclone-v${version}-linux-amd64.zip"
expected=fe435e0c36228e7c2f116a8701f01127bb1f694005fc11d1f27186c8bca4115d
tmp="$(mktemp -d)"
trap 'rm -rf "${tmp}"' EXIT

curl -fsSLo "${tmp}/${archive}" "https://downloads.rclone.org/v${version}/${archive}"
curl -fsSLo "${tmp}/SHA256SUMS" "https://downloads.rclone.org/v${version}/SHA256SUMS"
published="$(awk -v archive="${archive}" '$2 == archive {print $1}' "${tmp}/SHA256SUMS")"
actual="$(sha256sum "${tmp}/${archive}" | awk '{print $1}')"
[[ "${published}" == "${expected}" && "${actual}" == "${expected}" ]] || {
  echo "rclone checksum validation failed" >&2
  exit 1
}

unzip -q "${tmp}/${archive}" -d "${tmp}"
install -d -m 0755 -o root -g root "/opt/edsys-tools/rclone/v${version}"
install -m 0755 -o root -g root "${tmp}/rclone-v${version}-linux-amd64/rclone" "/opt/edsys-tools/rclone/v${version}/rclone"
ln -sfn "/opt/edsys-tools/rclone/v${version}" /opt/edsys-tools/rclone/current
/opt/edsys-tools/rclone/current/rclone version
