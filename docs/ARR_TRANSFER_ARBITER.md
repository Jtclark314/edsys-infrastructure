# ARR Transfer Arbiter

Status: deployment source and operator contract for `arr-server`.

## Completed torrent policy

As of 2026-09-05, the owner retains torrent downloading but stops seeding at
completion. qBittorrent persists `Session\GlobalMaxRatio=0`,
`Session\GlobalMaxSeedingMinutes=0`, and `Session\ShareLimitAction=Stop`.
The Web API readback is `max_ratio_enabled=true`, `max_ratio=0`,
`max_seeding_time_enabled=true`, `max_seeding_time=0`, and `max_ratio_act=0`.
Unset ARR indexer and per-torrent seeding limits inherit these defaults.

This is a qBittorrent completion policy, not a change to the arbiter invariant.
Normal operation remains `auto`; torrent downloading can still upload pieces
before completion. Keep the completion action at `Stop` so ARR can import
before removing completed torrent data. Do not automatically delete payloads
at the qBittorrent ratio boundary. Private pre-change configuration and job
metadata are retained on `arr-server` under
`/var/backups/edsys/arr-cleanup-20260906/`; restoring those settings would
re-enable the previous seeding behavior and requires owner authorization.

## Purpose

`arr-transfer-arbiter` prevents SABnzbd and qBittorrent from transferring at
the same time. SABnzbd has priority in `auto` mode. The safety invariant is:

> The controller must positively confirm that the current client is quiesced
> before it releases the other client.

The controller does not delete, move, recheck, or alter download jobs. It does
not stop Gluetun. qBittorrent is normally frozen with Docker pause/unpause so
its per-torrent state remains intact. SABnzbd is controlled with its supported
queue and post-processing pause APIs.

## Fail-closed design

- qBittorrent's Compose and effective Docker restart policy are `no`. Only the
  arbiter starts or unpauses it.
- SABnzbd persists `start_paused=1`, `pause_on_post_processing=1`, and
  `preserve_paused_state=0`. Disabling preservation is intentional: SAB's
  preservation feature would otherwise rewrite `start_paused` to `0` whenever
  the arbiter releases the live queue.
- A handoff to SABnzbd pauses and confirms qBittorrent first, then resumes the
  SAB queue and post-processing.
- A handoff to qBittorrent pauses and confirms both SAB mechanisms first, takes
  a fresh pause-state snapshot, and only then starts or unpauses qBittorrent.
- Any unavailable API or unconfirmed state quiesces qBittorrent and withholds
  permission to start it.
- An action or confirmation failure latches a persistent fault. While latched,
  the daemon repeatedly tries to hold both clients until an operator explicitly
  resets the fault.
- On daemon stop, crash, watchdog restart, or systemd restart,
  `ExecStopPost` reapplies the fail-safe hold.
- On host or Docker restart, qBittorrent cannot self-start and SABnzbd starts
  paused. The daemon then reconciles from that safe posture.
- The daemon repairs and verifies the SAB boot-safety values on startup and
  checks them for drift every 60 seconds.

The API key is read from the owner-only live SAB config at runtime. It is not
copied to an environment file, command line, status file, journal entry, or
Git. Status contains only pause flags and aggregate work counts.

SAB 5 reports its queue pause state directly but does not expose the live
manual post-processor pause flag. The controller therefore proves the latter
without guessing: it requires a successful synchronous `pause_pp` command for
the current SAB container instance, verifies that the instance did not restart
during the command, and requires the aggregate post-processing queue count to
be zero before releasing qBittorrent. A SAB container restart invalidates that
proof and forces a new fail-closed handoff.

## Automatic behavior

The daemon polls every five seconds. In `auto` mode:

1. If the SAB queue or post-processing has work, qBittorrent is quiesced and
   SAB is released.
   When `pause_on_post_processing=1` automatically holds SAB's download queue
   during an active post-processing job, the controller treats that as a valid
   SAB-active state. It leaves the automatic queue hold intact, keeps
   qBittorrent quiesced, and does not require the download queue to resume until
   post-processing finishes.
2. When SAB becomes empty, SAB keeps control for a continuous 60-second idle
   grace period. This avoids rapid switching between download and processing
   phases.
3. After the grace period, SAB's queue and post-processing are paused and
   confirmed, then qBittorrent is started or unfrozen.
4. A new SAB job remains safely queued while SAB is paused. On the next poll,
   qBittorrent is frozen before SAB is resumed.

## Operator modes

| Mode | Behavior |
| --- | --- |
| `auto` | SAB priority with a 60-second idle grace, then qBittorrent |
| `hold` | Pause or hold both clients |
| `sab-only` | Hold qBittorrent and release SAB |
| `qbit-only` | Pause SAB queue and post-processing, then release qBittorrent |

`qbit-only` remains fail-closed when SAB reports post-processing work; it does
not release qBittorrent merely because a pause command was accepted. Drain SAB
or return to `sab-only`, confirm an aggregate post-processing count of zero,
and then retry the controlled mode.

Set a mode and inspect the sanitized status:

```bash
sudo arr-transfer-arbiter set-mode hold
sudo arr-transfer-arbiter status --check
sudo arr-transfer-arbiter set-mode auto
```

Modes persist in `/var/lib/arr-transfer-arbiter/state.json`. Runtime status is
written atomically to `/run/arr-transfer-arbiter/status.json`.

## Fault recovery

Do not clear a fault before confirming the dependency and safe posture:

```bash
sudo arr-transfer-arbiter set-mode hold
sudo journalctl -u arr-transfer-arbiter.service -n 100 --no-pager
sudo arr-transfer-arbiter preflight
sudo arr-transfer-arbiter fail-safe
sudo arr-transfer-arbiter reset-fault
sudo arr-transfer-arbiter set-mode auto
sudo arr-transfer-arbiter status --check
```

`reset-fault` never starts a client directly. The running daemon applies the
selected mode on its next poll and repeats all safety confirmations.

## Installation

Review the implementation and tests, then run from a checked-out copy of this
repository on `arr-server`:

```bash
sudo scripts/ops/install-arr-transfer-arbiter.sh --enable
```

The installer:

1. Establishes a fail-safe hold before replacing files.
2. Backs up every changed live file under a root-only timestamped directory in
   `/var/backups/arr-transfer-arbiter/` and records an install manifest.
3. Persists and verifies the SAB boot-pause settings.
4. Narrowly changes only qBittorrent's Compose restart policy and validates the
   full Compose model.
5. Updates the existing container's effective restart policy to `no`.
6. Compiles the controller, verifies the systemd units, and enables the daemon
   plus a one-minute status-check timer.

The host-level boot prerequisite is managed in Proxmox rather than by this
guest installer. ARR VMID 200 now runs on EdCore `pve-node3`, onboot order 20
with a 180-second delay, after Home Assistant VM300 at order 10. VM301 remains
on node1 at its existing order 1. The in-guest qBittorrent/SAB boot safeguards
remain unchanged.

If installation fails after safety control begins, the error path reapplies the
hold and does not restore an unsafe auto-restart policy. Review the private
backup manifest before any rollback. Never restore only the old qBittorrent
restart policy without also arranging an equivalent fail-closed controller.

After migration, `edcore-control status` reports `arr_stack` alongside Home
Assistant and the lab. Its live status and existing helper tests passed.

## EdCore NVMe Capacity Alert

Install `config/netdata/health.d/edsys-node3-arr-pool.conf` only on node3 at
`/etc/netdata/health.d/edsys-node3-arr-pool.conf`, then run
`netdatacli reload-health`. It supplements the stock LVM alerts with warning
at 75% and critical at 85% shared thin-pool usage. Verify the named
`edsys_arr_nvme_pool_capacity` alarm through Netdata's local
`/api/v1/alarms?all` endpoint. Live parsing and clear-state evaluation passed.
Preserve at least 100 GiB pool headroom; the guest download filesystem's
free-space check is a separate limit. Logical disk sizes can exceed physical
pool capacity and must not be treated as reserved space.

## Verification

### Proxmox memory budget

As of 2026-09-06 UTC, ARR runs on NVMe-backed `pve-node3`:

| VM | Node | Current memory policy |
| --- | --- | --- |
| 200 `arr-vm` | pve-node3 | Fixed 12,288 MiB; `balloon=0`; four vCPU |
| 301 `node1-services` | pve-node1 | 4,096 MiB balloon target; `shares=0`; 16,384 MiB ceiling |

VM200's 280 GiB system and 300 GiB download disks use `local-lvm` with
`discard=on`, `iothread=1`, and `ssd=1`. Guest `fstrim.timer` is enabled.
All Docker state, download staging, repair, and unpacking moved with the VM;
Plex and NFS media storage remain on 9950x. With Home Assistant, ARR, Kali,
and Metasploitable all running, configured guest RAM totals 38 GiB on EdCore.
The lab remains off by default and isolated from ARR's `vmbr0` network.

The installed ZFS storage exporter supports only ZFS streams, so native
offline migration into LVM-thin is rejected. The completed migration used a
cold backup/restore with original VMID and MAC, after archive integrity,
cross-host checksum, and isolated restore/boot verification. The source VM
registration was preserved and removed under the Proxmox configuration lock;
original source ZFS disks remain offline for rollback. Never run duplicate
VM200 instances. Keep the verified off-host AI Store backup for at least seven
days after acceptance. Raw recovery artifacts remain private outside Git.

The source node's historical September 5 repair used a 6 GiB ARR balloon
target and 4 GiB VM301 target to prevent automatic memory growth on a roughly
16 GiB host. That ARR target is superseded by the fixed 12 GiB EdCore
allocation. Preserve VM301's existing `shares=0` policy on node1.

The previous two 8 GiB floors overcommitted memory before host and ZFS overhead.
Host swap reads from mechanical storage coincided with Docker control calls
exceeding their 15-second timeout and re-latching the safety fault. Check host
and guest swap activity, I/O pressure, actual VM allocations, and Docker
latency under sustained downloads before accepting a fault reset. Existing
swap occupancy alone does not prove ongoing pressure; correlate it with
swap-in/out rates and control-call latency.

Private pre-change VM configurations are retained under the root-only
`/var/backups/edsys/sab-pause-20260905/` directory on `pve-node1`. Review host
capacity before restoring larger allocations. Keep the controller's timeout,
watchdog, boot-pause settings, and mutual exclusion policy intact.

The matching host and ARR guest policy is
`config/sysctl/90-edsys-memory.conf`, installed as
`/etc/sysctl.d/90-edsys-memory.conf`. It sets `vm.swappiness=10` instead of
the prior value of 60 to reflect the high random-I/O cost of mechanical-disk
swap. Swap stays enabled. Apply only this file with `sysctl -p`, then check
`sysctl vm.swappiness`; do not reload unrelated sysctl configuration.
Rollback removes this dedicated file and restores `vm.swappiness=60`.
The [kernel documentation](https://docs.kernel.org/admin-guide/sysctl/vm.html#swappiness)
describes the tradeoff; this policy does not replace adequate RAM headroom.

### Controller checks

```bash
sudo systemctl status arr-transfer-arbiter.service --no-pager
sudo systemctl status arr-transfer-arbiter-health.timer --no-pager
sudo arr-transfer-arbiter preflight
sudo arr-transfer-arbiter status --check
docker inspect -f '{{.State.Status}} paused={{.State.Paused}} restart={{.HostConfig.RestartPolicy.Name}}' qbittorrent
```

The main unit uses `Type=notify`, a 60-second systemd watchdog, bounded API and
Docker calls, `Restart=always`, and an `ExecStopPost` fail-safe. The watchdog
window intentionally exceeds two consecutive bounded 15-second Docker calls,
so a transient Docker control-plane stall can be recorded as degraded and the
fail-closed posture can be reconciled before systemd kills the controller. The
five-second poll interval also avoids unnecessary Docker inspection pressure
during sustained download and post-processing I/O. The health
timer makes a latched or degraded controller visible as a failed oneshot unit
even when the long-running process remains alive to enforce the hold.
External notification routing for that failed-unit surface remains to be
confirmed separately; no credential or webhook is embedded in this deployment.

## Source files

- `scripts/ops/arr-transfer-arbiter.py`
- `scripts/ops/arr-transfer-arbiter.env.example`
- `scripts/ops/install-arr-transfer-arbiter.sh`
- `scripts/ops/systemd/arr-transfer-arbiter*.service`
- `scripts/ops/systemd/arr-transfer-arbiter-health.timer`
- `scripts/ops/tests/test_arr_transfer_arbiter.py`
