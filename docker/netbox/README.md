# EdSys NetBox

This stack runs NetBox 4.6.7 in dedicated Proxmox VMID 323. NetBox becomes the
structured operational authority only after the import, idempotence, backup,
isolated restore, VM reboot, and `pve-edcore` reboot gates pass. Until then the
reviewed EdSys-Master YAML remains authoritative.

## Boundaries

- LAN: `https://netbox.edsys.local` on `192.168.50.81` through Caddy's private CA.
- Tailnet: `https://netbox.taile832fe.ts.net` through Tailscale Serve.
- NetBox itself publishes only `127.0.0.1:8080`; Caddy reaches it through the
  private Compose network.
- No Cloudflare tunnel, public DNS, port forwarding, Funnel, or wildcard bind.
- Secrets are root-owned files under `/etc/edsys-secrets/netbox`; they are
  mounted individually as Docker secrets and never belong in Git.

## Provision and deploy

1. On `pve-edcore`, run the checked `scripts/provision-vm.sh --apply` after its
   read-only preflight.
2. Copy this tracked directory to the same path inside the guest.
3. Run `scripts/bootstrap-guest.sh` as root.
4. Run `scripts/generate-secrets.sh` as root.
5. Run `scripts/deploy.sh` as root.
6. Supply a one-time Tailscale auth key outside Git, then run
   `scripts/enroll-tailscale.sh`. Enrollment deliberately declines Tailnet DNS
   and advertised routes so the server retains the reviewed LAN/Pi-hole path.
7. Trust Caddy's public root certificate on approved LAN clients only.

## Backups and restore

`scripts/backup.sh` produces a PostgreSQL custom-format dump, volume archives,
configuration copies, image identities, object counts, and SHA-256 checksums
under `/var/backups/netbox`. The 9950x backup orchestrator must pull and verify
`current` before encrypted Restic runs. `scripts/restore-test.sh` validates
hashes/archives, PostgreSQL checksums and relations, then runs NetBox migration,
system-check, and representative ORM assertions against an internal-only
temporary PostgreSQL/Valkey/NetBox environment. Production restoration must
always begin in isolation.

The 9950x-side verified pull, sync/export review timers, and Healthchecks
integration live under `scripts/netbox/`. Scheduled discovery and export jobs
produce plans only; neither applies changes nor writes the reviewed export.

The Proxmox post-acceptance snapshot is a short-lived rollback point, not a
backup. Upgrades are manual and require a logical backup, isolated rehearsal,
release-note review, image scan, migrations, smoke tests, and retained rollback
artifacts.

## Verification

```bash
docker compose config --quiet
docker compose ps
docker compose exec -T netbox /opt/netbox/netbox/manage.py check
docker compose exec -T netbox /opt/netbox/netbox/manage.py migrate --check
curl --fail --cacert /path/to/caddy-root.crt https://netbox.edsys.local/login/
curl --fail https://netbox.taile832fe.ts.net/login/
```

Third-party plugins are intentionally absent. NetBox never pushes device,
hypervisor, Docker, or network configuration.
