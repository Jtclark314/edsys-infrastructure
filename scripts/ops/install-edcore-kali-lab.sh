#!/usr/bin/env bash

set -Eeuo pipefail

if ((EUID != 0)); then
  echo "Run this installer as root on EdCore." >&2
  exit 1
fi

public_key_file=""
source_dir=""
prepare_only=false
resume_staged=false

while (($#)); do
  case "$1" in
    --public-key-file)
      public_key_file="${2:-}"
      shift 2
      ;;
    --source-dir)
      source_dir="${2:-}"
      shift 2
      ;;
    --prepare-only)
      prepare_only=true
      shift
      ;;
    --resume-staged)
      resume_staged=true
      shift
      ;;
    *)
      echo "Unknown argument: $1" >&2
      exit 2
      ;;
  esac
done

if [[ "$prepare_only" == true && "$resume_staged" == true ]]; then
  echo "--prepare-only and --resume-staged cannot be combined." >&2
  exit 2
fi

if [[ "$(hostname -s)" != "edcore-workhorse" ]]; then
  echo "Refusing to configure the wrong host: $(hostname -s)" >&2
  exit 1
fi

required_source=(
  kali-lab-preseed.cfg.in
  security-lab-bootstrap-network.xml
  security-lab-network.xml
)
for file in "${required_source[@]}"; do
  if [[ ! -r "${source_dir}/${file}" ]]; then
    echo "Missing reviewed source file: ${source_dir}/${file}" >&2
    exit 1
  fi
done

mapfile -t public_key_lines < <(sed '/^[[:space:]]*$/d' "$public_key_file")
if ((${#public_key_lines[@]} != 1)) ||
  [[ ! "${public_key_lines[0]}" =~ ^ssh-ed25519[[:space:]][A-Za-z0-9+/=]+([[:space:]][A-Za-z0-9@._-]+)?$ ]]; then
  echo "The supplied file must contain exactly one safe ED25519 public key." >&2
  exit 1
fi

if virsh dominfo kali-lab >/dev/null 2>&1; then
  echo "Refusing to overwrite the existing kali-lab domain." >&2
  exit 1
fi

if [[ "$resume_staged" == false ]]; then
  available_bytes="$(df --output=avail -B1 /var/lib | tail -1 | tr -d ' ')"
  if ((available_bytes < 220000000000)); then
    echo "At least 220 GB free is required before staging the lab." >&2
    exit 1
  fi
fi

packages=(
  cockpit-machines
  dnsmasq
  edk2-ovmf
  guestfs-tools
  libguestfs
  libvirt
  qemu-desktop
  swtpm
  virt-install
  virt-viewer
)

run_id="$(date -u +%Y%m%dT%H%M%SZ)"
backup_dir=""
if [[ "$resume_staged" == false ]]; then
  backup_root="/var/backups/edsys-edcore-control"
  backup_dir="${backup_root}/${run_id}-kali-lab"
  install -d -o root -g root -m 0700 "$backup_root" "$backup_dir"
  pacman -Qqe >"${backup_dir}/explicit-packages-before.txt"
  chmod 0600 "${backup_dir}/explicit-packages-before.txt"

  pacman -S --needed --noconfirm "${packages[@]}"

  install -o root -g root -m 0644 /dev/stdin /etc/modules-load.d/edsys-kali-lab.conf <<'EOF'
kvm_amd
vhost_net
EOF
  modprobe kvm_amd
  modprobe vhost_net

  if getent group libvirt >/dev/null; then
    usermod --append --groups libvirt,kvm jeremy
  fi

  systemctl enable --now libvirtd.service
  systemctl enable --now virtlogd.socket
  systemctl restart cockpit.socket
else
  for command_name in bsdtar gpg gpgv qemu-img virsh virt-install; do
    command -v "$command_name" >/dev/null || {
      echo "Missing staged host command: $command_name" >&2
      exit 1
    }
  done
  systemctl is-active --quiet libvirtd.service
fi

ensure_ufw_rule() {
  local comment="$1"
  shift
  if ! ufw status | grep -Fq "# ${comment}"; then
    ufw "$@" comment "$comment"
  fi
}

egress_interface="$(ip route show default | awk 'NR == 1 { print $5 }')"
[[ -n "$egress_interface" ]] || {
  echo "Could not determine EdCore's default egress interface." >&2
  exit 1
}

ensure_ufw_rule 'EdSys Kali lab DHCP' \
  allow in on virbr77 proto udp from any port 68 to any port 67
ensure_ufw_rule 'EdSys Kali bootstrap DHCP' \
  allow in on virbr78 proto udp from any port 68 to any port 67
ensure_ufw_rule 'EdSys Kali bootstrap DNS UDP' \
  allow in on virbr78 proto udp from 192.168.78.0/24 to 192.168.78.1 port 53
ensure_ufw_rule 'EdSys Kali bootstrap DNS TCP' \
  allow in on virbr78 proto tcp from 192.168.78.0/24 to 192.168.78.1 port 53
ensure_ufw_rule 'EdSys Kali bootstrap deny RFC1918 10' \
  route deny in on virbr78 out on "$egress_interface" from 192.168.78.0/24 to 10.0.0.0/8
ensure_ufw_rule 'EdSys Kali bootstrap deny RFC1918 172' \
  route deny in on virbr78 out on "$egress_interface" from 192.168.78.0/24 to 172.16.0.0/12
ensure_ufw_rule 'EdSys Kali bootstrap deny RFC1918 192' \
  route deny in on virbr78 out on "$egress_interface" from 192.168.78.0/24 to 192.168.0.0/16
ensure_ufw_rule 'EdSys Kali bootstrap deny linklocal' \
  route deny in on virbr78 out on "$egress_interface" from 192.168.78.0/24 to 169.254.0.0/16
ensure_ufw_rule 'EdSys Kali bootstrap public egress' \
  route allow in on virbr78 out on "$egress_interface" from 192.168.78.0/24 to any

define_network() {
  local name="$1"
  local xml="$2"
  local autostart="$3"

  if virsh net-info "$name" >/dev/null 2>&1; then
    echo "Refusing to replace the existing libvirt network: $name" >&2
    exit 1
  fi
  virsh net-define "$xml" >/dev/null
  if [[ "$autostart" == true ]]; then
    virsh net-autostart "$name" >/dev/null
  fi
  virsh net-start "$name" >/dev/null
}

if [[ "$resume_staged" == false ]]; then
  define_network security-lab "${source_dir}/security-lab-network.xml" true
  define_network security-lab-bootstrap "${source_dir}/security-lab-bootstrap-network.xml" false
else
  for network_name in security-lab security-lab-bootstrap; do
    virsh net-info "$network_name" >/dev/null 2>&1 || {
      echo "Missing staged libvirt network: $network_name" >&2
      exit 1
    }
    [[ "$(virsh net-info "$network_name" | awk '/^Active:/ {print $2}')" == yes ]] || {
      echo "Staged libvirt network is not active: $network_name" >&2
      exit 1
    }
  done
fi

kali_release="2026.2"
image_name="kali-linux-${kali_release}-installer-amd64.iso"
official_base="https://cdimage.kali.org/kali-${kali_release}"
expected_fingerprint="827C8569F2518CC677FECA1AED65462EC8D5E4C5"
download_dir="/var/lib/libvirt/boot/kali-${kali_release}"
install -d -o root -g libvirt-qemu -m 0750 "$download_dir"

key_file="${download_dir}/archive-key.asc"
keyring_file="${download_dir}/archive-key.gpg"
sums_file="${download_dir}/SHA256SUMS"
signature_file="${download_dir}/SHA256SUMS.gpg"
image_file="${download_dir}/${image_name}"
partial_image="${image_file}.part"

curl --fail --location --retry 5 --retry-all-errors \
  --output "$key_file" https://archive.kali.org/archive-key.asc
curl --fail --location --retry 5 --retry-all-errors \
  --output "$sums_file" "${official_base}/SHA256SUMS"
curl --fail --location --retry 5 --retry-all-errors \
  --output "$signature_file" "${official_base}/SHA256SUMS.gpg"

gpg_home="$(mktemp -d /tmp/edsys-kali-gpg.XXXXXX)"
chmod 0700 "$gpg_home"
cleanup_gpg_home() {
  rm -rf -- "$gpg_home"
}
trap cleanup_gpg_home EXIT

actual_fingerprint="$(gpg --batch --homedir "$gpg_home" --show-keys --with-colons "$key_file" |
  awk -F: '$1 == "fpr" { print toupper($10); exit }')"
if [[ "$actual_fingerprint" != "$expected_fingerprint" ]]; then
  echo "Official Kali archive-key fingerprint verification failed." >&2
  exit 1
fi

gpg --batch --yes --homedir "$gpg_home" --dearmor --output "$keyring_file" "$key_file"
gpgv --keyring "$keyring_file" "$signature_file" "$sums_file"

mapfile -t image_hashes < <(awk -v file="$image_name" '$2 == file { print $1 }' "$sums_file")
if ((${#image_hashes[@]} != 1)) || [[ ! "${image_hashes[0]}" =~ ^[0-9a-f]{64}$ ]]; then
  echo "The signed checksum manifest did not contain one valid hash for ${image_name}." >&2
  exit 1
fi

if [[ -e "$image_file" ]]; then
  printf '%s  %s\n' "${image_hashes[0]}" "$image_file" | sha256sum --check --status
else
  curl --fail --location --retry 8 --retry-all-errors --continue-at - \
    --output "$partial_image" "${official_base}/${image_name}"
  printf '%s  %s\n' "${image_hashes[0]}" "$partial_image" | sha256sum --check
  mv "$partial_image" "$image_file"
fi

chown root:root "$key_file" "$keyring_file" "$sums_file" "$signature_file"
chmod 0644 "$key_file" "$keyring_file" "$sums_file" "$signature_file"
chown root:libvirt-qemu "$image_file"
chmod 0640 "$image_file"

runtime_root="/var/lib/edsys-control/kali-lab"
install -d -o root -g root -m 0700 "$runtime_root"
preseed_file="${runtime_root}/kali-lab-preseed.cfg"
python3 - "${source_dir}/kali-lab-preseed.cfg.in" "$preseed_file" "${public_key_lines[0]}" <<'PY'
import pathlib
import sys

source = pathlib.Path(sys.argv[1]).read_text(encoding="utf-8")
destination = pathlib.Path(sys.argv[2])
key = sys.argv[3]
marker = "@KALI_LAB_SSH_PUBLIC_KEY@"
if source.count(marker) != 1:
    raise SystemExit("Preseed public-key marker count is not exactly one")
destination.write_text(source.replace(marker, key), encoding="utf-8")
destination.chmod(0o600)
PY

disk_file="/var/lib/libvirt/images/kali-lab.qcow2"
if [[ "$resume_staged" == false ]]; then
  if [[ -e "$disk_file" ]]; then
    echo "Refusing to overwrite staged disk: $disk_file" >&2
    exit 1
  fi
  qemu-img create -f qcow2 -o preallocation=metadata,lazy_refcounts=on "$disk_file" 160G
  chown root:root "$disk_file"
  chmod 0600 "$disk_file"
else
  [[ -f "$disk_file" ]] || {
    echo "Missing staged disk: $disk_file" >&2
    exit 1
  }
  qemu-img check --quiet "$disk_file"
fi

python3 - "$runtime_root/manifest.json" "$run_id" "$image_name" "${image_hashes[0]}" <<'PY'
import json
import os
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
temporary = path.with_suffix(".json.tmp")
value = {
    "created_at": sys.argv[2],
    "image": sys.argv[3],
    "sha256": sys.argv[4],
    "role": "isolated-kali-security-lab",
    "version": 1,
}
temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
os.chmod(temporary, 0o600)
os.replace(temporary, path)
PY

if [[ "$prepare_only" == true ]]; then
  echo "Kali lab host, networks, signed image, preseed, and disk are staged."
  echo "Private rollback inventory: ${backup_dir}"
  exit 0
fi

installer_boot_dir="${download_dir}/installer"
installer_kernel="${installer_boot_dir}/vmlinuz"
installer_initrd="${installer_boot_dir}/initrd.gz"
install -d -o root -g libvirt-qemu -m 0750 "$installer_boot_dir"
kernel_temp="${installer_kernel}.${run_id}.tmp"
initrd_temp="${installer_initrd}.${run_id}.tmp"
bsdtar -xOf "$image_file" install.amd/vmlinuz >"$kernel_temp"
bsdtar -xOf "$image_file" install.amd/initrd.gz >"$initrd_temp"
[[ -s "$kernel_temp" && -s "$initrd_temp" ]] || {
  echo "Failed to extract the Kali installer kernel and initrd." >&2
  exit 1
}
install -o root -g root -m 0644 "$kernel_temp" "$installer_kernel"
install -o root -g root -m 0644 "$initrd_temp" "$installer_initrd"
rm -f -- "$kernel_temp" "$initrd_temp"

virt-install \
  --connect qemu:///system \
  --name kali-lab \
  --description "EdSys isolated Kali security lab" \
  --memory 16384 \
  --vcpus 6 \
  --cpu host-passthrough \
  --machine q35 \
  --boot uefi \
  --disk "path=${disk_file},format=qcow2,bus=virtio,cache=none,discard=unmap" \
  --disk "path=${image_file},device=cdrom,readonly=on" \
  --network "network=security-lab-bootstrap,model=virtio,mac=52:54:00:ed:77:10" \
  --install "kernel=${installer_kernel},initrd=${installer_initrd}" \
  --initrd-inject "$preseed_file" \
  --extra-args "auto=true priority=critical file=/kali-lab-preseed.cfg console=ttyS0,115200n8 serial" \
  --osinfo detect=on,name=debian13 \
  --graphics spice,listen=127.0.0.1 \
  --video virtio \
  --sound none \
  --channel unix,target.type=virtio,target.name=org.qemu.guest_agent.0 \
  --rng /dev/urandom \
  --console pty,target.type=serial \
  --noautoconsole \
  --noreboot \
  --wait=-1

sleep 2
virsh dominfo kali-lab >/dev/null 2>&1 || {
  echo "Kali installer exited without leaving a persistent domain." >&2
  exit 1
}
disk_actual_bytes="$(qemu-img info --output=json "$disk_file" | python3 -c 'import json, sys; print(json.load(sys.stdin)["actual-size"])')"
if ((disk_actual_bytes < 1000000000)); then
  echo "Kali installer exited before writing a plausible guest filesystem." >&2
  exit 1
fi

virsh autostart kali-lab --disable >/dev/null
echo "Kali installation completed. Perform the controlled bootstrap update before isolation."
if [[ -n "$backup_dir" ]]; then
  echo "Private rollback inventory: ${backup_dir}"
else
  echo "Installation completed from the already verified staged media."
fi
