# ARR Transfer Arbiter

Status: deployment source and operator contract for `arr-server`.

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

The daemon polls every two seconds. In `auto` mode:

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
guest installer: on `pve-node1`, VMID 301 `node1-services` is onboot order 1 and
VMID 200 `arr-vm` is onboot order 2, each with a 180-second startup delay. This
staggers recovery before the in-guest qBittorrent/SAB boot safeguards take over.

If installation fails after safety control begins, the error path reapplies the
hold and does not restore an unsafe auto-restart policy. Review the private
backup manifest before any rollback. Never restore only the old qBittorrent
restart policy without also arranging an equivalent fail-closed controller.

## Verification

```bash
sudo systemctl status arr-transfer-arbiter.service --no-pager
sudo systemctl status arr-transfer-arbiter-health.timer --no-pager
sudo arr-transfer-arbiter preflight
sudo arr-transfer-arbiter status --check
docker inspect -f '{{.State.Status}} paused={{.State.Paused}} restart={{.HostConfig.RestartPolicy.Name}}' qbittorrent
```

The main unit uses `Type=notify`, a 30-second systemd watchdog, bounded API and
Docker calls, `Restart=always`, and an `ExecStopPost` fail-safe. The health
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
