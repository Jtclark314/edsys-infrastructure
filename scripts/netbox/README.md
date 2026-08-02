# EdSys NetBox Synchronization

`edsys-netbox-sync` is the reviewed, no-delete bridge between EdSys discovery
sources and NetBox. It owns observed facts only and never pushes configuration
to Proxmox, Docker, routers, switches, wireless infrastructure, or guests.

## Safety gate

Every mutating command first writes a root-private plan and prints its SHA-256.
Application refuses unless the exact hash is supplied:

```bash
sudo edsys-netbox-sync bootstrap --dry-run
sudo edsys-netbox-sync bootstrap --apply --confirm-plan-hash HASH_FROM_DRY_RUN
```

Supported commands are `bootstrap`, `sync-proxmox`, `sync-docker`,
`sync-network`, `reconcile`, `validate`, and `export`. Automatic deletion is
not implemented. Missing objects require a separately reviewed retirement
plan.

`sync-docker` records a private evidence report because NetBox has no native
container model. It does not invent one. Reviewed service-catalog records own
application service intent.

`sync-proxmox` also inventories verified chassis, board, disk, and physical
NIC evidence into private NetBox inventory items. Root-private plan/evidence
files can contain hardware serials and must not be copied into Git or RAG.

The daily review timer runs discovery, reconciliation, and validation only.
It never applies a generated plan. All cutover gates passed on 2026-08-02 and
the reviewed cutover constant now marks managed operational records
authoritative. Retired host-local bridge observations remain explicitly
non-authoritative.

The export command follows the same review gate and writes a deterministic,
sanitized snapshot. It excludes serials, credentials, SSIDs/PSKs, API tokens,
dynamic clients, and raw evidence. It never commits or pushes.

## Guest Healthchecks heartbeats

After `scripts/ops/bootstrap-healthchecks.sh` has created the private local
check files, run `install-guest-healthchecks.sh`. It rewrites only the
allowlisted base URL to the 9950x LAN address and installs the backup and
restore-test ping URLs under the guest's root-only NetBox secret directory.
It never prints a ping token.
