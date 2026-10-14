#!/usr/bin/env bash
# Fresh-profile installation only. Does not enable or launch the display unit.
set -euo pipefail
test "$(id -u)" = 0
test "$(hostname)" = edsys-edge-livingroom
test "${1:-}" = --apply
source_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
test ! -e /etc/systemd/system/edsys-bedroom-display.service
test ! -e /var/lib/edsys-voice/.kodi
voice_uid=$(id -u edsys-voice)
kodi_version=3:21.3+dfsg-1+rpt3
checkpoint=/var/backups/edsys-bedroom-display/pre-kodi-$(date -u +%Y%m%dT%H%M%SZ)
install -d -m 0700 "$checkpoint"
dpkg-query -W -f='${binary:Package}\n' | sort > "$checkpoint/packages-before.txt"
apt-mark showmanual | sort > "$checkpoint/manual-before.txt"
cp -a /etc/edsys-voice "$checkpoint/voice-config"
cp -a /etc/systemd/user/edsys-voice.service "$checkpoint/voice-unit"
cp -a /boot/firmware/cmdline.txt "$checkpoint/boot-cmdline"
cp -a /boot/firmware/config.txt "$checkpoint/boot-config"
DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
  "kodi=$kodi_version" "kodi-bin=$kodi_version" "kodi-data=$kodi_version" \
  "kodi-repository-kodi=$kodi_version" fonts-noto-core fonts-noto-mono fonts-roboto-unhinted
install -d -m 0755 /usr/lib/aarch64-linux-gnu/kodi/addons
dpkg-query -W -f='${binary:Package}\n' | sort > "$checkpoint/packages-after.txt"
comm -13 "$checkpoint/packages-before.txt" "$checkpoint/packages-after.txt" > "$checkpoint/packages-added.txt"
python3 "$source_dir/cec_policy.py" --backup-dir "$checkpoint"
install -d -m 0700 -o edsys-voice -g edsys-voice /var/lib/edsys-voice/.kodi
install -d -m 0700 -o edsys-voice -g edsys-voice /var/lib/edsys-voice/.kodi/userdata
install -m 0600 -o edsys-voice -g edsys-voice "$source_dir/guisettings.xml" /var/lib/edsys-voice/.kodi/userdata/guisettings.xml
sed "s/@VOICE_UID@/$voice_uid/g" "$source_dir/edsys-bedroom-display.service" > /etc/systemd/system/edsys-bedroom-display.service
chmod 0644 /etc/systemd/system/edsys-bedroom-display.service
systemd-analyze verify /etc/systemd/system/edsys-bedroom-display.service
systemctl daemon-reload
printf 'Kodi prepared, not started or enabled. Recovery checkpoint: %s\n' "$checkpoint"
