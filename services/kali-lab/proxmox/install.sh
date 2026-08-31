#!/usr/bin/env bash
set -Eeuo pipefail

if [[ ${EUID} -ne 0 ]]; then
  echo "Run as root on pve-node3." >&2
  exit 1
fi
if [[ ${1:-} != --apply ]]; then
  echo "Usage: $0 --apply" >&2
  exit 2
fi
if [[ $(hostname -s) != pve-node3 ]] || ! command -v pveversion >/dev/null 2>&1; then
  echo "Refusing to deploy outside Proxmox host pve-node3." >&2
  exit 1
fi
for command in /usr/sbin/dnsmasq /usr/sbin/nft /usr/sbin/ifreload; do
  [[ -x $command ]] || { echo "Required command missing: $command" >&2; exit 1; }
done

source_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
backup_dir="/var/backups/edsys-security-lab/$(date -u +%Y%m%dT%H%M%SZ)"
install -d -o root -g root -m 0700 "$backup_dir"
for path in \
  /etc/network/interfaces.d/edsys-security-lab \
  /etc/edsys/security-lab-dnsmasq.conf \
  /etc/edsys/security-lab.nft \
  /etc/sysctl.d/99-edsys-security-lab.conf \
  /etc/systemd/system/edsys-security-lab-guard.service \
  /etc/systemd/system/edsys-security-lab-dhcp.service \
  /usr/local/sbin/edsys-security-lab-guard \
  /usr/local/sbin/verify-edsys-security-lab; do
  [[ ! -e $path ]] || cp -a -- "$path" "$backup_dir/"
done

install -d -o root -g root -m 0755 /etc/edsys /etc/network/interfaces.d
install -o root -g root -m 0644 "$source_dir/edsys-security-lab.interfaces" /etc/network/interfaces.d/edsys-security-lab
install -o root -g root -m 0644 "$source_dir/security-lab-dnsmasq.conf" /etc/edsys/security-lab-dnsmasq.conf
install -o root -g root -m 0644 "$source_dir/security-lab.nft" /etc/edsys/security-lab.nft
install -o root -g root -m 0644 "$source_dir/99-edsys-security-lab.conf" /etc/sysctl.d/99-edsys-security-lab.conf
install -o root -g root -m 0755 "$source_dir/edsys-security-lab-guard" /usr/local/sbin/edsys-security-lab-guard
install -o root -g root -m 0755 "$source_dir/verify.sh" /usr/local/sbin/verify-edsys-security-lab
install -o root -g root -m 0644 "$source_dir/edsys-security-lab-guard.service" /etc/systemd/system/edsys-security-lab-guard.service
install -o root -g root -m 0644 "$source_dir/edsys-security-lab-dhcp.service" /etc/systemd/system/edsys-security-lab-dhcp.service

sysctl --system >/dev/null
ifreload -a
systemctl daemon-reload
systemctl enable --now edsys-security-lab-guard.service edsys-security-lab-dhcp.service
/usr/local/sbin/verify-edsys-security-lab
