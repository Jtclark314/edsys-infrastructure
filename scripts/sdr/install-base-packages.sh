#!/usr/bin/env bash
set -euo pipefail
umask 077

mode=${1:-}
expected_fingerprint="82B2AC14962B3C75FAC97206B64EADD5CB69C981"
key_url="https://luarvique.github.io/ppa/openwebrx-plus.gpg"
key_path="/etc/apt/trusted.gpg.d/openwebrx-plus.gpg"
source_path="/etc/apt/sources.list.d/openwebrx-plus.list"
source_line="deb [signed-by=${key_path}] https://luarvique.github.io/ppa/noble ./"

packages=(
  build-essential
  ca-certificates
  cmake
  cockpit
  cockpit-pcp
  curl
  direwolf
  dnsutils
  ethtool
  fd-find
  ffmpeg
  git
  gnupg
  gnuradio
  htop
  iftop
  iotop
  jq
  libboost-dev
  libboost-program-options-dev
  libcodec2-dev
  libgtest-dev
  libhamlib-utils
  libncurses-dev
  librtlsdr-dev
  libsamplerate0-dev
  libspeexdsp-dev
  libwrap0
  lsof
  mosquitto-clients
  multimon-ng
  netcat-openbsd
  nfs-common
  nmap
  openssh-server
  pipx
  pkg-config
  python3-pip
  python3-serial
  python3-venv
  qemu-guest-agent
  ripgrep
  rsync
  rtl-433
  rtl-sdr
  soapysdr-module-rtlsdr
  soapysdr-tools
  socat
  sox
  sqlite3
  tcpdump
  tmux
  ufw
  unattended-upgrades
  usbutils
  yq
)

usage() {
  cat >&2 <<'EOF'
Usage: sudo ./install-base-packages.sh --check | --apply

--check  Verify the Ubuntu release, OpenWebRX+ repository, and base packages.
--apply  Back up repository metadata, configure the verified public repository,
         and install OpenWebRX+ plus the EdCore SDR base package set.
EOF
}

(( EUID == 0 )) || { echo 'Run as root.' >&2; exit 2; }
[[ $(hostname) == edcore-sdr ]] || { echo 'Refusing to run outside edcore-sdr.' >&2; exit 1; }
[[ -r /etc/os-release ]] || { echo 'Missing /etc/os-release.' >&2; exit 1; }
# shellcheck disable=SC1091
source /etc/os-release
[[ ${ID:-} == ubuntu && ${VERSION_CODENAME:-} == noble ]] || {
  echo "Expected Ubuntu noble, found ${ID:-unknown}/${VERSION_CODENAME:-unknown}." >&2
  exit 1
}

check_state() {
  local failed=0 fingerprint package
  if [[ ! -r $key_path ]]; then
    echo "FAIL missing ${key_path}" >&2
    failed=1
  else
    fingerprint=$(gpg --batch --show-keys --with-colons "$key_path" 2>/dev/null |
      awk -F: '$1 == "fpr" { print $10; exit }')
    if [[ $fingerprint != "$expected_fingerprint" ]]; then
      echo "FAIL OpenWebRX+ key fingerprint=${fingerprint:-missing}" >&2
      failed=1
    else
      echo "PASS OpenWebRX+ key fingerprint=${fingerprint}"
    fi
  fi
  if [[ $(cat "$source_path" 2>/dev/null || true) != "$source_line" ]]; then
    echo "FAIL OpenWebRX+ noble source is missing or differs" >&2
    failed=1
  else
    echo 'PASS OpenWebRX+ noble source'
  fi
  for package in openwebrx "${packages[@]}"; do
    if ! dpkg-query -W -f='${db:Status-Abbrev}' "$package" 2>/dev/null | grep -q '^ii '; then
      echo "FAIL package missing: ${package}" >&2
      failed=1
    fi
  done
  (( failed == 0 )) || return 1
  echo "PASS base packages: $(( ${#packages[@]} + 1 )) installed"
}

case "$mode" in
  --check)
    check_state
    ;;
  --apply)
    stamp=$(date -u +%Y%m%dT%H%M%SZ)
    backup="/var/backups/edsys-sdr-base/${stamp}"
    work=$(mktemp -d)
    cleanup() {
      find "$work" -type f -delete 2>/dev/null || true
      rmdir "$work" 2>/dev/null || true
    }
    trap cleanup EXIT
    install -d -m 0700 "$backup"
    for path in "$key_path" "$source_path"; do
      if [[ -e $path ]]; then
        cp -a "$path" "$backup/$(basename "$path")"
      fi
    done
    curl --fail --silent --show-error --location "$key_url" -o "$work/openwebrx-plus.asc"
    fingerprint=$(gpg --batch --show-keys --with-colons "$work/openwebrx-plus.asc" |
      awk -F: '$1 == "fpr" { print $10; exit }')
    [[ $fingerprint == "$expected_fingerprint" ]] || {
      echo "Refusing unrecognized OpenWebRX+ key fingerprint=${fingerprint:-missing}." >&2
      exit 1
    }
    gpg --batch --yes --dearmor --output "$work/openwebrx-plus.gpg" "$work/openwebrx-plus.asc"
    install -m 0644 -o root -g root "$work/openwebrx-plus.gpg" "$key_path"
    printf '%s\n' "$source_line" >"$work/openwebrx-plus.list"
    install -m 0644 -o root -g root "$work/openwebrx-plus.list" "$source_path"
    export DEBIAN_FRONTEND=noninteractive
    apt-get update
    apt-get install -y openwebrx "${packages[@]}"
    check_state
    echo "Private repository-metadata backup: ${backup}"
    echo 'EDCORE_SDR_BASE_PACKAGES_OK'
    ;;
  *)
    usage
    exit 2
    ;;
esac
