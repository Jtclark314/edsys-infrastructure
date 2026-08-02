#!/usr/bin/env bash
set -euo pipefail

if [[ ${EUID} -ne 0 ]]; then
  echo "Run as root on the NetBox VM." >&2
  exit 1
fi
[[ $(hostname -s) == netbox ]] || { echo "Run only on the netbox guest." >&2; exit 1; }

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y --no-install-recommends \
  ca-certificates curl docker.io docker-compose-v2 jq nftables openssl python3 python3-yaml \
  qemu-guest-agent rsync unattended-upgrades
systemctl enable --now qemu-guest-agent docker

install -d -o root -g root -m 0755 /etc/docker
cat >/etc/docker/daemon.json <<'JSON'
{
  "live-restore": true,
  "log-driver": "local",
  "log-opts": {
    "max-size": "10m",
    "max-file": "3"
  },
  "no-new-privileges": true
}
JSON
systemctl restart docker

if ! command -v tailscale >/dev/null 2>&1; then
  curl -fsSL https://pkgs.tailscale.com/stable/ubuntu/noble.noarmor.gpg \
    >/usr/share/keyrings/tailscale-archive-keyring.gpg
  curl -fsSL https://pkgs.tailscale.com/stable/ubuntu/noble.tailscale-keyring.list \
    >/etc/apt/sources.list.d/tailscale.list
  apt-get update
  apt-get install -y --no-install-recommends tailscale
fi
systemctl enable --now tailscaled

lan_iface=$(ip -o route show default | awk '{print $5; exit}')
[[ -n "$lan_iface" ]] || { echo "Unable to identify the LAN interface." >&2; exit 1; }
cat >/etc/edsys-netbox-firewall.nft <<EOF
#!/usr/sbin/nft -f
table inet edsys_netbox_filter {
  chain input {
    type filter hook input priority filter; policy drop;
    iifname "lo" accept
    ct state established,related accept
    ip protocol icmp accept
    ip6 nexthdr ipv6-icmp accept
    udp dport 41641 accept comment "Tailscale WireGuard"
    iifname "$lan_iface" ip saddr 192.168.50.0/24 tcp dport { 22, 80, 443 } accept
    iifname "tailscale0" ip saddr 100.64.0.0/10 tcp dport { 22, 443 } accept
  }

  chain output {
    type filter hook output priority filter; policy accept;
  }
}
EOF
nft -c -f /etc/edsys-netbox-firewall.nft
cat >/usr/local/sbin/edsys-netbox-firewall <<'SCRIPT'
#!/usr/bin/env bash
set -euo pipefail
nft list table inet edsys_netbox_filter >/dev/null 2>&1 && nft delete table inet edsys_netbox_filter
exec nft -f /etc/edsys-netbox-firewall.nft
SCRIPT
chmod 0755 /usr/local/sbin/edsys-netbox-firewall
cat >/etc/systemd/system/edsys-netbox-firewall.service <<'UNIT'
[Unit]
Description=EdSys NetBox guest firewall
DefaultDependencies=no
Before=network-pre.target docker.service tailscaled.service
Wants=network-pre.target

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/usr/local/sbin/edsys-netbox-firewall
ExecReload=/usr/local/sbin/edsys-netbox-firewall

[Install]
WantedBy=multi-user.target
UNIT
systemctl disable nftables.service 2>/dev/null || true
systemctl daemon-reload
systemctl enable edsys-netbox-firewall.service
systemctl restart edsys-netbox-firewall.service

install -d -o root -g root -m 0700 /etc/edsys-secrets/netbox /var/backups/netbox
install -d -o root -g root -m 0755 /srv/edsys/edsys-infrastructure/docker/netbox

systemctl enable unattended-upgrades.service

cat >/etc/systemd/system/edsys-netbox-tailscale-serve.service <<'UNIT'
[Unit]
Description=Tailnet-only HTTPS proxy for EdSys NetBox
Requires=tailscaled.service edsys-netbox-compose.service
After=tailscaled.service edsys-netbox-compose.service network-online.target

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/usr/bin/tailscale serve --bg --yes http://127.0.0.1:8080
ExecStop=/usr/bin/tailscale serve reset

[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload

echo "Guest OS bootstrap complete. Deploy the tracked stack, generate secrets, and enroll Tailscale before enabling Serve."
