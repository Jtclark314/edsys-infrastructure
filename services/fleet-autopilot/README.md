# EdSys Fleet Autopilot Host Agent

The host agent supplies the Workhorse AI Portal Fleet workspace with live,
sanitized inventory while retaining full administrative execution on the
canonical `9950x` host. It also installs the private EdSys Proxmox MCP.

## Architecture

```text
Workhorse AI Portal container
  /data/fleet atomic snapshots and queue requests
                     |
                     v
9950x user service: edsys-fleet-worker
  local commands, key-only SSH, PowerShell, Docker, Codex transaction helpers
                     |
                     +--> 9950x / Nimo / Basecamp / edcore-ops / work laptop
                     +--> Proxmox official pvesh API through pve-node0

Codex stdio MCP: edsys-proxmox-mcp
  cluster, guest, snapshot, lifecycle, and rollback tools
```

The Portal container never receives SSH keys, Proxmox credentials, Docker
socket access, or host administration mounts. Runtime snapshots, jobs, logs,
and private rollback material remain outside Git.

## Commands

```bash
./install.sh
edsys-fleet collect
edsys-fleet show
edsys-fleet jobs
edsys-fleet queue inspect
systemctl --user status edsys-fleet-worker.service edsys-fleet-collect.timer
```

`config/fleet-policy.yml` is the operator-facing policy and
`edsys_fleet/fleet-policy.yml` is the installed package copy. Keep them
byte-identical; the test suite fails if they drift.

## Proxmox MCP tools

- `proxmox_cluster_status`
- `proxmox_list_guests`
- `proxmox_guest_details`
- `proxmox_guest_action`
- `proxmox_create_snapshot`
- `proxmox_rollback_snapshot`
- `proxmox_delete_snapshot`

The MCP uses the official `pvesh` shell interface over the already accepted
key-only administrative SSH route. It does not store an API token in Git or
inside the Portal. The tracked package pins the accepted stable MCP Python SDK
version so reinstalling the worker cannot silently cross a protocol/runtime
compatibility boundary.

## Action behavior

- **Inspect** refreshes the complete snapshot.
- **Verify** refreshes and evaluates reachability plus critical drift.
- **Upgrade** executes a staged Codex transaction when a run ID is supplied;
  other component types receive an explicit checkpoint/acceptance plan until a
  deterministic adapter is registered.
- **Roll Back** requires an explicit Codex transaction or Proxmox snapshot ID.
- Proxmox guest power and snapshot actions submit directly to the cluster and
  return the Proxmox task identifier.
