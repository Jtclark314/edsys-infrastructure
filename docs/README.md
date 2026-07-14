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
- `../scripts/backup/README.md` - Google Drive offsite backup tooling using `9950x`, `restic`, and `rclone`.
- `../scripts/security/README.md` - fail-closed 9950x SSH source/interface guard and capability-preserving forwarding policy.
- `../scripts/network/README.md` - FreeBind systemd socket proxy for exact-address Tailnet AI access without Docker boot dependency on `tailscale0`.
- `../scripts/deploy/README.md` - deployment preparation helpers.
- `../scripts/ops/README.md` - operations/report helpers.
- `CONTAINER_RECOVERY.md` - ordered 9950x Docker recovery architecture and operations.
- `REBOOT_ACCEPTANCE.md` - one-shot full-host reboot recovery and acceptance gate.

## Rule

Use this repo for deployable definitions and repeatable commands. Do not store runtime data or secrets here.
