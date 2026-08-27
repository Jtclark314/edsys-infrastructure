#!/usr/bin/env bash
set -Eeuo pipefail

readonly canonical=/etc/edsys-automation-firewall.nft
config=$canonical

if (( $# > 0 )); then
  [[ $# -eq 2 && $1 == --candidate ]] || {
    echo "Usage: $0 [--candidate /absolute/read-only-test-file]" >&2
    exit 64
  }
  config=$2
fi

[[ ${EUID} -eq 0 ]] || { echo "Firewall apply must run as root." >&2; exit 1; }
[[ $config == /* && -f $config && ! -L $config ]] || {
  echo "Firewall candidate must be an absolute regular non-symlink file." >&2
  exit 1
}
[[ $(stat -c '%u:%g' "$config") == 0:0 ]] || { echo "Firewall candidate is not root-owned." >&2; exit 1; }
config_mode=$(stat -c '%a' "$config")
(( (8#$config_mode & 8#022) == 0 )) || { echo "Firewall candidate is group/world writable." >&2; exit 1; }

transaction=$(mktemp /run/edsys-automation-firewall.XXXXXX)
cleanup() { rm -f -- "$transaction"; }
trap cleanup EXIT
chmod 0600 "$transaction"

# Deletion and replacement are one nftables transaction. Parsing/checking the
# immutable snapshot occurs before apply; an invalid candidate cannot remove
# the active table or leave a partially loaded ruleset.
if nft list table inet edsys_automation_filter >/dev/null 2>&1; then
  printf 'delete table inet edsys_automation_filter\n' >>"$transaction"
fi
cat -- "$config" >>"$transaction"
nft -c -f "$transaction"
nft -f "$transaction"
