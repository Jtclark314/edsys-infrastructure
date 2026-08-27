#!/usr/bin/env bash
set -Eeuo pipefail

readonly vmid=324
readonly name=edcore-automation
readonly address=192.168.50.82/24
readonly address_ip=192.168.50.82
readonly gateway=192.168.50.1
readonly bridge=vmbr0
readonly storage=local-lvm
readonly image=/var/lib/vz/template/iso/noble-server-cloudimg-amd64-20260725.img
readonly checksum_file=/var/lib/vz/template/iso/noble-current-SHA256SUMS
readonly disk_gib=120
readonly min_free_gib=140
readonly cores=6
readonly memory_mib=8192
readonly startup_order=50

apply=false
if [[ $# -gt 1 || (${1:-} != "" && ${1:-} != --apply) ]]; then
  echo "Usage: $0 [--apply]" >&2
  exit 2
fi
[[ ${1:-} == --apply ]] && apply=true

[[ ${EUID} -eq 0 ]] || { echo "Run as root on pve-edcore." >&2; exit 1; }
[[ $(hostname -s) == pve-edcore ]] || { echo "Refusing to run outside pve-edcore." >&2; exit 1; }
for command in awk getent grep ip pvecm pvesm qm sed sha256sum sort; do
  command -v "$command" >/dev/null 2>&1 || { echo "Missing required command: $command" >&2; exit 1; }
done
pvecm status | grep -Eq '^Quorate:[[:space:]]+Yes$' || { echo "Cluster is not quorate." >&2; exit 1; }
if qm status "$vmid" >/dev/null 2>&1; then
  echo "VMID $vmid already exists; refusing to modify it." >&2
  exit 1
fi
if grep -R -F -l --exclude='*.lock' "$address_ip" /etc/pve/nodes /etc/pve/qemu-server /etc/pve/lxc 2>/dev/null | grep -q .; then
  echo "$address_ip already appears in a Proxmox guest configuration." >&2
  exit 1
fi
if ping -n -c 2 -W 1 "$address_ip" >/dev/null 2>&1; then
  echo "$address_ip answered ICMP; refusing to provision." >&2
  exit 1
fi
neighbor_state=$(ip neigh show to "$address_ip" dev "$bridge" 2>/dev/null || true)
if grep -Eq 'lladdr|REACHABLE|STALE|DELAY|PROBE|PERMANENT|NOARP' <<<"$neighbor_state"; then
  echo "Neighbor state indicates $address_ip is already present: $neighbor_state" >&2
  exit 1
fi
if getent ahostsv4 "$name" 2>/dev/null | awk '{print $1}' | grep -qv '^$'; then
  echo "$name already resolves; reconcile DNS before provisioning." >&2
  exit 1
fi

[[ -f "$image" ]] || { echo "Cloud image missing: $image" >&2; exit 1; }
[[ -f "$checksum_file" ]] || { echo "Checksum manifest missing: $checksum_file" >&2; exit 1; }
expected=$(awk '$2 == "*noble-server-cloudimg-amd64.img" {print $1; exit}' "$checksum_file")
actual=$(sha256sum "$image" | awk '{print $1}')
[[ ${#expected} -eq 64 && "$actual" == "$expected" ]] || {
  echo "Cloud image SHA-256 does not match noble-current-SHA256SUMS." >&2
  exit 1
}

read -r storage_name storage_type storage_status _total_kib _used_kib available_kib _percent \
  < <(pvesm status --storage "$storage" | awk -v name="$storage" '$1 == name {print; exit}')
[[ "$storage_name" == "$storage" && "$storage_type" == lvmthin && "$storage_status" == active ]] || {
  echo "$storage is missing, inactive, or not lvmthin." >&2
  exit 1
}
[[ "$available_kib" =~ ^[0-9]+$ ]] || { echo "Unable to parse $storage available KiB." >&2; exit 1; }
minimum_kib=$((min_free_gib * 1024 * 1024))
(( available_kib >= minimum_kib )) || {
  echo "$storage has less than ${min_free_gib} GiB physically available; refusing the ${disk_gib} GiB thin disk." >&2
  exit 1
}

cat <<EOF
Preflight passed for the dedicated EdCore automation VM:
  host:            pve-edcore
  VMID/name:       $vmid / $name
  network:         $address via $gateway on $bridge
  compute:         $cores vCPU / $memory_mib MiB fixed initial RAM
  disk:            ${disk_gib}G on $storage
  startup order:   $startup_order
  image/hash:      verified Ubuntu Noble cloud image
  physical free:   $((available_kib / 1024 / 1024)) GiB before allocation
EOF

if ! $apply; then
  echo "Dry-run only; rerun with --apply after reviewing this exact plan."
  exit 0
fi

keys=$(mktemp)
created=false
complete=false
cleanup() {
  local rc=$?
  rm -f "$keys"
  if (( rc != 0 )) && $created && ! $complete; then
    echo "Provisioning failed after creating VMID $vmid; removing only that newly-created incomplete VM." >&2
    qm stop "$vmid" --skiplock 1 >/dev/null 2>&1 || true
    qm destroy "$vmid" --purge 1 --destroy-unreferenced-disks 1 >/dev/null 2>&1 || true
  fi
  exit "$rc"
}
trap cleanup EXIT

qm cloudinit dump 323 user \
  | sed -n 's/^[[:space:]]*- \(ssh-[^[:space:]]\+[[:space:]].*\)$/\1/p' >"$keys"
if [[ -f /root/.ssh/authorized_keys ]]; then
  sed -n 's/^.*\(ssh-\(ed25519\|rsa\|ecdsa\)-[^[:space:]]\+[[:space:]].*\)$/\1/p' \
    /root/.ssh/authorized_keys >>"$keys"
fi
sort -u -o "$keys" "$keys"
[[ -s "$keys" ]] || { echo "No trusted public SSH keys were found." >&2; exit 1; }

qm create "$vmid" \
  --name "$name" \
  --description 'Dedicated EdSys production automation fabric; HAOS remains device/state authority and final actuation boundary.' \
  --tags 'edsys;automation;production' \
  --machine q35 \
  --bios ovmf \
  --ostype l26 \
  --cpu host \
  --sockets 1 \
  --cores "$cores" \
  --memory "$memory_mib" \
  --balloon 0 \
  --scsihw virtio-scsi-single \
  --agent enabled=1,fstrim_cloned_disks=1 \
  --net0 "virtio,bridge=$bridge,firewall=1" \
  --onboot 1 \
  --startup "order=$startup_order,up=180,down=180" \
  --serial0 socket \
  --vga serial0 \
  --tablet 0
created=true

qm set "$vmid" --efidisk0 "$storage:1,efitype=4m,ms-cert=2023k,pre-enrolled-keys=1"
qm importdisk "$vmid" "$image" "$storage" --format raw
imported=$(qm config "$vmid" | awk -F': ' '/^unused0:/{print $2; exit}')
[[ -n "$imported" ]] || { echo "Imported disk was not registered as unused0." >&2; exit 1; }
qm set "$vmid" --scsi0 "$imported,discard=on,iothread=1,ssd=1"
qm set "$vmid" --delete unused0
qm resize "$vmid" scsi0 "${disk_gib}G"
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

config=$(qm config "$vmid")
grep -qx "cores: $cores" <<<"$config"
grep -qx "memory: $memory_mib" <<<"$config"
grep -qx 'balloon: 0' <<<"$config"
grep -Eq "^scsi0: .*size=${disk_gib}G" <<<"$config"
grep -qx "ipconfig0: ip=$address,gw=$gateway" <<<"$config"
grep -qx "startup: order=$startup_order,up=180,down=180" <<<"$config"
grep -qx 'onboot: 1' <<<"$config"

qm start "$vmid"
complete=true
printf 'VMID %s started with the reviewed fixed initial allocation. Guest bootstrap remains required.\n' "$vmid"
printf '%s\n' "$config" | grep -E '^(name|cores|memory|balloon|scsi0|ipconfig0|onboot|startup|tags):'
