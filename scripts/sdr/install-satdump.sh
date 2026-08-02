#!/usr/bin/env bash
set -euo pipefail

version=1.2.2
sha256=68672f0d1bb76d5646d02ad2cbbeaa7549bed5ecedf4ee27d0d8467e72cf3221
url="https://github.com/SatDump/SatDump/releases/download/${version}/satdump_${version}_ubuntu_24.04_amd64.deb"

(( EUID == 0 )) || { echo 'Run as root.' >&2; exit 2; }
package=$(mktemp --suffix=.deb)
trap 'find "$package" -delete' EXIT
curl -fL --retry 3 --proto '=https' --tlsv1.2 -o "$package" "$url"
echo "$sha256  $package" | sha256sum --check --strict
[[ $(dpkg-deb -f "$package" Package) == satdump ]]
[[ $(dpkg-deb -f "$package" Version) == "$version" ]]
apt-get install -y "$package"
command -v satdump >/dev/null
dpkg-query -W -f='${Version}\n' satdump | grep -Fx "$version" >/dev/null
# SatDump 1.2.2 intentionally returns 1 after printing its CLI usage.
satdump --help >/dev/null 2>&1 || [[ $? -eq 1 ]]
