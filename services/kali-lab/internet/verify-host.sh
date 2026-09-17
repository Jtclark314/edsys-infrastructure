#!/usr/bin/env bash
set -Eeuo pipefail
[[ $(hostname -s) == pve-node3 ]]
/usr/local/sbin/edsys-security-lab-guard verify
[[ $(qm status 331) == 'status: stopped' ]]
for vm in 330 331; do
  config=$(qm config "$vm")
  grep -Eq '^net0: .*,bridge=vmbr77(,|$)' <<<"$config"
  grep -Fxq 'onboot: 0' <<<"$config"
  grep -Fxq 'protection: 1' <<<"$config"
done
grep -Eqi '^net1: virtio=52:54:00:ED:78:10$' <<<"$(qm config 330)"
[[ $(qm config 331 | grep -Ec '^net[0-9]+:') == 1 ]]
# QEMU user mode is intentional; no port mappings or lab forwarding.
command_line=$(qm showcmd 330)
grep -Fq 'type=user,id=net1' <<<"$command_line"
! grep -Eq 'hostfwd=|guestfwd=' <<<"$command_line"
echo 'KALI_INTERNET_HOST_POLICY_OK'
