# MailBrain Docker stack

Dedicated Outlook email RAG portal for `mailbrain.edsyslab.com`.

## Runtime state

- MailBrain SQLite/FTS data: `/mnt/ai-store/rag/mailbrain/`
- MailBrain Operator curated RAG data: `/mnt/ai-store/rag/mailbrain/knowledge/`
- Canonical operator Obsidian note: `/mnt/ai-store/rag/docs/EdSysVault/70 AI & RAG/MailBrain Operator - RAG Summary.md`
- OAuth/token/sync state: `/opt/edsys-workhorse/mailbrain/`
- App source: `/home/jeremy/code/edsys-mailbrain`

Do not commit the live `.env`, token files, runtime DB, curated knowledge DB, extracted mailbox data, vectors, logs, or attachment bytes.

## Deploy

```bash
cd /srv/edsys/edsys-infrastructure/docker/mailbrain
cp .env.example /home/jeremy/code/edsys-mailbrain/.env
# edit the private .env outside Git
docker compose up -d --build
```

The compose stack binds MailBrain on LAN/Tailnet port `3035` and on loopback for local checks:

- current Cloudflare Tunnel target: `http://192.168.50.50:3035`
- loopback preview: `http://127.0.0.1:3035`
- Tailnet preview: `http://100.87.137.47:3035`

The portal root and API endpoints are protected by MailBrain Basic auth in this preview stack. The private local admin password is stored on `9950x` at `/opt/edsys-workhorse/mailbrain/local-admin-password` and must not be committed or pasted into docs. Public `mailbrain.edsyslab.com` exposure should still use Cloudflare/TLS controls and an auth review before production sync/backfill.

## Operator curated RAG

The compose stack mounts:

- `/mnt/ai-store/rag/mailbrain` to `/data/mailbrain` for email and curated runtime stores;
- `/mnt/ai-store/rag/docs/EdSysVault` to `/mnt/ai-store/rag/docs/EdSysVault` so the canonical MailBrain Operator note can be indexed;
- `/home/jeremy/code` read-only to `/host-code` so allowlisted MailBrain Git docs can be indexed.

After deploying code changes, run a supervised knowledge sync through `POST /api/mailbrain-knowledge/sync` and verify `GET /api/operator/context-preview?query=...` returns base prompt and curated snippets. Curated knowledge guides MailBrain behavior only; Outlook email RAG remains authoritative for current email status, outcomes, and action-owner claims.

## Microsoft Graph cutover sync

Live cutover status as of 2026-07-07: Graph backfill and Qdrant rebuild completed on the running portal, with 10,028 indexed messages, 172,193 chunks, 7,734 attachment records, and 2,682 indexed attachment-text records.

Create/refresh the delegated Graph token outside Git, then dry-run one page per folder before resetting the temporary connector corpus:

```bash
cd /home/jeremy/code/edsys-mailbrain
.venv/bin/python tools/mailbrain_graph_auth.py --expected-email jclark@thompsonturner.com

GRAPH_ACCESS_TOKEN_FILE=/opt/edsys-workhorse/mailbrain/graph-access-token \
MAILBRAIN_SQLITE_PATH=/mnt/ai-store/rag/mailbrain/mailbrain.sqlite \
.venv/bin/python tools/mailbrain_graph_sync.py --all-folders --backfill --limit-pages 1 --dry-run --progress
```

After the dry-run succeeds, archive and reset email-only state, then run a supervised write sync with embeddings:

```bash
MAILBRAIN_SQLITE_PATH=/mnt/ai-store/rag/mailbrain/mailbrain.sqlite \
.venv/bin/python tools/mailbrain_graph_cutover.py --archive --reset-email-index --reset-qdrant --yes

GRAPH_ACCESS_TOKEN_FILE=/opt/edsys-workhorse/mailbrain/graph-access-token \
MAILBRAIN_SQLITE_PATH=/mnt/ai-store/rag/mailbrain/mailbrain.sqlite \
.venv/bin/python tools/mailbrain_graph_sync.py --all-folders --backfill --limit-pages 1 --embed --progress
```

Remove `--limit-pages` for the full historical backfill after the supervised run validates searches, source cards, attachment extraction, and vector scores. Run a normal non-`--backfill` Graph sync afterward to maintain per-folder delta links for ongoing changes. If embeddings must be rebuilt, use `tools/mailbrain_reembed_all.py --reset-collection --batch-size 64`. Very large supported attachments above `MAILBRAIN_ATTACHMENT_TEXT_MAX_BYTES` remain metadata-only so backfill cannot stall on huge PDFs/workbooks.
