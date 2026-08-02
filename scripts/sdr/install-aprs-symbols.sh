#!/usr/bin/env bash
set -euo pipefail

commit=f2286a9cd43eb6ba4501250b4c39fff111e3796c
target=/usr/share/aprs-symbols

(( EUID == 0 )) || { echo 'Run as root.' >&2; exit 2; }
work=$(mktemp -d)
trap 'find "$work" -depth -delete' EXIT
git clone -q --filter=blob:none https://github.com/hessu/aprs-symbols.git "$work/source"
git -C "$work/source" checkout -q --detach "$commit"
[[ $(git -C "$work/source" rev-parse HEAD) == "$commit" ]]
install -d -m 0755 "$target"
cp -a "$work/source/png" "$target/"
cp -a "$work/source/COPYRIGHT.md" "$work/source/README.md" "$target/"
find "$target" -type d -exec chmod 0755 {} +
find "$target" -type f -exec chmod 0644 {} +
