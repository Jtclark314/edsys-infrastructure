# EdSys Infrastructure Docs

Status: starting baseline.

This folder documents deployment standards for EdSys infrastructure. It should stay implementation-focused and should point high-level source-of-truth notes back to `EdSys-Master`.

## Index

- `DEPLOYMENT_MODEL.md`
- `SERVICE_DEFINITION_STANDARD.md`
- `DOCKER_COMPOSE_STANDARD.md`
- `CONFIG_TEMPLATE_STANDARD.md`
- `../docker/9950x-workhorse/README.md` - loopback-only 9950x workhorse stack for EdSys + AI services, observability, backup UI, notifications, and dependency-update templates.
- `../docker/homepage-workhorse/README.md` - second Homepage instance for the Workhorse/AI/Programming/Codex dashboard.
- `../docker/edsys-ai-portal/README.md` - private LiteLLM-backed EdSys operator UI.
- `../docker/edsys-control-api/README.md` - read-only API and dashboard over the EdSys-Master source-of-truth YAML.
- `../scripts/backup/README.md` - Google Drive offsite backup tooling using `9950x`, `restic`, and `rclone`.
- `../scripts/deploy/README.md` - deployment preparation helpers.
- `../scripts/ops/README.md` - operations/report helpers.
- `CONTAINER_RECOVERY.md` - ordered 9950x Docker recovery architecture and operations.

## Rule

Use this repo for deployable definitions and repeatable commands. Do not store runtime data or secrets here.
