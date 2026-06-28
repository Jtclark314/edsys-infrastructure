# EdSys AI Portal

Status: deployed on 9950x, Phase 1 MVP.

Private LiteLLM-backed operator UI for EdSys. The app repository lives at `/home/jeremy/code/edsys-ai-portal`; this folder only records the sanitized deployment shape and validation commands.

## Interfaces

| Service | URL |
| --- | --- |
| EdSys AI Portal | `http://192.168.50.50:3020` / `http://100.87.137.47:3020` |
| Health check | `http://192.168.50.50:3020/api/health` / `http://100.87.137.47:3020/api/health` |

There is no public internet exposure. Keep it LAN/Tailnet-only unless a separate auth/reverse-proxy review approves a change.

## Runtime Secrets and State

Do not commit runtime env files, LiteLLM keys, portal passwords, prompt history, logs, or SQLite databases.

Private runtime files on 9950x:

- `/home/jeremy/code/edsys-ai-portal/.env` — app runtime settings and Basic Auth password; ignored by Git.
- `/opt/edsys-workhorse/litellm/service-keys/edsys-ai-portal.env` — dedicated LiteLLM virtual key for this service.
- `/opt/edsys-workhorse/litellm/client-env/edsys-ai-portal.env` — recovery/client copy of the LiteLLM service-key env.
- `/opt/edsys-workhorse/edsys-ai-portal/data/` — future SQLite/export runtime data; Phase 1 history is disabled.

## Model Policy

The UI is allow-listed to current EdSys LiteLLM chat aliases only:

- `edsys-chat-local`
- `edsys-chat-cloud`
- `edsys-coder-local`
- `edsys-coder-cloud`
- `edsys-voice-fast`
- `edsys-voice-quality`

Cloud-capable routes require explicit confirmation when `REQUIRE_CLOUD_CONFIRMATION=true`.

## Deployment

Preferred standalone deployment from the app repo:

```bash
cd /home/jeremy/code/edsys-ai-portal
docker compose config --quiet
docker compose up -d --build
```

Equivalent sanitized infrastructure reference:

```bash
cd /srv/edsys/edsys-infrastructure
docker compose -f docker/edsys-ai-portal/compose.yaml config --quiet
docker compose -f docker/edsys-ai-portal/compose.yaml up -d --build
```

## Verification

```bash
curl -fsS http://192.168.50.50:3020/api/health
curl -fsS http://100.87.137.47:3020/api/health
# Authenticated status/chat checks should read the local ignored .env without printing the password.
```

Expected behavior:

- `/api/health` returns only `{ "status": "ok" }` and is unauthenticated.
- `/api/status`, `/api/models`, `/api/presets`, `/api/chat`, `/api/chat/stream`, and `/` require Basic Auth when enabled.
- Browser source contains no LiteLLM key or backend auth header.
- Portal logs include request ID, model, duration, success/failure, and usage summary only; no prompt/response body by default.

## Backup Notes

The source app and this deployment reference are Git-backed. Runtime secrets and future `/opt/edsys-workhorse/edsys-ai-portal/data/` state should only be protected through the encrypted/private backup path; never copy them into Git.
