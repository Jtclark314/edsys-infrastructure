#!/usr/bin/env bash
# Run on the Pi after transferring this source directory. Audio remains held.
set -euo pipefail
test "$(hostname)" = edsys-edge-livingroom
test "$(id -u)" = 0
source_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
release=v1.1.15
commit=43a183a2322cde4c9e2558f333124941fdeaa439
root=/opt/edsys-voice/linux-voice-assistant
apt-get install -y --no-install-recommends pipewire pipewire-pulse wireplumber \
  pulseaudio-utils alsa-utils libmpv2 libasound2-plugins ca-certificates \
  python3-venv python3-dev build-essential git
if ! id edsys-voice >/dev/null 2>&1; then
  useradd --system --create-home --home-dir /var/lib/edsys-voice \
    --shell /usr/sbin/nologin --groups audio edsys-voice
fi
install -d -m 0755 /opt/edsys-voice /usr/local/lib/edsys-voice
install -d -m 0750 -o root -g edsys-voice /etc/edsys-voice
install -d -m 0700 -o edsys-voice -g edsys-voice /var/lib/edsys-voice/models
if [[ ! -d "$root/.git" ]]; then
  git -c advice.detachedHead=false clone --depth 1 --branch "$release" \
    https://github.com/OHF-Voice/linux-voice-assistant.git "$root"
fi
test "$(git -C "$root" rev-parse HEAD)" = "$commit"
printf '%s\n' "${release#v}" > "$root/version.txt"
printf '%s\n' "$commit" > "$root/version_githash.txt"
if [[ ! -x "$root/.venv/bin/python" ]]; then
  python3 -m venv "$root/.venv"
  "$root/.venv/bin/python" -m pip install --upgrade pip setuptools wheel
  if [[ -f "$source_dir/requirements-arm64.lock" ]]; then
    "$root/.venv/bin/python" -m pip install --require-hashes -r "$source_dir/requirements-arm64.lock"
    "$root/.venv/bin/python" -m pip install --no-deps --no-build-isolation -e "$root"
  else
    "$root/.venv/bin/python" -m pip install --report /var/backups/edsys-voice/install-report.json -e "$root"
  fi
fi
install -m 0755 "$source_dir/run.py" /usr/local/lib/edsys-voice/run.py
install -m 0644 "$source_dir/edsys-voice.service" /etc/systemd/user/edsys-voice.service
python3 "$source_dir/configure-firewall.py"
if [[ ! -f /etc/edsys-voice/audio.json ]]; then
  install -m 0640 -o root -g edsys-voice "$source_dir/audio.example.json" /etc/edsys-voice/audio.json
fi
loginctl enable-linger edsys-voice
# The SSH operator must not create a second PipeWire session owning the USB pair.
operator_uid=$(id -u jeremy)
if [[ -S "/run/user/$operator_uid/bus" ]]; then
  runuser -u jeremy -- env XDG_RUNTIME_DIR="/run/user/$operator_uid" \
    DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/$operator_uid/bus" \
    systemctl --user mask --now pipewire.service pipewire.socket \
    pipewire-pulse.service pipewire-pulse.socket wireplumber.service
else
  install -d -o jeremy -g jeremy -m 0755 /home/jeremy/.config/systemd/user
  for unit in pipewire.service pipewire.socket pipewire-pulse.service pipewire-pulse.socket wireplumber.service; do
    ln -s /dev/null "/home/jeremy/.config/systemd/user/$unit"
  done
fi
voice_uid=$(id -u edsys-voice)
systemctl start "user@$voice_uid.service"
runuser -u edsys-voice -- env XDG_RUNTIME_DIR="/run/user/$voice_uid" \
  DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/$voice_uid/bus" systemctl --user daemon-reload
runuser -u edsys-voice -- env XDG_RUNTIME_DIR="/run/user/$voice_uid" \
  DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/$voice_uid/bus" \
  systemctl --user enable --now pipewire.socket pipewire-pulse.socket wireplumber.service edsys-voice.service
