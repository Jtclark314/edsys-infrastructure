# Docker Stacks

Status: starting baseline.

Store deployable Docker Compose stacks here.

Recommended service folder:

```text
docker/service-name/
|-- compose.yaml
|-- .env.example
`-- README.md
```

Do not commit live `.env` files, Docker volumes, databases, uploads, logs, or backups.

## Current Stacks

- `9950x-core/` - pinned Compose ownership for Portainer, Plex, Tautulli, and Open WebUI; private runtime environment values remain outside Git.
- `container-recovery/` - explicit five-tier desired-state manifest and systemd templates for non-destructive recovery after Docker daemon or host restarts.
- `9950x-workhorse/` - loopback-only EdSys + AI workhorse services for the 9950x. Runtime state lives under `/opt/edsys-workhorse`; the stack folder contains only sanitized Compose/config templates.
- `edsys-ai-portal/` - private LiteLLM-backed FastAPI/static operator UI on `192.168.50.50:3020` and `100.87.137.47:3020`; app code lives in `/home/jeremy/code/edsys-ai-portal`.
- `homepage-workhorse/` - second Homepage instance for the 9950x Workhorse/AI/Programming/Codex dashboard on port `3019`.
- `edsys-control-api/` - read-only EdSys source-of-truth API over `EdSys-Master` YAML.
