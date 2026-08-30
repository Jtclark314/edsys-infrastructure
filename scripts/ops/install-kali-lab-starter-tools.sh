#!/usr/bin/env bash

set -Eeuo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
bootstrap_xml="${repo_root}/services/kali-lab/security-lab-bootstrap-network.xml"
kali_sources="${repo_root}/services/kali-lab/kali.sources"
control="${EDCORE_CONTROL:-edcore-control}"
host="${EDCORE_SSH_HOST:-edcore-admin}"
bootstrap_mac="52:54:00:ed:78:10"
bootstrap_address="192.168.78.20"
guest_config="/etc/systemd/network/20-kali-starter-bootstrap.network"
remote_xml="/var/lib/edsys-control/kali-lab/security-lab-bootstrap-network.xml"

for command_name in base64 ssh "$control"; do
  command -v "$command_name" >/dev/null || {
    echo "Missing required 9950x command: $command_name" >&2
    exit 1
  }
done
[[ -r "$bootstrap_xml" ]] || {
  echo "Missing reviewed bootstrap network source: $bootstrap_xml" >&2
  exit 1
}
[[ -r "$kali_sources" ]] || {
  echo "Missing reviewed Kali repository source: $kali_sources" >&2
  exit 1
}

ssh -o BatchMode=yes -o ClearAllForwardings=yes "$host" \
  'sudo -n install -d -o root -g root -m 0700 /var/lib/edsys-control/kali-lab && sudo -n install -o root -g root -m 0600 /dev/stdin /var/lib/edsys-control/kali-lab/security-lab-bootstrap-network.xml' \
  <"$bootstrap_xml"

cleanup() {
  set +e
  "$control" lab run -- sudo sh -c "rm -f '$guest_config'; printf '%s\\n' '# Managed for the isolated EdSys Kali lab; no resolver.' >/etc/resolv.conf; networkctl reload" >/dev/null 2>&1
  "$control" root -- bash -lc "
    if virsh dominfo kali-lab >/dev/null 2>&1; then
      state=\$(virsh domstate kali-lab | xargs)
      if [[ \"\$state\" == running ]]; then
        virsh detach-interface kali-lab network --mac '$bootstrap_mac' --live >/dev/null 2>&1 || true
      fi
      virsh detach-interface kali-lab network --mac '$bootstrap_mac' --config >/dev/null 2>&1 || true
    fi
    if virsh net-info security-lab-bootstrap >/dev/null 2>&1; then
      virsh net-destroy security-lab-bootstrap >/dev/null 2>&1 || true
      virsh net-undefine security-lab-bootstrap >/dev/null 2>&1 || true
    fi
    while true; do
      number=\$(ufw status numbered | awk '/EdSys Kali starter bootstrap/ {gsub(/[][]/, \"\", \$1); print \$1; exit}')
      [[ -n \"\$number\" ]] || break
      ufw --force delete \"\$number\" >/dev/null
    done
  " >/dev/null 2>&1
}
trap cleanup EXIT

"$control" root -- bash -lc "
  set -euo pipefail
  [[ \"\$(hostname -s)\" == edcore-workhorse ]]
  [[ \"\$(virsh domstate kali-lab | xargs)\" == running ]]
  ! virsh net-info security-lab-bootstrap >/dev/null 2>&1
  egress=\$(ip route show default | awk 'NR == 1 {print \$5}')
  [[ -n \"\$egress\" ]]
  virsh net-define '$remote_xml' >/dev/null
  virsh net-start security-lab-bootstrap >/dev/null
  virsh net-update security-lab-bootstrap add ip-dhcp-host \
    \"<host mac='$bootstrap_mac' name='kali-starter-bootstrap' ip='$bootstrap_address'/>\" \
    --live --config >/dev/null
  ufw allow in on virbr78 proto udp from any port 68 to any port 67 comment 'EdSys Kali starter bootstrap DHCP'
  ufw allow in on virbr78 proto udp from 192.168.78.0/24 to 192.168.78.1 port 53 comment 'EdSys Kali starter bootstrap DNS UDP'
  ufw allow in on virbr78 proto tcp from 192.168.78.0/24 to 192.168.78.1 port 53 comment 'EdSys Kali starter bootstrap DNS TCP'
  ufw route deny in on virbr78 out on \"\$egress\" from 192.168.78.0/24 to 10.0.0.0/8 comment 'EdSys Kali starter bootstrap deny RFC1918 10'
  ufw route deny in on virbr78 out on \"\$egress\" from 192.168.78.0/24 to 172.16.0.0/12 comment 'EdSys Kali starter bootstrap deny RFC1918 172'
  ufw route deny in on virbr78 out on \"\$egress\" from 192.168.78.0/24 to 192.168.0.0/16 comment 'EdSys Kali starter bootstrap deny RFC1918 192'
  ufw route deny in on virbr78 out on \"\$egress\" from 192.168.78.0/24 to 169.254.0.0/16 comment 'EdSys Kali starter bootstrap deny linklocal'
  ufw route allow in on virbr78 out on \"\$egress\" from 192.168.78.0/24 to any comment 'EdSys Kali starter bootstrap public egress'
"

bootstrap_config="$(cat <<EOF
[Match]
MACAddress=${bootstrap_mac}

[Network]
DHCP=ipv4
LinkLocalAddressing=no
IPv6AcceptRA=no

[DHCPv4]
UseDNS=yes
UseRoutes=yes
EOF
)"
encoded_config="$(printf '%s\n' "$bootstrap_config" | base64 -w0)"
encoded_sources="$(base64 -w0 "$kali_sources")"
"$control" lab run -- sudo bash -lc \
  "test -s /usr/share/keyrings/kali-archive-keyring.gpg; install -d -m 0755 /etc/apt/sources.list.d; printf '%s' '$encoded_sources' | base64 -d >/etc/apt/sources.list.d/kali.sources; chmod 0644 /etc/apt/sources.list.d/kali.sources; printf '%s' '$encoded_config' | base64 -d >'$guest_config'; chmod 0644 '$guest_config'; networkctl reload"

"$control" root -- virsh attach-interface kali-lab network security-lab-bootstrap \
  --model virtio --mac "$bootstrap_mac" --live --config

for attempt in $(seq 1 45); do
  if "$control" lab run -- sh -c \
    "ip -4 route show default | grep -q 'via 192.168.78.1' && getent hosts http.kali.org >/dev/null"; then
    break
  fi
  sleep 2
  if ((attempt == 45)); then
    echo "Kali bootstrap interface did not become ready." >&2
    exit 1
  fi
done

"$control" lab run -- sudo env DEBIAN_FRONTEND=noninteractive apt-get update
"$control" lab run -- sudo env DEBIAN_FRONTEND=noninteractive apt-get install -y \
  nmap metasploit-framework exploitdb netcat-openbsd

"$control" lab run -- sh -c \
  'nmap --version | head -1; command -v msfconsole; command -v searchsploit; dpkg-query -W nmap metasploit-framework exploitdb netcat-openbsd'

cleanup
trap - EXIT

"$control" lab run -- sh -c \
  '! ip -4 route show default | grep -q .; ! getent hosts http.kali.org >/dev/null; ! ping -c 1 -W 1 192.168.50.1 >/dev/null 2>&1; ! ping -c 1 -W 1 1.1.1.1 >/dev/null 2>&1'

echo "Kali starter tools installed; temporary bootstrap network and firewall rules removed."
