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
