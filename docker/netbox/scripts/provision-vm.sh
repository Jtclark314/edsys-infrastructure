#!/usr/bin/env bash
set -euo pipefail

vmid=323
name=netbox
address=192.168.50.81/24
gateway=192.168.50.1
image=/var/lib/vz/template/iso/noble-server-cloudimg-amd64-20260725.img
storage=local-lvm

if [[ ${1:-} != --apply ]]; then
  cat <<EOF
Dry-run only. This script provisions VMID $vmid ($name) on the current Proxmox node.
Target: $address, 4 vCPU, 8192 MiB RAM, 128 GiB $storage, startup order 40.
Run with --apply only after verifying VMID/IP/storage/quorum.
EOF
  exit 0
fi

[[ $(hostname -s) == pve-edcore ]] || { echo "Run only on pve-edcore." >&2; exit 1; }
pvecm status | grep -q 'Quorate:.*Yes' || { echo "Cluster is not quorate." >&2; exit 1; }
if qm status "$vmid" >/dev/null 2>&1; then
  echo "VMID $vmid already exists; refusing to modify it." >&2
  exit 1
fi
[[ -f "$image" ]] || { echo "Cloud image missing: $image" >&2; exit 1; }
expected=$(awk '$2 == "*noble-server-cloudimg-amd64.img" {print $1}' "$(dirname "$image")/noble-current-SHA256SUMS")
actual=$(sha256sum "$image" | awk '{print $1}')
[[ -n "$expected" && "$actual" == "$expected" ]] || { echo "Cloud image SHA-256 does not match noble-current-SHA256SUMS." >&2; exit 1; }
echo "$image: OK"

keys=$(mktemp)
trap 'rm -f "$keys"' EXIT
qm cloudinit dump 322 user | sed -n 's/^[[:space:]]*- \(ssh-[^[:space:]]\+[[:space:]].*\)$/\1/p' >"$keys"
if [[ -f /root/.ssh/authorized_keys ]]; then
  sed -n 's/^.*\(ssh-\(ed25519\|rsa\|ecdsa\)-[^[:space:]]\+[[:space:]].*\)$/\1/p' /root/.ssh/authorized_keys >>"$keys"
fi
sort -u -o "$keys" "$keys"
[[ -s "$keys" ]] || { echo "No trusted public SSH keys were found." >&2; exit 1; }

qm create "$vmid" \
  --name "$name" \
  --description 'EdSys authoritative NetBox operational inventory; Git retains reviewed sanitized exports.' \
  --machine q35 \
  --bios ovmf \
  --ostype l26 \
  --cpu host \
  --sockets 1 \
  --cores 4 \
  --memory 8192 \
  --balloon 0 \
  --scsihw virtio-scsi-single \
  --agent enabled=1,fstrim_cloned_disks=1 \
  --net0 virtio,bridge=vmbr0 \
  --onboot 1 \
  --startup order=40,up=180,down=180 \
  --serial0 socket \
  --vga serial0 \
  --tablet 0

qm set "$vmid" --efidisk0 "$storage:1,efitype=4m,ms-cert=2023k,pre-enrolled-keys=1"
qm importdisk "$vmid" "$image" "$storage" --format raw
imported=$(qm config "$vmid" | awk -F': ' '/^unused0:/{print $2}')
[[ -n "$imported" ]] || { echo "Imported disk was not registered as unused0." >&2; exit 1; }
qm set "$vmid" --scsi0 "$imported,discard=on,iothread=1,ssd=1"
qm set "$vmid" --delete unused0
qm resize "$vmid" scsi0 128G
qm set "$vmid" --ide2 "$storage:cloudinit"
qm set "$vmid" --boot order=scsi0
qm set "$vmid" \
  --ciuser jeremy \
  --ciupgrade 0 \
  --sshkeys "$keys" \
  --ipconfig0 "ip=$address,gw=$gateway" \
  --nameserver '192.168.50.5 192.168.50.6' \
  --searchdomain edsys.local

qm cloudinit update "$vmid"
qm start "$vmid"
qm config "$vmid"
