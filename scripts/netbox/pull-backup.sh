#!/usr/bin/env bash
set -euo pipefail

remote=netbox
host_key_alias=192.168.50.81
destination_root=/srv/edsys-backup/staging/netbox
run_id=$(sudo -H -u jeremy -- ssh -o BatchMode=yes -o StrictHostKeyChecking=yes -o "HostKeyAlias=$host_key_alias" "$remote" \
  'sudo basename "$(sudo readlink -f /var/backups/netbox/current)"')
[[ "$run_id" =~ ^20[0-9]{6}T[0-9]{6}Z$ ]] || { echo "Invalid remote NetBox backup identifier" >&2; exit 1; }

staging="$destination_root/.staging-$run_id"
final="$destination_root/$run_id"
install -d -o root -g root -m 0750 "$destination_root"
rm -rf "$staging"
install -d -o root -g root -m 0700 "$staging"

rsync -a --delete --rsync-path='sudo rsync' \
  -e "sudo -H -u jeremy -- ssh -o BatchMode=yes -o StrictHostKeyChecking=yes -o HostKeyAlias=$host_key_alias" \
  "$remote:/var/backups/netbox/$run_id/" "$staging/"

(cd "$staging" && sha256sum -c SHA256SUMS)
chmod -R go-rwx "$staging"
if [[ -d "$final" ]]; then
  rm -rf "$staging"
else
  mv "$staging" "$final"
fi
ln -sfn "$run_id" "$destination_root/current"
find "$destination_root" -mindepth 1 -maxdepth 1 -type d -name '20????????T??????Z' -mtime +35 -print0 \
  | xargs -0r --no-run-if-empty rm -rf --
printf 'Verified NetBox backup pull: %s\n' "$final"
