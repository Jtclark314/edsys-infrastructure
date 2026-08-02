#!/usr/bin/env bash
set -euo pipefail
umask 077

# Codec2 1.2.0 is the stable release shipped by Ubuntu 24.04.  OpenWebRX+
# needs the test utility below, but neither Ubuntu's codec2 packages nor its
# FreeDV desktop package installs that binary.
repo=https://github.com/drowe67/codec2.git
commit=06d4c11e699b0351765f10398abb4f663a984f36
src=$(mktemp -d)
trap 'rm -rf "$src"' EXIT

(( EUID == 0 )) || { echo 'Run as root.' >&2; exit 2; }
apt-get update -qq
DEBIAN_FRONTEND=noninteractive apt-get install -y \
  git build-essential cmake libspeexdsp-dev libsamplerate0-dev

git clone --quiet --filter=blob:none "$repo" "$src/codec2"
git -C "$src/codec2" checkout --quiet --detach "$commit"
[[ $(git -C "$src/codec2" rev-parse HEAD) == "$commit" ]]

cmake -S "$src/codec2" -B "$src/build" -DCMAKE_BUILD_TYPE=Release
cmake --build "$src/build" --parallel "$(nproc)" --target freedv_rx
install -m 0755 -o root -g root "$src/build/src/freedv_rx" /usr/local/bin/freedv_rx
help=$(/usr/local/bin/freedv_rx 2>&1 || true)
grep -qi '^usage:' <<<"$help"
echo 'FREEDV_RX_INSTALL_OK'
