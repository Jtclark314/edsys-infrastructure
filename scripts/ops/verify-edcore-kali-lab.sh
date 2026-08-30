#!/usr/bin/env bash

set -Eeuo pipefail

if [[ "$(hostname -s)" != "edcore-workhorse" ]]; then
  echo "Run this verifier on edcore-workhorse." >&2
  exit 1
fi

failures=0

pass() {
  printf 'PASS  %s\n' "$1"
}

fail() {
  printf 'FAIL  %s\n' "$1" >&2
  failures=$((failures + 1))
}

require_command() {
  local command="$1"
  if command -v "$command" >/dev/null 2>&1; then pass "command $command"; else fail "command $command"; fi
}

for command in virsh qemu-system-x86_64 qemu-img virt-install; do
  require_command "$command"
done

for package in qemu-desktop libvirt virt-install cockpit-machines edk2-ovmf; do
  if pacman -Q "$package" >/dev/null 2>&1; then pass "package $package"; else fail "package $package"; fi
done

for unit in libvirtd.service cockpit.socket; do
  if systemctl is-active --quiet "$unit"; then pass "active $unit"; else fail "active $unit"; fi
done

if [[ "$(ss -Hlnpt 'sport = :9090' | awk '{print $4}' | sort -u)" == "127.0.0.1:9090" ]]; then
  pass "Cockpit loopback-only listener"
else
  fail "Cockpit loopback-only listener"
fi

if virsh dominfo kali-lab >/dev/null 2>&1; then pass "kali-lab domain"; else fail "kali-lab domain"; fi
if [[ "$(virsh dominfo kali-lab | awk -F: '/^Autostart:/ {gsub(/^[[:space:]]+/, "", $2); print $2}')" == "disable" ]]; then
  pass "kali-lab autostart disabled"
else
  fail "kali-lab autostart disabled"
fi

if virsh net-info security-lab >/dev/null 2>&1; then pass "security-lab network"; else fail "security-lab network"; fi
if ! virsh net-dumpxml security-lab | grep -q '<forward'; then
  pass "security-lab has no forwarding"
else
  fail "security-lab has no forwarding"
fi
if virsh net-dumpxml security-lab | grep -q "<dns enable='no'"; then
  pass "security-lab DNS disabled"
else
  fail "security-lab DNS disabled"
fi
if ! virsh net-info security-lab-bootstrap >/dev/null 2>&1; then
  pass "bootstrap network removed"
else
  fail "bootstrap network removed"
fi

if [[ "$(virsh domiflist kali-lab | awk '$2 == "network" {print $3}')" == "security-lab" ]]; then
  pass "kali-lab attached only to security-lab"
else
  fail "kali-lab attached only to security-lab"
fi
if ! virsh domblklist kali-lab --details | awk '$2 == "cdrom" {found=1} END {exit !found}'; then
  pass "installer media detached"
else
  fail "installer media detached"
fi
if ! ufw status | grep -q 'EdSys Kali bootstrap'; then
  pass "bootstrap firewall rules removed"
else
  fail "bootstrap firewall rules removed"
fi

if virsh snapshot-list kali-lab --name | grep -q '^clean-baseline-'; then
  pass "clean baseline snapshot"
else
  fail "clean baseline snapshot"
fi

if ((failures)); then
  echo "Kali lab host acceptance failed: ${failures}" >&2
  exit 1
fi
echo "Kali lab host acceptance passed."
