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

- `9950x-workhorse/` - loopback-only EdSys + AI workhorse services for the 9950x. Runtime state lives under `/opt/edsys-workhorse`; the stack folder contains only sanitized Compose/config templates.
- `homepage-workhorse/` - second Homepage instance for the 9950x Workhorse/AI/Programming/Codex dashboard on port `3019`.
- `edsys-control-api/` - read-only EdSys source-of-truth API over `EdSys-Master` YAML.
