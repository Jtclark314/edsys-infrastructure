#!/usr/bin/env bash
set -euo pipefail

if [[ ${EUID} -ne 0 ]]; then
  echo "Run as root on the NetBox VM." >&2
  exit 1
fi

auth_file=/etc/edsys-secrets/netbox/tailscale_auth_key
if [[ ! -s "$auth_file" ]]; then
  echo "Missing one-time auth key at $auth_file; no Tailnet mutation was attempted." >&2
  echo "Create a tagged, reusable-or-one-time key in the private Tailscale admin console, store it root-only, then rerun." >&2
  exit 2
fi

tailscale up --auth-key="file:$auth_file" --hostname=netbox --ssh=false --accept-dns=false --accept-routes=false
shred -u "$auth_file"
tailscale serve --bg --yes http://127.0.0.1:8080
systemctl enable --now edsys-netbox-tailscale-serve.service
tailscale status --json | jq '{BackendState, Self: {HostName: .Self.HostName, DNSName: .Self.DNSName, TailscaleIPs: .Self.TailscaleIPs}}'
tailscale serve status --json
