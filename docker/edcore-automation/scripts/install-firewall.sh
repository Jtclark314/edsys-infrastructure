#!/usr/bin/env bash
set -Eeuo pipefail

LC_ALL=C
export LC_ALL

readonly stack_dir=/srv/edsys/edsys-infrastructure/docker/edcore-automation
readonly template=$stack_dir/firewall/edsys-automation-firewall.nft.in
readonly canonical=/etc/edsys-automation-firewall.nft
readonly installed_apply=/usr/local/sbin/edsys-automation-firewall
readonly ingress_bridge=br-ed-ingress
readonly egress_bridge=br-edsys-egress

[[ ${1:-} == --apply && $# -eq 1 ]] || {
  echo "Usage: $0 --apply" >&2
  exit 64
}
[[ ${EUID} -eq 0 ]] || { echo "Firewall installation must run as root." >&2; exit 1; }
[[ $(hostname -s) == edcore-automation ]] || { echo "Refusing to install on the wrong host." >&2; exit 1; }

guard_phase=--transfer
if [[ $(stat -c '%u:%g:%a' "$stack_dir/mosquitto/aclfile") == 1883:0:640 ]]; then
  guard_phase=--runtime
fi
"$stack_dir/scripts/source-guard.sh" "$guard_phase"

for bridge in "$ingress_bridge" "$egress_bridge"; do
  [[ $bridge =~ ^[a-z0-9][a-z0-9-]*$ && ${#bridge} -le 15 ]] || {
    echo "Fixed bridge name is not IFNAMSIZ-safe: $bridge" >&2
    exit 1
  }
  grep -Fq -- "\"$bridge\"" "$template" || {
    echo "Firewall template is missing fixed bridge $bridge." >&2
    exit 1
  }
done

lan_iface=$(ip -o route show default | awk '{print $5; exit}')
[[ $lan_iface =~ ^[[:alnum:]_.:-]+$ && ${#lan_iface} -le 15 ]] || {
  echo "Unable to identify an IFNAMSIZ-safe LAN interface." >&2
  exit 1
}
ip -4 -o addr show dev "$lan_iface" | awk '{print $4}' | grep -qx '192.168.50.82/24' || {
  echo "Expected 192.168.50.82/24 is not assigned to $lan_iface." >&2
  exit 1
}
[[ -f $template && ! -L $template && $(grep -o '@LAN_IFACE@' "$template" | wc -l) -eq 5 ]] || {
  echo "Firewall template is absent or has an unexpected placeholder contract." >&2
  exit 1
}

candidate=$(mktemp /run/edsys-automation-firewall-candidate.XXXXXX)
previous=$(mktemp /run/edsys-automation-firewall-previous.XXXXXX)
next=$canonical.new
had_previous=false
cleanup() { rm -f -- "$candidate" "$previous" "$next"; }
trap cleanup EXIT
chmod 0600 "$candidate" "$previous"
sed "s|@LAN_IFACE@|$lan_iface|g" "$template" >"$candidate"
[[ $(grep -c '@LAN_IFACE@' "$candidate") -eq 0 ]] || { echo "Firewall rendering left a placeholder." >&2; exit 1; }

install -o root -g root -m 0755 "$stack_dir/scripts/firewall-apply.sh" "$installed_apply"

active_fingerprint() {
  local snapshot
  if ! snapshot=$(nft -j list table inet edsys_automation_filter 2>/dev/null); then
    printf '%s\n' absent
    return 0
  fi
  jq -cS '
    walk(
      if type == "object" and has("counter") and (.counter | type == "object") then
        .counter |= del(.packets, .bytes)
      else
        .
      end
    )
  ' <<<"$snapshot"
}

if [[ -e $canonical || -L $canonical ]]; then
  [[ -f $canonical && ! -L $canonical ]] || { echo "Existing canonical firewall is not a regular file." >&2; exit 1; }
  canonical_mode=$(stat -c '%a' "$canonical")
  if [[ $(stat -c '%u:%g' "$canonical") != 0:0 ]] || (( (8#$canonical_mode & 8#022) != 0 )); then
    echo "Existing canonical firewall has unsafe ownership or mode." >&2
    exit 1
  fi
  cp --preserve=mode,ownership,timestamps -- "$canonical" "$previous"
  had_previous=true
fi
active_before=$(active_fingerprint)

install -o root -g root -m 0644 "$candidate" "$next"
mv -f -- "$next" "$canonical"
if ! "$installed_apply"; then
  if [[ $had_previous == true ]]; then
    install -o root -g root -m 0644 "$previous" "$next"
    mv -f -- "$next" "$canonical"
  else
    rm -f -- "$canonical"
  fi
  active_after=$(active_fingerprint)
  [[ $active_after == "$active_before" ]] || {
    echo "Failed atomic replacement changed the active firewall; inspect from the console." >&2
    exit 1
  }
  echo "New firewall failed to apply; deployment remains stopped before Compose attachment." >&2
  exit 1
fi

printf 'automation_firewall_install=passed interface=%s\n' "$lan_iface"
