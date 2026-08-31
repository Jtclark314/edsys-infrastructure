#!/usr/bin/env bash
set -Eeuo pipefail

[[ $(hostname -s) == pve-node3 ]]
command -v pveversion >/dev/null
ip -4 address show dev vmbr77 | grep -Fq '192.168.77.1/24'
[[ $(cat /proc/sys/net/ipv4/ip_forward) == 0 ]]
[[ $(cat /proc/sys/net/ipv6/conf/all/forwarding) == 0 ]]
systemctl is-enabled --quiet edsys-security-lab-guard.service
systemctl is-active --quiet edsys-security-lab-guard.service
systemctl is-enabled --quiet edsys-security-lab-dhcp.service
systemctl is-active --quiet edsys-security-lab-dhcp.service
/usr/local/sbin/edsys-security-lab-guard verify

grep -Fxq 'port=0' /etc/edsys/security-lab-dnsmasq.conf
grep -Fxq 'dhcp-option=3' /etc/edsys/security-lab-dnsmasq.conf
grep -Fxq 'dhcp-option=6' /etc/edsys/security-lab-dnsmasq.conf
for vmid in 330 331; do
  qm config "$vmid" | grep -Eq '^net0: .*,bridge=vmbr77(,|$)'
  qm config "$vmid" | grep -Fxq 'onboot: 0'
  qm config "$vmid" | grep -Fxq 'protection: 1'
done
qm listsnapshot 330 | grep -Fq clean-baseline-20260830
qm listsnapshot 330 | grep -Fq starter-tools-baseline-20260830
qm listsnapshot 330 | grep -Fq personal-login-baseline-20260830
qm listsnapshot 331 | grep -Fq clean-vulnerable-baseline-20260830
[[ $(qm status 330) == 'status: stopped' ]]
[[ $(qm status 331) == 'status: stopped' ]]

echo 'EDSYS_SECURITY_LAB_PROXMOX_OK'
