# Netdata Compute Topology

## Current Design

`9950x` is the single EdSys Netdata Parent. The four Proxmox hosts are Netdata
Children and stream to the parent over the LAN:

- `9950x` — parent and local collector
- `pve-edcore` — child
- `pve-node0` — child
- `pve-node1` — child
- `pve-node2` — child

All five nodes carry the host label `group=edsys-compute`. The authoritative
local dashboard and API are on `9950x` TCP `19999`.

The streaming API key is generated at deployment time, stored only in
root-readable live configuration, and never committed. Existing Netdata Cloud
claim material is backed up and preserved. Children may retain a direct Cloud
connection for path redundancy; this does not create a second local node
because Netdata uses the child machine/node identity when the Parent reports it.

## Deployment

From the authoritative infrastructure checkout on `9950x`:

```bash
cd /srv/edsys/edsys-infrastructure
sudo scripts/ops/deploy-netdata-compute.sh --apply
```

The installer:

1. Preflights SSH and child-to-parent LAN reachability.
2. Stores private pre-change configuration under
   `/var/backups/edsys-netdata-compute/<UTC timestamp>/` on every affected host.
3. Aligns the four Debian 13 Proxmox hosts to the signed Netdata edge APT
   repository already used by `9950x` and `pve-node0`.
4. Configures exact hostnames and the shared `edsys-compute` label.
5. Restarts the Parent first and each Child individually.
6. Requires the exact five-node online topology before reporting success.

If configuration deployment fails, the script restores the prior
`netdata.conf` and `stream.conf` files and restarts Netdata. Package installation
is not automatically reversed; that avoids destructive package removal on a
Proxmox host.

## Verification

```bash
cd /srv/edsys/edsys-infrastructure
scripts/ops/deploy-netdata-compute.sh --check
```

The check fails unless:

- the node set is exactly the five names above;
- every node is reachable, with its alert engine either online or in the
  bounded post-restart `initializing` state;
- every node has `group=edsys-compute`;
- `9950x` reports Parent mode, five total nodes, and four receiving streams.

Direct API inspection remains available at:

```text
http://127.0.0.1:19999/api/v3/nodes
http://127.0.0.1:19999/api/v2/info
```

## Recovery

Review the timestamped backup on each host before rollback. Restore only the
configuration files recorded there, then restart `netdata`. Do not copy Netdata
Cloud private keys or streaming credentials into Git, tickets, chat, or the RAG
corpus.

The implementation follows Netdata's documented Parent/Child streaming model
and host-label organization model:

- <https://learn.netdata.cloud/docs/netdata-parents/parent-child-configuration-reference>
- <https://learn.netdata.cloud/docs/netdata-agent/configuration/organize-systems-metrics-and-alerts>
