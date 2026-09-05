# EdSys Infrastructure Docs

Status: starting baseline.

This folder documents deployment standards for EdSys infrastructure. It should stay implementation-focused and should point high-level source-of-truth notes back to `EdSys-Master`.

## Index

- `DEPLOYMENT_MODEL.md`
- `SERVICE_DEFINITION_STANDARD.md`
- `DOCKER_COMPOSE_STANDARD.md`
- `CONFIG_TEMPLATE_STANDARD.md`
- `../docker/9950x-workhorse/README.md` - LAN/Tailnet-scoped 9950x workhorse stack for EdSys + AI services, observability, backup UI, notifications, and dependency-update templates.
- `../docker/9950x-ai/README.md` - shared Ollama, Qdrant, Infinity, and Wyoming AI/voice foundations with explicit host bindings.
- `../docker/anythingllm/README.md` - AnythingLLM deployment with private runtime env references and explicit host bindings.
- `../docker/edsys-glasses-gateway/README.md` - tracked host-binding wrapper for the private glasses-gateway source and runtime state.
- `../docker/homepage-workhorse/README.md` - second Homepage instance for the Workhorse/AI/Programming/Codex dashboard.
- `../docker/edsys-ai-portal/README.md` - private LiteLLM-backed EdSys operator UI.
- `../docker/edsys-control-api/README.md` - read-only API and dashboard over the EdSys-Master source-of-truth YAML.
- `../docker/edsys-code-intelligence/README.md` - hardened, loopback-only committed-code search, CPU reranking, local review MCP, and guarded index timers for Codex.
- `../docker/ask-foothills/README.md` - planned read-only Ask Foothills service on 9950x; production activation remains authorization-gated.
- `../scripts/backup/README.md` - Google Drive offsite backup tooling using `9950x`, `restic`, and `rclone`.
- `../scripts/security/README.md` - fail-closed 9950x SSH source/interface guard and capability-preserving forwarding policy.
- `../scripts/network/README.md` - FreeBind systemd socket proxy for exact-address Tailnet AI access without Docker boot dependency on `tailscale0`.
- `../scripts/deploy/README.md` - deployment preparation helpers.
- `../scripts/ops/README.md` - operations/report helpers.
- `../services/kali-lab/README.md` - current pve-node3 isolated Kali and
  Metasploitable lab deployment, containment, verification, and recovery source.
- `../scripts/sdr/README.md` - historical retired EdCore SDR receiver source.
- `CONTAINER_RECOVERY.md` - ordered 9950x Docker recovery architecture and operations.
- `REBOOT_ACCEPTANCE.md` - one-shot full-host reboot recovery and acceptance gate.
- `EDCORE_CONTROL_PLANE.md` - current 9950x-to-pve-node3 Proxmox, HAOS, and isolated-lab control plane.
- `ARR_TRANSFER_ARBITER.md` - fail-closed SABnzbd/qBittorrent mutual exclusion and operator controls.
- `NETDATA_COMPUTE.md` - authoritative nine-node Netdata Parent/Child deployment and recovery procedure.
- `../docker/netbox/README.md` - production NetBox platform, access, backup, restore, and upgrade boundary.
- `../scripts/netbox/README.md` - plan-gated discovery, reconciliation, validation, and sanitized export.
- `../services/fleet-autopilot/README.md` - cross-host inventory/drift collector, guarded Portal action worker, and private Proxmox stdio MCP.
- `../services/music-assistant/README.md` - read-only NFS source that lets the
  official HAOS Music Assistant app index the `9950x` music tree independently
  of Plex.

- `../services/3d-printing/README.md` - 9950x mount-guarded portable CAD/modeling/slicer stack, isolated dependency locks, offline qualification and selective rollback.

## Rule

Use this repo for deployable definitions and repeatable commands. Do not store runtime data or secrets here.
