# Deployment Model

Status: starting baseline.

EdSys is local-first. Services should be deployable from clear local definitions, with runtime state and secrets supplied outside Git.

## Preferred Pattern

Each service should have:

- A folder under `docker/` or another clear deployment area.
- A `compose.yml` or `compose.yaml` when Docker Compose is used.
- A `.env.example` with placeholders only.
- A local `.env` ignored by Git for real values.
- A service README with start, stop, update, backup, restore, and verification notes.

## Storage Rule

Do not commit runtime data. Docker volumes, databases, uploads, backups, media, and generated logs belong outside Git and should have a separate backup plan.

## Remote Access

Cloudflare and Tailscale details may be documented at a high level, but tunnel tokens, credentials, and private keys must remain outside Git.
