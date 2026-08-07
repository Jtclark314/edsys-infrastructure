# EdSys Fleet Autopilot v2

Fleet Autopilot is the durable operations control plane behind the Workhorse
AI Portal. It inventories every Codex host, detects approved-release drift,
executes explicit component transactions, proves rollback, runs capability
benchmarks, and preserves observable evidence without reducing Codex authority.

Codex remains configured for `danger-full-access`, network access, login
shells, remote control, approval Never, `gpt-5.6-sol`, Ultra reasoning, and
Priority processing. Fleet approvals protect owner intent and recovery
integrity; they are not a sandbox or a reduced-permission profile.

## Architecture

```text
Workhorse AI Portal (loopback backend behind Tailscale Serve HTTPS)
  identity, plans, approvals, SSE replay, recovery browser, benchmark UI
                              |
                              v
/data/fleet/fleet-control.sqlite (WAL, FULL sync, migrations, busy timeout)
  transactions, jobs, hash-chained events, approvals, recovery points,
  adapter qualifications, benchmarks, agent enrollment/heartbeats
                              |
                              v
9950x user services
  edsys-fleet-worker       exclusive mutation locks and phase reconciliation
  edsys-fleet-collect      five-minute inventory and offline-agent cleanup
  edsys-fleet-benchmark-*  deterministic daily / real Codex Ultra weekly
  edsys-fleet-backup       encrypted backup plus isolated restore test
                              |
        +---------------------+-----------------------+
        |                     |                       |
 local / key-only SSH   signed outbound agent   official pvesh over SSH
 9950x, Nimo,           employer-policy Dell    four-node Proxmox cluster
 Basecamp, edcore-ops   (no inbound port)       and VMID 390 canary
```

The Portal container never receives SSH keys, Proxmox credentials, the Docker
socket, or host administration mounts. Runtime databases, candidate bundles,
signing keys, benchmark artifacts, logs, and rollback material remain private
and outside Git.

## Durable transaction contract

The state machine is:

```text
planned -> preflight -> awaiting_approval -> approved -> checkpointing
        -> applying -> restarting -> verifying -> observing -> accepted

failed -> rollback_pending -> rolling_back -> rolled_back
                                  \-> manual_intervention_required
```

Every adapter implements `discover`, `resolve_candidate`, `preflight`,
`checkpoint`, `apply`, `restart_or_reboot`, `verify`, `accept`, `rollback`, and
`cleanup`. Generic adapters accept only an approved immutable manifest with
exact argv, candidate and rollback SHA-256 identities, and all ten phases.
Executable manifests reject credential-like fields and are stored exactly;
only bounded event evidence is sanitized and truncated.

An adapter is **not qualified** merely because its implementation exists. It
must perform a real rollback, verify the prior state, reapply the candidate,
and pass final verification. Normal transactions fail closed until that
host/component qualification is recorded. A worker restart replays only
idempotent phases; an interrupted mutation becomes manual intervention rather
than an unknown retry.

Read-only work may run concurrently. Mutations hold an exclusive per-host
lock, and reboot-class work also holds one global lock. Cancellation is
immediate before mutation; after mutation it waits for a phase boundary and
uses the already approved automatic component rollback.

## Policy and adapters

`config/fleet-policy.yml` is the operator policy and
`edsys_fleet/fleet-policy.yml` is the packaged copy. Tests require them to be
byte-identical. Policy v2 declares applicable hosts, discovery/candidate
method, desired release/channel, risk class, absence semantics, reboot and
observation requirements, and all lifecycle support for:

- Fleet Portal, Linux host agent, and Windows pull agent;
- Proxmox MCP and disposable guest lifecycle;
- Linux/Windows Codex, curated plugins, and individual MCP configuration;
- atomic Node/npm, npm global tools, Playwright MCP/Test;
- Chrome, Vivaldi, Firefox, launchers, and profiles;
- Docker Engine/Desktop, NVIDIA Container Toolkit, Ollama, and qualified
  digest-pinned Compose stacks;
- Linux packages/kernels, NVIDIA drivers, Nimo Windows Update, Proxmox
  packages/kernels, and controlled host reboot;
- Dell Windows Update as inventory-only unless employer policy authorizes it.

The optimized Vivaldi GPU/WebGPU launcher is outside automatic mutation and
must remain byte-for-byte unchanged unless the owner separately approves a
launcher edit.

## Capability benchmark

The versioned contract is `edsys_fleet/capability-contract.yml`.

- **Daily deterministic:** 05:00 Eastern, no model call.
- **Weekly Ultra:** Sunday 03:00 Eastern, real `gpt-5.6-sol`, Ultra, Priority,
  live web, approval Never, and danger-full-access.

The deterministic suite proves Chrome/Playwright upload-download/WebP/WebGPU,
all required MCP paths, NVIDIA/Vulkan/NVENC and NVIDIA containers, Docker,
remote hosts, document generation/rendering, Codex authority, and Proxmox
quorum plus VMID 390 snapshot rollback/cleanup. VMID 390 `fleet-canary` is an
unprivileged, networkless, no-production-data LXC on `pve-edcore`; it is also
tagged as a benchmark canary in NetBox.

Raw benchmark artifacts are retained 30 days, detailed results and audit
events two years, and compact trends/transaction summaries indefinitely. The
first critical failure uses the configured private EdSys notification path.
Benchmark failure blocks component acceptance and never reduces Codex
permissions.

## Dell outbound agent

`windows/dell-agent/` builds a signed Windows agent with:

- locally generated Ed25519 identity protected by DPAPI and Windows ACLs;
- signed nonce/timestamp/body-hash requests over Tailnet HTTPS;
- one-command ordered delivery, replay rejection, and approval expiry;
- no inbound listener or dependency on corporate-laptop SSH;
- signed bundle verification and exact-argv guarded component transactions;
- offline inventory/benchmark jobs and automatic rollback for approved
  mutations;
- a highest-permitted boot/logon Scheduled Task.

The Dell remains inventory-only until its employer policy, Windows support,
local administration, and `AllowMutations` setting are explicitly accepted.

## Install, inspect, benchmark, and recover

```bash
cd /srv/edsys/edsys-infrastructure/services/fleet-autopilot
./install.sh

edsys-fleet db-check
edsys-fleet collect
edsys-fleet show
edsys-fleet components
edsys-fleet transactions
edsys-fleet gates
edsys-fleet benchmark --suite deterministic --host 9950x --triggered-by cli
edsys-fleet db-backup-restore-test
systemctl --user status edsys-fleet-worker.service edsys-fleet-collect.timer
```

`install.sh` delegates to `tools/fleet-self-update.py`. Promotion requires a
clean authoritative `origin/main`, signs and hashes the candidate, stages an
atomic release, arms an external systemd watchdog, validates policy/database/
worker state, restores the exact prior links and units, proves that old worker,
then reapplies v2 and validates again. Its private run directory remains a
usable component recovery point; `rollback --run-dir ...` can restore it even
after acceptance.

Encrypted database backups use a private age identity, retain 35 recent
artifacts, decrypt into an isolated temporary path, run SQLite integrity/schema
checks, and only then prune detail beyond the two-year policy. The last working
recovery point is never automatically deleted.

## Proxmox MCP

The installed `edsys-proxmox-mcp` exposes:

- `proxmox_cluster_status`
- `proxmox_list_guests`
- `proxmox_guest_details`
- `proxmox_guest_action`
- `proxmox_create_snapshot`
- `proxmox_rollback_snapshot`
- `proxmox_delete_snapshot`

It validates nodes, VMIDs, guest types, actions, and snapshot names, then uses
the official `pvesh` interface over the existing key-only administrative
route. No API token is stored in the repository or Portal.

## Validation

```bash
uv run --with pytest --with pytest-asyncio pytest -q
uv run --with pytest --with PyYAML \
  pytest -q ../../scripts/netbox/tests/test_netbox_sync.py

cd windows/dell-agent
gofmt -w main.go dpapi_windows.go
GOOS=windows GOARCH=amd64 go build .
```

Do not claim a component qualified or a Fleet score of 10/10 from source tests
alone. Qualification, benchmark, identity, observation, Dell enrollment,
fault-injection, reboot, recovery, and documentation gates require live,
observable evidence.
