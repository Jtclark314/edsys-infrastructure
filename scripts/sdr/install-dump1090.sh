#!/usr/bin/env bash
set -euo pipefail

commit=74f9e6c4b0efe35c27e6806f8f0d9bbe49b8a6b1

(( EUID == 0 )) || { echo 'Run as root.' >&2; exit 2; }
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y git build-essential pkg-config libncurses-dev
work=$(mktemp -d)
trap 'find "$work" -depth -delete' EXIT
git clone -q --filter=blob:none https://github.com/flightaware/dump1090.git "$work/source"
git -C "$work/source" checkout -q --detach "$commit"
git -C "$work/source" submodule update -q --init --recursive
[[ $(git -C "$work/source" rev-parse HEAD) == "$commit" ]]
make -C "$work/source" -j"$(nproc)" \
  DUMP1090_VERSION="edsys-${commit:0:12}" \
  RTLSDR=no BLADERF=no HACKRF=no LIMESDR=no SOAPYSDR=no dump1090
install -m 0755 -o root -g root "$work/source/dump1090" /usr/local/bin/dump1090
/usr/local/bin/dump1090 --version
