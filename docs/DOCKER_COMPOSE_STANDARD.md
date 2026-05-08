# Docker Compose Standard

Status: starting baseline.

Docker Compose stacks should be readable, explicit, and easy to recover.

## Compose Rules

- Use `compose.yaml` unless a service has an existing standard.
- Pin images where stability matters.
- Set restart policy intentionally.
- Use named volumes or explicit bind mounts.
- Keep secrets in `.env`, not in `compose.yaml`.
- Include health checks where useful.
- Avoid exposing ports unless the service needs LAN access.
- Document backup paths and restore behavior.

## Service Folder Minimum

```text
service-name/
|-- compose.yaml
|-- .env.example
`-- README.md
```

Add scripts or templates only when they reduce operational risk.
