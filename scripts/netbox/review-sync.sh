#!/usr/bin/env bash
set -euo pipefail

/usr/local/sbin/edsys-netbox-sync sync-proxmox --dry-run
/usr/local/sbin/edsys-netbox-sync sync-docker --dry-run
/usr/local/sbin/edsys-netbox-sync sync-network --dry-run
/usr/local/sbin/edsys-netbox-sync reconcile --dry-run
/usr/local/sbin/edsys-netbox-sync validate --dry-run

echo "NetBox discovery plans generated for review; no changes were applied."
