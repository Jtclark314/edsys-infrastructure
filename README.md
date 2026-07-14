# EdSys Infrastructure

EdSys Infrastructure is the implementation-focused repo for deployable EdSys infrastructure definitions. Use it for Docker Compose stacks, deployment scripts, service templates, config templates, and practical deployment notes.

This repo should describe how services are deployed, but it must not contain live secrets, runtime databases, uploads, logs, backups, Docker volumes, or private machine state.

## Start Here

- [AI context](AI_CONTEXT.md)
- [Docs index](docs/README.md)
- [Deployment model](docs/DEPLOYMENT_MODEL.md)
- [Service definition standard](docs/SERVICE_DEFINITION_STANDARD.md)
- [Docker Compose standard](docs/DOCKER_COMPOSE_STANDARD.md)
- [Config template standard](docs/CONFIG_TEMPLATE_STANDARD.md)
- [9950x full-host reboot acceptance](docs/REBOOT_ACCEPTANCE.md)

## Folder Map

```text
edsys-infrastructure/
|-- AI_CONTEXT.md
|-- README.md
|-- docs/
|   |-- README.md
|   |-- DEPLOYMENT_MODEL.md
|   |-- SERVICE_DEFINITION_STANDARD.md
|   |-- DOCKER_COMPOSE_STANDARD.md
|   `-- CONFIG_TEMPLATE_STANDARD.md
|-- docker/
|   `-- README.md
|-- config/
|   `-- README.md
|-- scripts/
|   `-- README.md
`-- templates/
    |-- docker-compose.service-template.yml
    |-- service.env.example
    `-- service-definition.example.yml
```

## What Belongs Here

- Docker Compose stacks and service folders.
- Deployment scripts and helper scripts.
- Config templates with placeholders only.
- `.env.example` files.
- Service implementation docs.
- Operational commands that are safe to run after review.

## What Does Not Belong Here

- Real `.env` files.
- Passwords, tokens, API keys, private keys, Cloudflare tunnel tokens, or VPN credentials.
- SQLite databases, app uploads, media libraries, logs, backups, Docker volumes, or generated runtime state.
- Sanitized config snapshots that are not deployment sources. Those belong in `edsys-infra-configs`.

## Relationship To Other EdSys Repos

- `EdSys-Master`: top-level sanitized knowledgebase, network map, service catalog, and AI entrypoint.
- `edsys-infrastructure`: deployable definitions, scripts, templates, and implementation docs.
- `edsys-infra-configs`: sanitized known-good snapshots and generated audits for recovery/comparison.

## Safety Note

Use placeholders and examples in Git. Load real secrets only from local `.env` files, host-level secret stores, Cloudflare, Tailscale, or another private system outside the repo.
