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
| SearXNG | Private metasearch for local workflows and EdSys AI Portal live web grounding | `http://192.168.50.50:3017` / `http://100.87.137.47:3017` |
| Karakeep | Bookmark/read-it-later knowledge capture | `http://192.168.50.50:3018` / `http://100.87.137.47:3018` |
| Backrest | Restic backup browser/orchestrator UI | `http://192.168.50.50:9898` / `http://100.87.137.47:9898` |
| Loki/Alloy | Local container log pipeline | `http://192.168.50.50:3100` / `http://100.87.137.47:3100` |

## Deployment

```bash
cd /srv/edsys/edsys-infrastructure
scripts/deploy/prepare-9950x-workhorse.sh
mountpoint -q /mnt/ai-store
# Restore the private Seaweed identity file before starting the object store.
sudo install -d -o 1000 -g 1000 -m 0700 /mnt/ai-store/langfuse/seaweedfs
sudo install -d -o 101 -g 101 -m 0750 /mnt/ai-store/langfuse/clickhouse-backups
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

## Stable AI upgrade — November 13, 2026

The November 12 release audit was implemented with exact image digests:
LiteLLM 1.109.1, Langfuse web/worker 4.79.0, PostgreSQL 16.16/17.12,
ClickHouse 26.10.3.7, Redis 7.4.12, Valkey 8.1.11 and the dated SearXNG build
listed below. Database/cache maintenance stays on qualified compatible branches.
Core inference/RAG/speech and media have separate owning definitions under
`docker/9950x-ai`, `docker/anythingllm`, `services/local-coder` and `services/immich`.

LiteLLM passed official signing-key verification and an isolated migration before
promotion. Its 13 original scoped keys, six DB-managed models, 29 visible models,
30 metadata tables and all 106,876 checkpointed history IDs were retained.
Authenticated local chat, 384D float/base64 embeddings, scoped permission denial,
14 positive/10 negative production retrieval cases and a redacted Langfuse
callback passed. Configuration, aliases, provider connections and routes were
preserved. The old database/image state remains private recovery material.

Langfuse reached 3.227.1 first, with all required version 3 migrations complete,
then 4.79.0 after a fresh PostgreSQL/ClickHouse/Redis checkpoint. Five gated
version 4 backfill rows remain intentionally dormant. `legacy` writes, native
OTEL `dual_write`, disabled preview and disabled historic backfill preserve
existing SDK/API integrations and historical reads. Do not enable backfill while
legacy batch writes remain active. A future data-model cutover requires its own
producer/read/evaluator qualification. An image-only rollback across the schema
upgrade is not a recovery procedure; use the matching checkpoint and guide.

Docker requires `/mnt/data-500g` and `/mnt/ai-store` before future daemon starts,
with explicit mountpoint checks preceding auto-restart containers. Only systemd
was reloaded during commissioning; the Docker process was preserved. New object
and native-backup binds refuse missing source paths. Restore/provision their
directories/configuration on the actual AI Store before creating containers;
never initialize replacement data on a root-filesystem fallback.

## Workhorse observability for the AI Portal

Langfuse, Loki, and Grafana Alloy are the standard Workhorse observability path for the AI Portal:

- LiteLLM emits Langfuse OTEL traces with Workhorse/Portal tags and request metadata.
- LiteLLM `turn_off_message_logging=true` keeps prompts/responses redacted in Langfuse.
- SeaweedFS supplies the private `langfuse` S3 bucket, with original credentials,
  `events/` and `media/` prefixes and no host-published listener. See
  `services/workhorse-ai-maintenance/README.md` for migration/recovery acceptance.
- Alloy collects Docker container logs into Loki; the Portal only exposes bounded, redacted lookups for an allow-list of Workhorse containers.
- Loki's singleton Compactor enforces the configured seven-day filesystem retention policy. The 15-minute delete delay is selected for this single-process deployment, which has no separate index-gateway replicas, while avoiding unbounded local chunk growth; see Grafana's [retention documentation](https://grafana.com/docs/loki/latest/operations/storage/retention/).
- The Portal resolves Langfuse traces asynchronously by request ID because Langfuse ingestion is not immediate.

Smoke checks:

```bash
curl -fsS http://192.168.50.50:3012/api/public/health
curl -fsS http://192.168.50.50:3100/ready
# Object-store checks require the private S3 identity; never print it.
```

## SearXNG web grounding

SearXNG is used by EdSys AI Portal for current/public prompt grounding. Keep both `html` and `json` formats enabled in the private runtime settings so the human UI and Portal search client both work:

```yaml
search:
  formats:
    - html
    - json
```

Smoke test:

```bash
curl -fsS 'http://192.168.50.50:3017/search?q=USA%20FIFA%20World%20Cup%202026%20standings&format=json'
```

The 2026-11-12 Eastern upgrade pins SearXNG's rolling build
`2026.11.12+de722ab1b` and Valkey `8.1.11` by immutable image digests in Compose.
An isolated rehearsal and production both returned real HTML and JSON search
results. The production LAN/Tailnet bindings, mounts, explicit environment and
private settings stayed unchanged. Both formats remain enabled.

Valkey's effective RDB policy remains `save 30 1`, with `appendonly no`; this is
RDB persistence, not disabled persistence. Before the scoped recreation, SearXNG
was stopped and a consistent live RDB plus hashed key/value/absolute-expiry
fingerprints were captured privately. The new cache loaded that state and
matched the fingerprints before SearXNG restarted. Its original on-disk RDB
state was restored after loading; the existing save policy remains in effect.

For rollback, checkpoint current cache state/expiry again, then stop only
`searxng` and `searxng-redis` and use the matching prior cached image identities
from the private pre-promotion record. Preserve settings, mounts and routes;
change only these two image fields, since other workhorse migrations are
independent. Cache snapshots, original image identities and verified receipts
remain outside Git under the protected maintenance record.

## Alloy Docker-socket recovery

On 2026-10-20, Alloy was still running after the Docker daemon's 2026-10-17
restart, but its individual socket bind mount referenced the old, disconnected
socket inode. The host socket answered `/_ping`; the socket visible inside Alloy
returned `ECONNREFUSED`. This was a collector connection failure, independent of
Loki readiness or the Portal's ability to answer HTTP requests.

The scoped repair recreates **only Alloy**, using the same qualified v1.17.0 image
now pinned by digest. Its existing config, position data, ports and socket access
remain in place. Explicit `json-file` limits of `10m` and `3` files now bound
Alloy's own future container logs. Other containers' logging options are unchanged;
Docker does not apply new logging options retroactively to existing containers.

Check both socket identities before attributing this symptom to API versions or
permissions:

```bash
pid=$(sudo docker inspect --format '{{.State.Pid}}' workhorse-alloy)
sudo stat -Lc '%d:%i' /run/docker.sock "/proc/$pid/root/run/docker.sock"
sudo curl --max-time 3 --unix-socket /run/docker.sock http://localhost/_ping
sudo curl --max-time 3 --unix-socket "/proc/$pid/root/run/docker.sock" http://localhost/_ping
```

Use `/run` in the process-root path: `/var/run` is an absolute symlink inside the
container and can resolve back to the host's `/run` during a host-side check,
giving a misleading successful comparison. A running process, valid Alloy config
or HTTP readiness alone does not prove Docker discovery or log delivery.

If the bind mount is stale, first preserve the exact image, Alloy config and
`/opt/edsys-workhorse/alloy` position data privately. Preserve the old collector
log before recreating its container. Then, from the repository root:

```bash
sudo docker compose -f docker/9950x-workhorse/compose.yaml config --quiet
sudo docker compose -f docker/9950x-workhorse/compose.yaml up -d \
  --no-deps --no-build --pull never --force-recreate alloy
```

Do not restart the Docker daemon, Loki, the Portal or media applications for this
repair. Do not broaden the bind mount to all of `/run`. A future daemon socket
replacement can require the same targeted rebind; no automatic host hook was
added in this repair.

Acceptance requires matching host/container socket identities and successful
socket reads, a bounded quiet error window, and fresh real log entries from at
least two unchanged production containers. Match those entries to their Docker
source, then retrieve them through the authenticated Portal
`/api/workhorse/logs` endpoint as well as Loki. Preserve the private before/after
container IDs and start times to demonstrate the isolation of the repair.

Existing Docker history for newly discovered container IDs can replay after
reconnection. Loki's existing age policy rejects entries older than seven days;
an initial HTTP 400 replay rejection is not proof of a new connection failure.
Report rejected history separately from fresh accepted entries, and verify that
rejection counters stop growing before declaring the collector stable. Original
source logs and Loki retention settings must remain unchanged.

The 2026-10-20 acceptance check observed 144 seconds with no collector errors,
no growth in HTTP 400/429, parsing-error or dropped-entry counters, and 283 newly
accepted entries. Eight fresh Portal entries and two fresh LiteLLM entries
matched their unchanged production containers' Docker logs exactly and were
also returned by the authenticated Portal API. These include read-only health
and access probes; no fixture logs were injected. All 82 other container IDs,
start times and running states matched the before checkpoint. The initial
historical replay produced age rejections; this repair does not establish full
recovery of the ingestion gap or of all historical records.

The private recovery checkpoint for this repair is
`/var/backups/edsys-alloy/2026-10-20-socket-rebind/`. It contains the original
Compose/config, stable Alloy state and full original collector log, the exact
image archive, hashes and an isolated state-restore check. For rollback, stop
only Alloy, restore only its prior config/stanza and position data, and recreate
only Alloy using the archived image if necessary. Do not overwrite unrelated
Compose changes or delete Loki data. Restore the older positions only when
needed: doing so can replay already ingested entries. Keep archived logs as
evidence rather than copying them into a new container's active log file.


## LiteLLM broker operations

LiteLLM keeps `store_model_in_db=true`; models added through the UI/database, including OpenAI-backed chat and Codex entries, must be preserved during config changes. The sanitized config adds local model routes plus `router_settings.model_group_alias` aliases for EdSys client use. Do not put provider API keys in this config.

Ask Foothills uses the explicit `foothills-query-local` route backed by the
32K-context `ask-foothills-qwen35:latest` Ollama alias, with
`foothills-query-local-fallback` backed by `gpt-oss:20b`. Its service key is
restricted to those route names. The primary route's 300-second timeout is
model-specific and intentionally longer than the general broker default.

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
- Native database staging and the S3 export extend the existing encrypted backup
  job without another timer. See `services/workhorse-ai-maintenance/` for exact
  database-before-object ordering, hashes and isolated restore commands.
- Seaweed's private identity is `/opt/edsys-workhorse/langfuse/seaweedfs-s3.json`,
  owned by UID 1000 with mode 0600. Data is under
  `/mnt/ai-store/langfuse/seaweedfs`. Legacy MinIO-named private environment fields
  remain compatibility names for the unchanged credential pair.
- The retired MinIO profile retains its original volume/image as a frozen
  recovery source. After cutover, new objects exist only in Seaweed; rollback
  requires reconciling that delta or the complete matching database/object
  checkpoint, rather than simply switching back to the old store.
- Do not commit generated `.env`, live settings, Docker volumes, logs, databases, or UI-generated exports.

## Reachability

Operator-facing workhorse UI ports bind to `WORKHORSE_LAN_BIND_IP` and `WORKHORSE_TAILSCALE_BIND_IP`, currently `192.168.50.50` and `100.87.137.47`. Healthchecks also binds its same configured host port on `127.0.0.1` so host-local heartbeat/status tooling can use a stable loopback endpoint without depending on LAN or Tailnet routing. This fixes browser links from other machines while avoiding public internet exposure. Keep database/collector-only ports loopback or Docker-internal unless separately reviewed.

### Model Lab routes

The AI Portal's `#/lab` workspace uses eight explicit `edsys-lab-*` local Ollama
routes. These default to 32K context, a 600-second timeout, and no fallback mappings so
manual model selection stays fixed. They cover the installed GPT-OSS, Qwen 3.6,
Qwen 3.6 coder profile, Qwen 3.5, Foothills model profile, Qwen 2.5 Coder, and both
Llama models. The Foothills model profile in the Lab still uses EdSys knowledge;
Ask Foothills retains its separate project retrieval path and key.

Keep route/context definitions aligned with the Portal's `config/lab-models.yaml`
and grant only these additional names to its existing service-scoped key. Before
changing grants, retain the prior key metadata privately. Refresh in the Lab
checks both model installation and service-key access; it does not download or
register models. Existing use-case routes and other client keys stay independent.
Revert the config and prior key model list together if rolling this addition back.

The memory-capable Model Lab backend supplies a bounded per-request `num_ctx`,
expanding from 8K to each qualified model ceiling: 128K GPT-OSS/Llama, 256K
Qwen 3.5/3.6 profiles, 32K Qwen 2.5 Coder. Broker defaults remain 32K for other
callers and old-image rollback. All eight exact routes passed a short completion
and actual Ollama context readback on 2026-10-09. This verifies allocation and
routing, not model answer quality or full-window throughput. Shared Lab history
lives in the Portal's private persistent data volume; the existing critical
backup run stages a consistent SQLite snapshot before Restic.
