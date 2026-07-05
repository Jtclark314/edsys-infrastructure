# EdSys Workhorse AI Portal

Status: deployed on 9950x, Phase 1 backend plus Mission Control React redesign work in progress, grounded RAG enforcement, live web grounding, prompt-aware Model Router, Workhorse observability, and v1 safe agent/RAGOps APIs.

Private LiteLLM-backed operator UI for EdSys. The app repository lives at `/home/jeremy/code/edsys-ai-portal`; this folder only records the sanitized deployment shape and validation commands.

The app repo now builds a Vite + React + TypeScript + Tailwind **Mission Control** frontend into `app/static/app/`. FastAPI serves the compiled static assets with the legacy static page as fallback. Production still uses one container; Node exists only in the Docker build stage and there is no separate Node runtime service.

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
- `/mnt/ai-store/rag/evals/` — RAGOps eval result JSON files written by the host-side eval helper.
- `http://192.168.50.50:3017` — private SearXNG provider for live web grounding; JSON format must remain enabled in the workhorse SearXNG settings.
- `http://192.168.50.50:3012` — private Langfuse instance initialized with the `workhorse-ai-portal` project.
- `http://192.168.50.50:3100` — private Loki endpoint populated by Grafana Alloy.
- `http://192.168.50.50:8099` — read-only EdSys Control API used by v1 safe agent tools.

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

## Mission Control UI

The operator surface is organized by workspace instead of one long page:

- Overview, Command, Systems, Models, Knowledge, Observability, Agents, Voice, and Evaluations.
- Dense lists use paginated/filterable tables for services, models, logs, traces, tools, sources, and eval summaries.
- UI aggregation endpoints are metadata-focused and bounded: `/api/ui/overview`, `/api/control/summary`, `/api/control/search`, `/api/control/health`, `/api/rag/evals/summary`, and `/api/voice/latency/summary`.
- Write-like actions remain proposal-only; the Evaluations workspace summarizes previous results and copyable CLI commands but does not execute cloud evals from the browser.

## Safe Agent and RAGOps APIs

V1 agent tools are safe by default:

- `/api/tools` returns the tool catalog and safety policy.
- `/api/agent/run` and `/api/agent/run/stream` use LiteLLM/OpenAI-compatible tool calling to select one tool, then execute one read-only tool call or return a proposal for write-like/disabled actions.
- Enabled read-only tools cover EdSys Control API summary/search/health, current RAG search, Workhorse status/logs/trace lookup.
- ntfy summary publishing is side-effectful and requires explicit confirmation.
- Foothills ASI and Obsidian update adapters are present only as disabled/proposal paths until live state and write approval are reviewed.

RAGOps golden queries live in `/home/jeremy/code/EdSys-Master/data/rag-golden-queries.yml`. Run evals from the app repo virtualenv; cloud or DeepEval judges require explicit `--cloud-confirmed` and a service-scoped eval key outside Git.

Voice endpoints are measurement/proposal only:

- `/api/voice/latency` records wake/STT/first-token/TTS/playback timings to runtime JSONL.
- `/api/voice/intent` classifies simple edge intents but never executes Home Assistant actions in v1.

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

The app `Dockerfile` is multi-stage: Node builds the frontend bundle first, then the Python image copies the compiled bundle into `/app/app/static/app/` and serves it through FastAPI.

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
- `/api/tools` lists read-only tools plus disabled/proposal-only ASI/Obsidian adapters.
- `POST /api/agent/run` with "Check LiteLLM status" uses a read-only Control API path and does not call a write executor.
- `POST /api/voice/intent` for a lights command returns `action_allowed=false`.

## Backup Notes

The source app and this deployment reference are Git-backed. Runtime secrets and future `/opt/edsys-workhorse/edsys-ai-portal/data/` state should only be protected through the encrypted/private backup path; never copy them into Git.
