#!/usr/bin/env bash
set -euo pipefail

guest="${NETBOX_SSH_ALIAS:-netbox}"
base_url="${NETBOX_HEALTHCHECKS_BASE_URL:-http://192.168.50.50:3014}"
local_dir="${HEALTHCHECKS_ENV_DIR:-/etc/edsys-healthchecks}"

case "$base_url" in
  http://192.168.50.50:3014|https://192.168.50.50:3014) ;;
  *)
    echo "Refusing non-allowlisted NetBox Healthchecks base URL." >&2
    exit 1
    ;;
esac

declare -A files=(
  [edsys-netbox-backup.env]=backup.env
  [edsys-netbox-restore-test.env]=restore-test.env
)

ssh -o BatchMode=yes "$guest" \
  'sudo install -d -o root -g root -m 0700 /etc/edsys-secrets/netbox/healthchecks'

for source_name in "${!files[@]}"; do
  destination_name="${files[$source_name]}"
  source_file="$local_dir/$source_name"
  sudo test -s "$source_file" || {
    echo "Missing local Healthchecks environment file: $source_file" >&2
    exit 1
  }

  ping_path="$(sudo awk -F= '/^HC_PING_URL=/{u=substr($0,index($0,"=")+1); sub(/^https?:\/\/[^/]+/,"",u); print u}' "$source_file")"
  [[ "$ping_path" == /ping/* ]] || {
    echo "Invalid Healthchecks ping path in $source_file" >&2
    exit 1
  }

  tmp="$(mktemp)"
  remote_tmp="/tmp/edsys-netbox-healthcheck-$RANDOM.env"
  trap 'rm -f "$tmp"' EXIT
  printf 'HC_PING_URL=%s%s\n' "$base_url" "$ping_path" >"$tmp"
  chmod 0600 "$tmp"
  scp -q "$tmp" "$guest:$remote_tmp"
  ssh -o BatchMode=yes "$guest" \
    "sudo install -o root -g root -m 0600 '$remote_tmp' '/etc/edsys-secrets/netbox/healthchecks/$destination_name' && rm -f '$remote_tmp'"
  rm -f "$tmp"
  trap - EXIT
done

echo "Installed root-private NetBox guest heartbeat URLs without printing their tokens."
