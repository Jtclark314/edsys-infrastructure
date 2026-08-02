#!/usr/bin/env bash
set -euo pipefail

commit=9b8cec24d3f8d5e9f7f6e9c23661439e32343d6b

(( EUID == 0 )) || { echo 'Run as root.' >&2; exit 2; }
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y git build-essential cmake libcodec2-dev libboost-dev \
  libboost-program-options-dev libgtest-dev
work=$(mktemp -d)
trap 'find "$work" -depth -delete' EXIT
git clone -q --filter=blob:none https://github.com/mobilinkd/m17-cxx-demod.git "$work/source"
git -C "$work/source" checkout -q --detach "$commit"
git -C "$work/source" submodule update -q --init --recursive
[[ $(git -C "$work/source" rev-parse HEAD) == "$commit" ]]
cmake -S "$work/source" -B "$work/build" -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX=/usr/local
cmake --build "$work/build" --parallel "$(nproc)"
ctest --test-dir "$work/build" --output-on-failure
cmake --install "$work/build"
command -v m17-demod
