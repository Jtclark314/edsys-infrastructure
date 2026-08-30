#!/usr/bin/env bash

set -Eeuo pipefail

if [[ "$(hostname -s)" != "edcore-workhorse" ]]; then
  echo "Run this verifier on edcore-workhorse." >&2
  exit 1
fi

domain="metasploitable2-lab"
expected_hash="2ae8788e95273eee87bd379a250d86ec52f286fa7fe84773a3a8f6524085a1ff"
archive="/var/lib/libvirt/boot/metasploitable2/metasploitable-linux-2.0.0.zip"
failures=0

pass() { printf 'PASS  %s\n' "$1"; }
fail() { printf 'FAIL  %s\n' "$1" >&2; failures=$((failures + 1)); }

if virsh dominfo "$domain" >/dev/null 2>&1; then pass "$domain domain"; else fail "$domain domain"; fi
if [[ "$(virsh dominfo "$domain" 2>/dev/null | awk -F: '/^Autostart:/ {gsub(/^[[:space:]]+/, "", $2); print $2}')" == "disable" ]]; then
  pass "$domain autostart disabled"
else
  fail "$domain autostart disabled"
fi
if [[ "$(virsh domstate "$domain" 2>/dev/null | xargs)" == "shut off" ]]; then
  pass "$domain accepted resting state"
else
  fail "$domain accepted resting state"
fi

if [[ "$(virsh domiflist "$domain" 2>/dev/null | awk '$2 == "network" {print $3}')" == "security-lab" ]]; then
  pass "$domain attached only to security-lab"
else
  fail "$domain attached only to security-lab"
fi
if [[ "$(virsh domiflist "$domain" 2>/dev/null | awk '$2 == "network" {print $4}')" == "e1000" ]]; then
  pass "$domain compatibility NIC"
else
  fail "$domain compatibility NIC"
fi
if [[ "$(virsh domblklist "$domain" --details 2>/dev/null | awk '$2 == "disk" {print $4}')" == "/var/lib/libvirt/images/metasploitable2-lab.qcow2" ]]; then
  pass "$domain dedicated disk"
else
  fail "$domain dedicated disk"
fi

domain_xml="$(virsh dumpxml "$domain" 2>/dev/null || true)"
if python3 - "$domain_xml" <<'PY'
import sys
import xml.etree.ElementTree as ET

root = ET.fromstring(sys.argv[1])
graphics = root.find("./devices/graphics")
if graphics is None or graphics.get("listen") != "127.0.0.1":
    raise SystemExit(1)
if root.findall("./devices/hostdev"):
    raise SystemExit(1)
PY
then
  pass "$domain loopback graphics and no host devices"
else
  fail "$domain loopback graphics and no host devices"
fi

network_xml="$(virsh net-dumpxml security-lab 2>/dev/null || true)"
if [[ -n "$network_xml" ]] && ! grep -q '<forward' <<<"$network_xml"; then pass "security-lab has no forwarding"; else fail "security-lab has no forwarding"; fi
if grep -q "<dns enable='no'" <<<"$network_xml"; then pass "security-lab DNS disabled"; else fail "security-lab DNS disabled"; fi
if grep -Fq "<host mac='52:54:00:ed:77:20' name='metasploitable2-lab' ip='192.168.77.20'" <<<"$network_xml"; then
  pass "$domain fixed DHCP reservation"
else
  fail "$domain fixed DHCP reservation"
fi
if ! virsh net-info security-lab-bootstrap >/dev/null 2>&1; then pass "bootstrap network absent"; else fail "bootstrap network absent"; fi
if ufw status | grep -Fq '# EdSys security lab containment'; then pass "host route containment rule"; else fail "host route containment rule"; fi

if [[ -f "$archive" ]] && printf '%s  %s\n' "$expected_hash" "$archive" | sha256sum --check --status; then
  pass "official target archive checksum"
else
  fail "official target archive checksum"
fi
if qemu-img check --quiet /var/lib/libvirt/images/metasploitable2-lab.qcow2; then pass "$domain disk integrity"; else fail "$domain disk integrity"; fi
snapshot_names="$(virsh snapshot-list "$domain" --name 2>/dev/null || true)"
if grep -q '^clean-vulnerable-baseline-' <<<"$snapshot_names"; then
  pass "$domain clean vulnerable baseline snapshot"
else
  fail "$domain clean vulnerable baseline snapshot"
fi

if ((failures)); then
  echo "Metasploitable target acceptance failed: ${failures}" >&2
  exit 1
fi
echo "Metasploitable target host acceptance passed."
