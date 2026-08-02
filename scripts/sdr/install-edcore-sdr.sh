#!/usr/bin/env bash
set -euo pipefail
umask 077

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

usage() {
  echo 'Usage: sudo ./install-edcore-sdr.sh --apply' >&2
}

[[ ${1:-} == --apply ]] || { usage; exit 2; }
(( EUID == 0 )) || { echo 'Run as root.' >&2; exit 2; }
[[ $(hostname) == edcore-sdr ]] || { echo 'Refusing to deploy outside edcore-sdr.' >&2; exit 1; }

for command in install jq systemctl ufw python3; do
  command -v "$command" >/dev/null || { echo "Missing command: $command" >&2; exit 1; }
done
dpkg-query -W openwebrx rtl-sdr >/dev/null
python3 -m json.tool "$script_dir/openwebrx-settings.json" >/dev/null
python3 -m json.tool "$script_dir/openwebrx-bookmarks.json" >/dev/null

stamp=$(date -u +%Y%m%dT%H%M%SZ)
backup="/var/backups/edsys-sdr/$stamp"
install -d -m 0700 "$backup"
for path in \
  /etc/openwebrx/openwebrx.conf \
  /var/lib/openwebrx/settings.json \
  /var/lib/openwebrx/bookmarks.json \
  /etc/modprobe.d/blacklist-rtl-sdr-dvb.conf \
  /etc/systemd/system/edsys-rtl-tcp.service \
  /etc/systemd/system/openwebrx.service.d/20-edsys-hardening.conf \
  /etc/systemd/system/edsys-sdr-config-sync.service \
  /etc/systemd/system/edsys-sdr-config-sync.timer \
  /usr/local/sbin/edsys-sdrctl \
  /usr/local/sbin/verify-edcore-sdr \
  /usr/local/libexec/edsys-sdr-config-sync; do
  if [[ -e "$path" ]]; then
    encoded=${path#/}
    encoded=${encoded//\//__}
    cp -a "$path" "$backup/$encoded"
  fi
done

getent group edsys-sdr >/dev/null || groupadd -g 3220 edsys-sdr
usermod -aG plugdev,edsys-sdr openwebrx
usermod -aG plugdev,edsys-sdr jeremy
install -d -m 2770 -o openwebrx -g edsys-sdr /var/spool/edsys-sdr/openwebrx
install -d -m 2770 -o jeremy -g edsys-sdr /opt/edsys-sdr
[[ -d /srv/edsys-sdr-data ]] || install -d -m 0755 /srv/edsys-sdr-data
install -d -m 0755 /etc/systemd/system/openwebrx.service.d /usr/local/libexec

systemctl stop edsys-rtl-tcp.service openwebrx.service 2>/dev/null || true
install -m 0644 -o root -g root "$script_dir/openwebrx.conf" /etc/openwebrx/openwebrx.conf
install -m 0644 -o openwebrx -g openwebrx "$script_dir/openwebrx-settings.json" /var/lib/openwebrx/settings.json
install -m 0644 -o openwebrx -g openwebrx "$script_dir/openwebrx-bookmarks.json" /var/lib/openwebrx/bookmarks.json
install -m 0644 -o root -g root "$script_dir/blacklist-rtl-sdr-dvb.conf" /etc/modprobe.d/blacklist-rtl-sdr-dvb.conf
install -m 0644 -o root -g root "$script_dir/edsys-rtl-tcp.service" /etc/systemd/system/edsys-rtl-tcp.service
install -m 0644 -o root -g root "$script_dir/openwebrx-hardening.conf" /etc/systemd/system/openwebrx.service.d/20-edsys-hardening.conf
install -m 0644 -o root -g root "$script_dir/edsys-sdr-config-sync.service" /etc/systemd/system/edsys-sdr-config-sync.service
install -m 0644 -o root -g root "$script_dir/edsys-sdr-config-sync.timer" /etc/systemd/system/edsys-sdr-config-sync.timer
install -m 0755 -o root -g root "$script_dir/edsys-sdr-config-sync" /usr/local/libexec/edsys-sdr-config-sync
install -m 0755 -o root -g root "$script_dir/edsys-sdrctl" /usr/local/sbin/edsys-sdrctl
install -m 0755 -o root -g root "$script_dir/verify-edcore-sdr.py" /usr/local/sbin/verify-edcore-sdr

udevadm control --reload-rules
udevadm trigger --subsystem-match=usb
ufw allow from 192.168.50.0/24 to any port 22 proto tcp comment 'EdSys SSH' >/dev/null
ufw allow from 192.168.50.0/24 to any port 8073 proto tcp comment 'OpenWebRX Plus' >/dev/null
ufw allow from 192.168.50.0/24 to any port 9090 proto tcp comment 'Cockpit' >/dev/null
ufw --force enable >/dev/null

systemctl daemon-reload
systemctl disable --now codecserver.service >/dev/null 2>&1 || true
systemctl mask codecserver.service >/dev/null 2>&1 || true
systemctl disable edsys-rtl-tcp.service >/dev/null 2>&1 || true
systemctl enable openwebrx.service edsys-sdr-config-sync.timer >/dev/null
systemctl restart openwebrx.service
systemctl start edsys-sdr-config-sync.timer
echo "Deployment backup: $backup"
echo 'EDCORE_SDR_DEPLOY_OK'
