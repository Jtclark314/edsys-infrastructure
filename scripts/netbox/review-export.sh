#!/usr/bin/env bash
set -euo pipefail

/usr/local/sbin/edsys-netbox-sync export --dry-run
echo "NetBox sanitized export plan generated for explicit review; no file was written."
