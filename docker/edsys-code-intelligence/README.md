# EdSys Code Intelligence

Private, loopback-only code intelligence for Codex on `9950x`.

## Components

| Component | Purpose | Compute policy |
| --- | --- | --- |
| Zoekt | Trigram and ctags-backed search of committed `HEAD` content | Up to 8 logical CPUs for queries |
| Infinity | Cross-encoder reranking with a pinned AVX-512 VNNI ONNX model | CPU only; up to 12 logical CPUs and 4 GiB |
| MCP application | Five bounded, read-only Codex tools | Up to 4 logical CPUs and 2 GiB |
| Indexer timers | Incremental refresh and weekly clean rebuild | Low-priority; container capped at 24 logical CPUs and 16 GiB |
| Ollama | Existing local `qwen2.5-coder:32b` review/triage backend | Direct internal `ai-net`; request uses 24 threads |

The GPU is deliberately not used. It remains available to the desktop, Frigate,
and the existing GPU embedding service. The 9950X CPU and available system RAM
handle indexing, reranking, and advisory review.

## Network and privacy boundary

- The MCP endpoint is published only at `127.0.0.1:6071/mcp`.
- Zoekt and Infinity have no published ports and share an internal-only network.
- The MCP container has no repository mounts.
- The indexer mounts the explicit repository list read-only, runs without
  network access, and asks `zoekt-git-index` to index Git `HEAD`. Dirty,
  untracked, and ignored files are not indexed.
- Searches, diffs, test output, snippets, and model responses are not persisted
  or included in application logs.
- There is no LAN, Tailnet, tunnel, reverse-proxy, or public UI route.

## Pins

- Zoekt image:
  `ghcr.io/sourcegraph/zoekt@sha256:0bf4af966897c2fd493e2b0826440e17d5640e8c4d8579c7e5cac28f084da75a`
- Infinity image:
  `docker.io/michaelf34/infinity@sha256:11e8b3921b9f1a58965afaad4a844c435c9807cbc82c51e47cb147b7d977fc88`
- Reranker:
  `cross-encoder/ms-marco-MiniLM-L6-v2`
  revision `c5ee24cb16019beea0893ab7796b1df96625c6b8`
- ONNX artifact:
  `onnx/model_qint8_avx512_vnni.onnx`
  SHA-256 `3573b6b9593cb2f75987a31815d409ca3dd8808629118fd20451bb1a5d90cec7`

## State and backup policy

| Path | Nature | Backup |
| --- | --- | --- |
| `/mnt/ai-store/codex-intelligence/zoekt` | Regenerable index shards | No |
| `/mnt/ai-store/codex-intelligence/state` | Generated status metadata | No |
| `/mnt/ai-store/codex-intelligence/models` | Public, pinned model artifacts | No |

Restore by re-staging the model and rebuilding the index. Do not copy old Zoekt
shards across incompatible image revisions.

## Install or refresh

```bash
cd /srv/edsys/edsys-infrastructure/docker/edsys-code-intelligence
sudo ./install-9950x.sh
```

The installer:

1. verifies the loopback port, external `ai-net`, repository allowlist, and app
   checkout;
2. stages and verifies the pinned public reranker model if needed;
3. installs the guarded indexer and systemd units;
4. builds an initial committed-content index;
5. builds and starts the hardened containers;
6. enables five-minute incremental and weekly full-rebuild timers;
7. verifies health, tool count, loopback binding, and container security.

## Scheduled refresh

- `edsys-code-intelligence-index.timer`: five-minute incremental refresh.
- `edsys-code-intelligence-full-index.timer`: Sunday clean rebuild to remove
  stale shards and validate a fresh index from scratch.

Both units share a nonblocking lock. A failed refresh preserves the last usable
index and records failure metadata. The full rebuild switches indexes only after
the candidate index passes a local search validation; activation is rolled back
if Zoekt does not become healthy.

## Verification

```bash
docker compose config -q
docker compose ps
curl -fsS http://127.0.0.1:6071/ready | python3 -m json.tool
ss -ltnp | grep ':6071'
systemctl list-timers 'edsys-code-intelligence*'
sudo /usr/local/sbin/edsys-code-intelligence-index --mode incremental
```

MCP protocol verification:

```bash
python3 scripts/mcp-smoke.py http://127.0.0.1:6071/mcp
```

Expected tool names:

```text
search_code
search_symbol
code_index_status
review_diff_local
triage_test_failure_local
```

Golden correctness, latency, and reranker evaluation:

```bash
cd /home/jeremy/code/edsys-code-intelligence
uv run python scripts/evaluate.py \
  --output /mnt/ai-store/codex-intelligence/state/latest-evaluation.json
```

The evaluator requires every exact/symbol query in the checked-in suite to land
in the top five, warmed native-search p95 below 100 ms, full index duration below
10 minutes, and healthy core search. It reports reranker MRR separately. The
accepted 2026-07-29 result cleared the 5%/no-top-ten-regression gate, so the MCP
application's `ranking=auto` default uses CPU reranking for
`query_mode=any_terms` and retains native Zoekt ordering for literal,
conjunctive, and regex searches. Explicit `zoekt` and `rerank` overrides remain
available.

## Rollback

1. Disable the two timers.
2. Remove or disable the `edsys_code_intelligence` block from Codex
   configuration.
3. Run `docker compose down` in this folder.
4. Preserve the application and infrastructure Git history; the AI Store index
   may be regenerated or removed after confirming no rollback is needed.
