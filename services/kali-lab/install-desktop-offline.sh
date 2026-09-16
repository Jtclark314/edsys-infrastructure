#!/usr/bin/env bash
# Run inside the isolated Kali VM after staging a signed-APT download bundle.
set -Eeuo pipefail
[[ ${1:-} == --apply ]] || { echo 'Usage: install-desktop-offline.sh --apply'; exit 2; }
[[ $EUID == 0 ]]
grep -q '^ID=kali$' /etc/os-release
ip -4 address show | grep -Fq '192.168.77.10/24'
[[ -z $(ip -4 route show default) ]]
[[ -z $(ip -6 route show default) ]]
systemctl is-active --quiet systemd-networkd
bundle=/var/lib/edsys-desktop-install
cd "$bundle/archives"
sha256sum --check ../packages.sha256 > ../transfer-verification.log
if [[ ! -e "$bundle/previous-apt-lists.tar" ]]; then
  tar -C /var/lib/apt -cf "$bundle/previous-apt-lists.tar" lists
fi
# Remove stale uncompressed indexes before importing newer compressed indexes.
find /var/lib/apt/lists -maxdepth 1 -type f ! -name lock -delete
tar -C /var/lib/apt -xf "$bundle/apt-lists.tar"
cp -- *.deb /var/cache/apt/archives/

# Keep the established static, no-gateway network under systemd-networkd.
install -d -m 0755 /etc/NetworkManager/conf.d
cat > /etc/NetworkManager/conf.d/99-edsys-isolated-lab.conf <<'EOF'
[keyfile]
unmanaged-devices=interface-name:*
EOF
systemctl mask NetworkManager.service NetworkManager-wait-online.service

# Suppress package-triggered service starts; preserve any existing policy.
policy=/usr/sbin/policy-rc.d
[[ ! -e "$bundle/original-policy-rc.d" ]]
if [[ -e $policy || -L $policy ]]; then
  mv "$policy" "$bundle/original-policy-rc.d"
fi
restore_policy() {
  rm -f "$policy"
  if [[ -e "$bundle/original-policy-rc.d" || -L "$bundle/original-policy-rc.d" ]]; then
    mv "$bundle/original-policy-rc.d" "$policy"
  fi
}
trap restore_policy EXIT
printf '#!/bin/sh\nexit 101\n' > "$policy"
chmod 0755 "$policy"
export DEBIAN_FRONTEND=noninteractive NEEDRESTART_MODE=l
apt-get --no-download --no-remove -y -o Dpkg::Options::=--force-confold \
  install kali-desktop-xfce x11-apps xdotool
restore_policy
trap - EXIT

# No additional remote desktop listener; use the existing Proxmox console.
for unit in avahi-daemon.service avahi-daemon.socket cups.service cups.socket \
  cups.path cups-browsed.service bluetooth.service ModemManager.service; do
  if systemctl cat "$unit" >/dev/null 2>&1; then
    systemctl disable --now "$unit"
  fi
done
update-alternatives --set x-session-manager /usr/bin/startxfce4
systemctl set-default graphical.target
systemctl enable lightdm.service
systemctl start lightdm.service
dpkg --audit
systemctl is-active --quiet lightdm.service systemd-networkd.service
[[ -z $(ip -4 route show default) ]]
[[ -z $(ip -6 route show default) ]]
echo 'Offline Kali Xfce installation completed.'
