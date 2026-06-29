# EdSys Workhorse AI Portal

Status: deployed on 9950x, Phase 1 MVP plus grounded RAG enforcement, live web grounding, prompt-aware Model Router, and Workhorse observability.

Private LiteLLM-backed operator UI for EdSys. The app repository lives at `/home/jeremy/code/edsys-ai-portal`; this folder only records the sanitized deployment shape and validation commands.

## Interfaces

| Service | URL |
| --- | --- |
| Workhorse AI Portal | `http://192.168.50.50:3020` / `http://100.87.137.47:3020` |
| Health check | `http://192.168.50.50:3020/api/health` / `http://100.87.137.47:3020/api/health` |

There is no public internet exposure. Keep it LAN/Tailnet-only unless a separate auth/reverse-proxy review approves a change.

## Runtime Secrets and State

Do not commit runtime env files, LiteLLM keys, portal passwords, prompt history, logs, or SQLite databases.

Private runtime files on 9950x:

- `/home/jeremy/code/edsys-ai-portal/.env` — app runtime settings and Basic Auth password; ignored by Git.
- `/opt/edsys-workhorse/litellm/service-keys/edsys-ai-portal.env` — dedicated LiteLLM virtual key for this service.
- `/opt/edsys-workhorse/litellm/client-env/edsys-ai-portal.env` — recovery/client copy of the LiteLLM service-key env.
- `/opt/edsys-workhorse/edsys-ai-portal/data/` — future SQLite/export runtime data; Phase 1 history is disabled.
- `/mnt/ai-store/rag/grounding/edsys-grounding.sqlite` — current-only grounding index mounted read-only at `/data/rag/edsys-grounding.sqlite`.
- `http://192.168.50.50:3017` — private SearXNG provider for live web grounding; JSON format must remain enabled in the workhorse SearXNG settings.
- `http://192.168.50.50:3012` — private Langfuse instance initialized with the `workhorse-ai-portal` project.
- `http://192.168.50.50:3100` — private Loki endpoint populated by Grafana Alloy.

## Model Policy

The UI is allow-listed to the virtual Auto Router, grounded Portal modes, and current EdSys LiteLLM chat aliases:

- `edsys-auto` → default new-chat router selection; backend chooses the effective alias
- `edsys-grounded-local` → `edsys-chat-local`, RAG required
- `edsys-grounded-cloud` → `edsys-chat-cloud`, RAG required
- `edsys-chat-local`
- `edsys-chat-cloud`
- `edsys-coder-local`
- `edsys-coder-cloud`
- `edsys-voice-fast`
- `edsys-voice-quality`

Cloud-capable routes require explicit confirmation when `REQUIRE_CLOUD_CONFIRMATION=true`. EdSys factual/admin prompts are grounded automatically and are blocked if the current-only index is missing, stale, or lacks matching sources.

The backend Model Router runs before grounding/LiteLLM calls:

- EdSys factual/admin prompts stay on current RAG first.
- Current-events, news, weather, market, sports, “latest”, “today”, and similar fresh-knowledge prompts recommend `edsys-chat-cloud` and return a cloud-confirmation challenge before the call.
- General external/current-knowledge prompts such as public-figure, company, product, release/version, pricing, and ranking questions recommend `edsys-chat-cloud`.
- Confirmed current/public routes require live SearXNG web grounding before LiteLLM answers; streaming emits `web_grounding` source cards and the model is instructed to cite `[W#]` sources.
- Coding prompts can route to coder aliases; privacy-sensitive coding prompts prefer local coder.
- New chats reset to `edsys-auto` so stale manual model selection is not mistaken for the effective backend route.
- Streaming responses emit a `routing` event before optional `grounding`, optional `web_grounding`, and model deltas.

## Workhorse Observability

The standard name for this operator surface is now **Workhorse AI Portal** / **Workhorse**. Observability is part of the app contract:

- `/api/status` includes Langfuse and Loki health.
- `/api/workhorse/status` returns the same focused Workhorse observability status.
- `/api/workhorse/logs` returns bounded/redacted Loki lines for allow-listed containers such as `edsys-ai-portal`, `workhorse-litellm`, `workhorse-langfuse`, `workhorse-loki`, and `workhorse-alloy`.
- `/api/workhorse/trace/{request_id}` resolves a Portal request ID to the final Langfuse trace URL after asynchronous OTEL ingestion.
- UI response cards show the request ID, Langfuse trace/list links, and a Loki request-log link.

Langfuse keys used for trace lookup are backend-only runtime secrets in the ignored `.env`; they must not be committed or rendered into browser JavaScript. LiteLLM message logging stays disabled so Langfuse stores redacted prompt/response placeholders plus model/router/usage metadata.

## Grounding Index

Build or refresh the index on the host before/after deployment:

```bash
/home/jeremy/bin/edsys-grounding-index
systemctl --user status edsys-rag-sync.timer
```

The user service `edsys-rag-sync.service` runs mirror sync, grounding index build, and AnythingLLM upload every five minutes.

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
- `/api/status`, `/api/models`, `/api/rag/status`, `/api/rag/search`, `/api/presets`, `/api/chat`, `/api/chat/stream`, and `/` require Basic Auth when enabled.
- Browser source contains no LiteLLM key or backend auth header.
- Portal logs include request ID, requested/selected/target model aliases, router decision, duration, success/failure, RAG/web source counts, and usage summary only; no prompt/response body by default.
- Current/public smoke prompts should show a live web source card. Example class: World Cup/USA standings prompts should route to `edsys-chat-cloud`, apply SearXNG web grounding, and not answer "I do not have live sports data."
- `/api/workhorse/status` reports Langfuse and Loki `ok`.
- `/api/workhorse/logs?container=edsys-ai-portal&limit=5` returns recent redacted Portal log lines.
- After a smoke chat, `/api/workhorse/trace/{request_id}` resolves to a Langfuse trace URL within a few seconds.

## Backup Notes

The source app and this deployment reference are Git-backed. Runtime secrets and future `/opt/edsys-workhorse/edsys-ai-portal/data/` state should only be protected through the encrypted/private backup path; never copy them into Git.
