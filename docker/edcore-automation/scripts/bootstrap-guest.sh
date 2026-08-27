#!/usr/bin/env bash
set -Eeuo pipefail

stack_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
readonly stack_dir

if [[ ${1:-} != --apply || $# -ne 1 ]]; then
  cat <<'EOF'
Dry-run boundary: this script hardens the accepted edcore-automation Ubuntu
guest, installs Docker/Compose and required utilities, and installs its
default-deny firewall/systemd units. Run with --apply only inside that guest.
EOF
  exit 0
fi
[[ ${EUID} -eq 0 ]] || { echo "Run as root on edcore-automation." >&2; exit 1; }
[[ $(hostname -s) == edcore-automation ]] || { echo "Refusing to bootstrap the wrong guest." >&2; exit 1; }
id jeremy >/dev/null 2>&1 || { echo "Expected cloud-init user jeremy is absent." >&2; exit 1; }
[[ -s /home/jeremy/.ssh/authorized_keys ]] || { echo "jeremy has no authorized SSH key; refusing SSH hardening." >&2; exit 1; }

# Root must never execute a tree that the transfer account can still modify.
# A redeploy may already have Mosquitto's three read-only sources owned by its
# service UID; select the corresponding coherent guard phase without relaxing
# any other ownership or mode.
guard_phase=--transfer
if [[ $(stat -c '%u:%g:%a' "$stack_dir/mosquitto/aclfile") == 1883:0:640 ]]; then
  guard_phase=--runtime
fi
"$stack_dir/scripts/source-guard.sh" "$guard_phase"

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y --no-install-recommends \
  age apache2-utils ca-certificates chrony curl docker.io docker-compose-v2 git jq nftables \
  openssl python3 qemu-guest-agent rsync sqlite3 unattended-upgrades
systemctl enable --now qemu-guest-agent docker chrony unattended-upgrades.service

install -d -o root -g root -m 0755 /etc/docker
cat >/etc/docker/daemon.json <<'JSON'
{
  "live-restore": true,
  "log-driver": "local",
  "log-opts": {
    "max-size": "10m",
    "max-file": "3"
  },
  "no-new-privileges": true,
  "userland-proxy": false
}
JSON
systemctl restart docker

cat >/etc/ssh/sshd_config.d/60-edsys-automation.conf <<'SSH'
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitEmptyPasswords no
PermitRootLogin no
PubkeyAuthentication yes
X11Forwarding no
AllowTcpForwarding local
GatewayPorts no
PermitTunnel no
AllowUsers jeremy edsys-backup
ClientAliveInterval 300
ClientAliveCountMax 2
MaxAuthTries 4
SSH
sshd -t
systemctl reload ssh.service

cat >/etc/sysctl.d/60-edsys-automation-hardening.conf <<'SYSCTL'
fs.protected_fifos = 2
fs.protected_hardlinks = 1
fs.protected_regular = 2
fs.protected_symlinks = 1
kernel.dmesg_restrict = 1
kernel.kptr_restrict = 2
kernel.unprivileged_bpf_disabled = 1
net.ipv4.conf.all.accept_redirects = 0
net.ipv4.conf.all.send_redirects = 0
net.ipv4.conf.default.accept_redirects = 0
net.ipv4.conf.default.send_redirects = 0
net.ipv6.conf.all.accept_redirects = 0
net.ipv6.conf.default.accept_redirects = 0
SYSCTL
sysctl --system >/dev/null

install -o root -g root -m 0755 \
  "$stack_dir/scripts/source-guard.sh" \
  /usr/local/sbin/edsys-automation-source-guard
systemctl disable --now ufw.service nftables.service 2>/dev/null || true
"$stack_dir/scripts/install-firewall.sh" --apply

install -d -o root -g root -m 0750 /etc/edsys-secrets/edcore-automation /var/backups/edcore-automation
install -d -o root -g root -m 0750 /etc/edsys-escrow
install -d -o root -g root -m 0700 /var/backups/edcore-automation-secret-escrow
install -d -o root -g root -m 0755 "$stack_dir"
if [[ ! -e "$stack_dir/.env" ]]; then
  install -o root -g root -m 0640 "$stack_dir/.env.example" "$stack_dir/.env"
fi

for unit in "$stack_dir"/systemd/*; do
  install -o root -g root -m 0644 "$unit" "/etc/systemd/system/$(basename "$unit")"
done
systemctl daemon-reload
systemctl enable --now edsys-automation-firewall.service

if [[ $guard_phase == --runtime ]]; then
  /usr/local/sbin/edsys-automation-source-guard --runtime
else
  "$stack_dir/scripts/source-guard.sh" --transfer
fi

printf 'Guest hardening/bootstrap complete. Application units are installed but deliberately disabled until deploy/verify acceptance.\n'
