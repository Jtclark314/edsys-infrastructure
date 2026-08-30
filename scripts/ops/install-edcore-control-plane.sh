#!/usr/bin/env bash

set -Eeuo pipefail

if ((EUID != 0)); then
  echo "Run this installer as root from the EdCore physical/graphical session." >&2
  exit 1
fi

public_key_file=""
source_dir=""
hub_tailnet_ip=""
nimo_tailnet_ip=""

while (($#)); do
  case "$1" in
    --public-key-file)
      public_key_file="${2:-}"
      shift 2
      ;;
    --source-dir)
      source_dir="${2:-}"
      shift 2
      ;;
    --hub-tailnet-ip)
      hub_tailnet_ip="${2:-}"
      shift 2
      ;;
    --nimo-tailnet-ip)
      nimo_tailnet_ip="${2:-}"
      shift 2
      ;;
    *)
      echo "Unknown argument: $1" >&2
      exit 2
      ;;
  esac
done

if [[ "$(hostname -s)" != "edcore-workhorse" ]]; then
  echo "Refusing to configure the wrong host: $(hostname -s)" >&2
  exit 1
fi

if [[ ! -r "$public_key_file" ]]; then
  echo "The EdCore admin public-key file is missing or unreadable." >&2
  exit 1
fi

if [[ ! -r "${source_dir}/edcore-session" || ! -r "${source_dir}/90-edsys-omarchy-workhorse.conf" ]]; then
  echo "The reviewed EdCore control-plane source files are missing." >&2
  exit 1
fi

python3 - "$hub_tailnet_ip" "$nimo_tailnet_ip" <<'PY'
import ipaddress
import sys

tailnet = ipaddress.ip_network("100.64.0.0/10")
for label, raw in zip(("9950x", "Nimo"), sys.argv[1:]):
    try:
        address = ipaddress.ip_address(raw)
    except ValueError as exc:
        raise SystemExit(f"Invalid {label} Tailnet address: {exc}") from exc
    if address.version != 4 or address not in tailnet:
        raise SystemExit(f"The {label} address is not a Tailnet IPv4 address")
PY

mapfile -t public_key_lines < <(sed '/^[[:space:]]*$/d' "$public_key_file")
if ((${#public_key_lines[@]} != 1)) || [[ ! "${public_key_lines[0]}" =~ ^ssh-ed25519[[:space:]][A-Za-z0-9+/=]+([[:space:]].*)?$ ]]; then
  echo "The supplied public-key file is not one valid ED25519 public key." >&2
  exit 1
fi

packages=(
  audit
  bandwhich
  bottom
  bpftrace
  cockpit
  dmidecode
  glances
  hwinfo
  iftop
  iotop
  nethogs
  perf
  strace
  sysstat
  tailscale
  usbutils
  wev
  ydotool
)

backup_root="/var/backups/edsys-edcore-control"
run_id="$(date -u +%Y%m%dT%H%M%SZ)"
backup_dir="${backup_root}/${run_id}"
install -d -o root -g root -m 0700 "$backup_dir"

backup_if_present() {
  local source="$1"
  local label="$2"
  if [[ -e "$source" ]]; then
    cp -a -- "$source" "${backup_dir}/${label}"
  else
    : >"${backup_dir}/${label}.absent"
  fi
}

backup_if_present /etc/ssh/sshd_config.d/90-edsys-hardening.conf sshd-hardening.conf
backup_if_present /etc/sudoers.d/90-edsys-admin sudoers-edsys-admin
backup_if_present /etc/systemd/system/cockpit.socket.d/10-edsys-loopback.conf cockpit-loopback.conf
backup_if_present /home/jeremy/.config/systemd/user/ydotool.service.d/10-edsys.conf ydotool-user-override.conf
ufw status numbered >"${backup_dir}/ufw-before.txt" 2>&1 || true
chmod 0600 "${backup_dir}/ufw-before.txt"

pacman -S --needed --noconfirm "${packages[@]}"

admin_groups=(wheel docker input video render)
if getent group systemd-journal >/dev/null; then
  admin_groups+=(systemd-journal)
fi
group_csv="$(IFS=,; echo "${admin_groups[*]}")"

if id edsys-admin >/dev/null 2>&1; then
  usermod --shell /bin/bash --append --groups "$group_csv" edsys-admin
else
  useradd --create-home --shell /bin/bash --groups "$group_csv" edsys-admin
fi
passwd --lock edsys-admin >/dev/null
install -d -o edsys-admin -g edsys-admin -m 0700 /home/edsys-admin/.ssh
printf 'from="192.168.50.50,%s",restrict,pty,port-forwarding %s\n' \
  "$hub_tailnet_ip" "${public_key_lines[0]}" \
  >/home/edsys-admin/.ssh/authorized_keys
chown edsys-admin:edsys-admin /home/edsys-admin/.ssh/authorized_keys
chmod 0600 /home/edsys-admin/.ssh/authorized_keys

cat >/etc/sudoers.d/90-edsys-admin <<'EOF'
# Dedicated key-only EdSys control account. The SSH key is separately
# source-restricted to the canonical 9950x host.
edsys-admin ALL=(ALL:ALL) NOPASSWD: ALL
EOF
chown root:root /etc/sudoers.d/90-edsys-admin
chmod 0440 /etc/sudoers.d/90-edsys-admin
visudo -cf /etc/sudoers.d/90-edsys-admin >/dev/null

install -o root -g root -m 0755 "${source_dir}/edcore-session" /usr/local/bin/edcore-session
install -o root -g root -m 0644 "${source_dir}/90-edsys-omarchy-workhorse.conf" \
  /etc/ssh/sshd_config.d/90-edsys-hardening.conf
sshd -t

loginctl enable-linger jeremy
modprobe uinput
cat >/etc/modules-load.d/edsys-uinput.conf <<'EOF'
uinput
EOF

install -d -o jeremy -g jeremy -m 0700 /home/jeremy/.config/systemd/user/ydotool.service.d
cat >/home/jeremy/.config/systemd/user/ydotool.service.d/10-edsys.conf <<'EOF'
[Service]
ExecStart=
ExecStart=/usr/bin/ydotoold --socket-path=%t/ydotool.sock --socket-perm=0600
Restart=always
RestartSec=2
EOF
chown jeremy:jeremy /home/jeremy/.config/systemd/user/ydotool.service.d/10-edsys.conf
chmod 0600 /home/jeremy/.config/systemd/user/ydotool.service.d/10-edsys.conf

runuser -u jeremy -- env \
  XDG_RUNTIME_DIR=/run/user/1000 \
  DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus \
  systemctl --user daemon-reload
runuser -u jeremy -- env \
  XDG_RUNTIME_DIR=/run/user/1000 \
  DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus \
  systemctl --user enable --now ydotool.service

install -d -o root -g root -m 0755 /etc/systemd/system/cockpit.socket.d
cat >/etc/systemd/system/cockpit.socket.d/10-edsys-loopback.conf <<'EOF'
[Socket]
ListenStream=
ListenStream=127.0.0.1:9090
FreeBind=no
EOF
chmod 0644 /etc/systemd/system/cockpit.socket.d/10-edsys-loopback.conf

systemctl daemon-reload
systemctl enable --now tailscaled.service
for unit in \
  cockpit.socket \
  auditd.service \
  smartd.service \
  fstrim.timer \
  sysstat-collect.timer \
  sysstat-summary.timer; do
  if systemctl list-unit-files "$unit" --no-legend 2>/dev/null | grep -q "^${unit}"; then
    systemctl enable --now "$unit"
  fi
done

if ! tailscale status --json 2>/dev/null | python3 -c \
  'import json,sys; raise SystemExit(0 if json.load(sys.stdin).get("BackendState") == "Running" else 1)'; then
  echo
  echo "Tailscale enrollment is required."
  echo "Open the private authorization URL shown below in the EdCore browser and approve this device."
  echo
  tailscale up \
    --hostname=edcore-workhorse \
    --accept-dns=false \
    --accept-routes=false \
    --ssh=false \
    --timeout=10m
fi

tailscale set \
  --accept-dns=false \
  --accept-routes=false \
  --ssh=false \
  --shields-up=false

remove_gateway_rule() {
  local protocol="$1"
  local port="$2"
  ufw --force delete allow proto "$protocol" from 192.168.50.1 to any port "$port" >/dev/null 2>&1 || true
}

for port in 47984 47989 47990 48010; do
  remove_gateway_rule tcp "$port"
done
for port in 5353 47998:48000 48002 48010; do
  remove_gateway_rule udp "$port"
done


# Defense-in-depth host rules. Tailnet policy remains the primary identity gate;
# these exact-peer rules are also useful if Tailscale is later placed in a
# host-firewall-managed netfilter mode.
ufw allow in on tailscale0 proto tcp from "$hub_tailnet_ip" to any port 22 \
  comment 'EdSys 9950x SSH over Tailnet'
for port in 47984 47989 47990 48010; do
  ufw allow in on tailscale0 proto tcp from "$nimo_tailnet_ip" to any port "$port" \
    comment 'EdSys Nimo Sunshine over Tailnet'
done
for port in 5353 47998:48000 48002 48010; do
  ufw allow in on tailscale0 proto udp from "$nimo_tailnet_ip" to any port "$port" \
    comment 'EdSys Nimo Sunshine over Tailnet'
done
ufw status numbered >"${backup_dir}/ufw-after.txt" 2>&1 || true
chmod 0600 "${backup_dir}/ufw-after.txt"

systemctl reload sshd

install -d -o root -g root -m 0700 /var/lib/edsys-control
python3 - "$run_id" "${#packages[@]}" <<'PY'
import json
import os
import pathlib
import sys

path = pathlib.Path("/var/lib/edsys-control/edcore-control-plane.json")
temporary = path.with_suffix(".json.tmp")
value = {
    "installed_at": sys.argv[1],
    "package_count": int(sys.argv[2]),
    "role": "9950x-controlled-omarchy-workhorse",
    "version": 1,
}
temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
os.chmod(temporary, 0o600)
os.replace(temporary, path)
PY

echo "EdCore control-plane bootstrap complete."
echo "Private rollback material: ${backup_dir}"
echo "Keep this terminal open until a fresh edcore-admin SSH acceptance passes."
