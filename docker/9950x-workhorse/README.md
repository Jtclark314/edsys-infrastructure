# 9950x Workhorse Stack

Status: active 9950x workhorse stack. Runtime secrets and data live outside Git under `/opt/edsys-workhorse`; this folder contains only sanitized Compose/config templates.

## Interfaces

Operator-facing human/admin interfaces bind to the 9950x LAN and Tailnet addresses only. They are not public-internet exposed; keep Cloudflare/Tailscale Funnel exposure out of scope until a separate auth review.

| Service | Purpose | Initial URL |
| --- | --- | --- |
| LiteLLM | EdSys AI broker for local Ollama, Infinity embeddings, and DB/UI-managed OpenAI chat/Codex models | `http://192.168.50.50:4000` / `http://100.87.137.47:4000` |
| Langfuse | LLM tracing/observability | `http://192.168.50.50:3012` / `http://100.87.137.47:3012` |
| Dozzle | Live Docker log viewer | `http://192.168.50.50:3013` / `http://100.87.137.47:3013` |
| Healthchecks | Timer/job heartbeat dashboard | `http://192.168.50.50:3014` / `http://100.87.137.47:3014` |
| ntfy | Local notification endpoint | `http://192.168.50.50:3015` / `http://100.87.137.47:3015` |
| Scrutiny | SMART disk health dashboard | `http://192.168.50.50:3016` / `http://100.87.137.47:3016` |
| SearXNG | Private metasearch for local workflows | `http://192.168.50.50:3017` / `http://100.87.137.47:3017` |
| Karakeep | Bookmark/read-it-later knowledge capture | `http://192.168.50.50:3018` / `http://100.87.137.47:3018` |
| Backrest | Restic backup browser/orchestrator UI | `http://192.168.50.50:9898` / `http://100.87.137.47:9898` |
| Loki/Alloy | Local container log pipeline | `http://192.168.50.50:3100` / `http://100.87.137.47:3100` |

## Deployment

```bash
cd /srv/edsys/edsys-infrastructure
scripts/deploy/prepare-9950x-workhorse.sh
docker compose -f docker/9950x-workhorse/compose.yaml config --quiet
docker compose -f docker/9950x-workhorse/compose.yaml up -d
```

Optional local timer heartbeat bootstrap:

```bash
scripts/ops/bootstrap-healthchecks.sh
```

That script creates local Healthchecks records, private root-readable files under `/etc/edsys-healthchecks/`, systemd drop-ins for known EdSys timers, and reloads systemd. It does not print ping URLs or login values.

Renovate is configured with a `manual` Compose profile because it requires a local GitHub token in the ignored `.env` file. It is PR-only/no-automerge by default.

CrowdSec is deployed detection-only. There is no firewall, reverse-proxy, or SSH bouncer in this stack; add blocking only after baseline review.


## LiteLLM broker operations

LiteLLM keeps `store_model_in_db=true`; models added through the UI/database, including OpenAI-backed chat and Codex entries, must be preserved during config changes. The sanitized config adds local model routes plus `router_settings.model_group_alias` aliases for EdSys client use. Do not put provider API keys in this config.

Private runtime artifacts live outside Git:

- `/opt/edsys-workhorse/litellm/snapshots/` — DB/model registry snapshots before broker changes.
- `/opt/edsys-workhorse/litellm/service-keys/` — service-scoped LiteLLM key files for client apps.
- `/opt/edsys-workhorse/litellm/client-env/` — root-readable env files used when recreating Open WebUI/AnythingLLM with LiteLLM routing.

Validation commands:

```bash
cd /srv/edsys/edsys-infrastructure
docker compose -f docker/9950x-workhorse/compose.yaml config --quiet
docker compose -f docker/9950x-workhorse/compose.yaml restart litellm
# Use a private service key from /opt/edsys-workhorse/litellm/service-keys/ for authenticated smoke tests.
```

Recurring broker smoke testing is installed on 9950x as `edsys-litellm-broker-smoke.timer`. The script source is `scripts/ops/litellm-broker-smoke.sh`; the installed command is `/usr/local/sbin/edsys-litellm-broker-smoke`.

## Backup and recovery notes

- Runtime state: `/opt/edsys-workhorse` plus named Docker volumes for database-heavy components. LiteLLM broker snapshots/service keys under `/opt/edsys-workhorse/litellm/` are private runtime recovery material and must stay out of Git.
- Backrest receives read-only mounts for existing EdSys backup/source paths in this first pass.
- Add explicit backup classifications to `EdSys-Master` once each service is accepted into the long-term baseline.
- Do not commit generated `.env`, live settings, Docker volumes, logs, databases, or UI-generated exports.

## Reachability

Operator-facing workhorse UI ports bind to `WORKHORSE_LAN_BIND_IP` and `WORKHORSE_TAILSCALE_BIND_IP` only, currently `192.168.50.50` and `100.87.137.47`. This fixes browser links from other machines while avoiding public internet exposure. Keep database/collector-only ports loopback or Docker-internal unless separately reviewed.
