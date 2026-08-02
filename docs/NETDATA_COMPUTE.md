# Netdata Compute Topology

## Current Design

`9950x` is the single EdSys Netdata Parent. The four Proxmox hosts plus the
`edcore-ops`, `edcore-sdr`, and `netbox` Ubuntu satellites are Netdata Children and stream
to the parent over the LAN:

- `9950x` — parent and local collector
- `edcore-ops` — Ubuntu operations/Codex satellite
- `edcore-sdr` — Ubuntu SDR/RF observatory satellite
- `netbox` — Ubuntu inventory candidate with NetBox, Docker, and node_exporter metrics
- `pve-edcore` — child
- `pve-node0` — child
- `pve-node1` — child
- `pve-node2` — child

All eight nodes carry the host label `group=edsys-compute`. The authoritative
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

1. Preflights SSH and child-to-parent LAN reachability, including the
   separately provisioned `edcore-ops`, `edcore-sdr`, and `netbox` satellites.
2. Stores private pre-change configuration under
   `/var/backups/edsys-netdata-compute/<UTC timestamp>/` on every affected host.
3. Aligns the four Debian 13 Proxmox hosts and all three Ubuntu 24.04 satellites to
   the signed Netdata edge APT repository. Satellite web interfaces bind only
   to loopback; the deployer configures their outbound streams.
4. Configures exact hostnames and the shared `edsys-compute` label.
5. Sets the Parent's Docker collector to a 10-second cadence while retaining
   Docker service discovery and Docker/cgroup charts.
6. Restarts the Parent first and each Child individually.
7. Requires the exact eight-node topology and seven receiving streams before
   reporting success.

If configuration deployment fails, the script restores the prior
`netdata.conf`, `stream.conf`, and Parent `go.d/docker.conf` files and restarts
Netdata. Package installation is not automatically reversed; that avoids
destructive package removal on a Proxmox host.

The 2026-07-28 host performance sweep found that the stock one-second Docker
collector cadence made `dockerd` average 112.1% CPU while walking the 9950x's
160-image inventory. The Parent now collects the Docker job every 10 seconds.
A 30-second post-change sample averaged 13.8% `dockerd` CPU, and the exact
seven-node topology passed after the controlled Parent restart and the SDR
satellite addition. The later 2026-08-02 NetBox expansion added the eighth
node, loopback-only node_exporter, Docker/container collection, and NetBox
Prometheus collection. All eight Agents reported `v2.10.0-980-nightly`; the
strict eight-node/seven-receiver topology passed.

## Verification

```bash
cd /srv/edsys/edsys-infrastructure
scripts/ops/deploy-netdata-compute.sh --check
```

The check fails unless:

- the node set is exactly the eight names above;
- every node is reachable, with its alert engine either online or in the
  bounded post-restart `initializing` state;
- every node has `group=edsys-compute`;
- `9950x` reports Parent mode, eight total nodes, and seven receiving streams.

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
