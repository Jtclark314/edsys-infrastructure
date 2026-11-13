# 9950x AI Foundations

Deployable source for the shared Ollama, Qdrant, Infinity, and Wyoming voice foundations on `9950x`.

## Exposure policy

- Ollama `11434`, Qdrant HTTP `6333`, and Infinity `7997` publish from Docker only on loopback plus the `9950x` LAN address. The exact Tailnet address is provided by the FreeBind systemd socket proxies documented in `../../scripts/network/README.md`.
- Piper `10200`, Whisper `10300`, and OpenWakeWord `10400` bind only to loopback plus the LAN because their intended consumer is the private Home Assistant/voice path.
- No service binds `0.0.0.0` or `[::]` on the host. Container-internal listeners may still use `0.0.0.0` inside the isolated Docker network.
- The external `ai-net` network must already exist.

Runtime data remains under `/mnt/ai-store`; it does not belong in Git. All six
images are pinned to reviewed digests. Shared Ollama `0.40.16`, Qdrant
`1.20.0`, and native Infinity `3.0.0` were qualified and deployed on 2026-11-12.
The upgrade retained all nine Ollama models/aliases and the existing embedding
checkpoint. It did not download replacement model weights.
Wyoming Whisper `3.8.1` and Piper `2.5.3` were also qualified and deployed;
OpenWakeWord was already current at `2.1.0` and retained its existing image.

Ollama and Infinity both reserve the NVIDIA GPU. Ollama automatically chooses
full or partial offload based on model and context size; verify the actual
split with `ollama ps` instead of assuming the GPU is active from container
environment variables alone. Ask Foothills uses model-specific Ollama aliases
with a 32K context so its retrieval prompts do not inherit Ollama's smaller
GPU-derived default context. Do not raise the global context for voice and
other lightweight clients.

Infinity uses the retained `michaelfeil/bge-small-en-v1.5` revision
`ab7b31bd10f9bfbb915a28662ec4726b06c6552a` directly from the existing local
snapshot, mounted read-only. Keep that snapshot when restoring the service.
Its served model name and 384 dimensions are unchanged. Native v3 uses new
CLI flags and supports both `/embeddings` and `/v1/embeddings`; the broker's
existing root API base remains valid. `--max-client-batch-size 512` preserves
AnythingLLM's 500-chunk ingestion requests. CUDA requires float16/bfloat16;
float32 is rejected by the native CUDA attention backend. The qualified
float16 output had cosine similarity at least `0.999923` to the old backend
over 24 golden/negative queries. A real 500-input batch and all 14 positive
and 10 negative retained-index retrieval cases passed before and after
promotion. Existing stored vectors were retained without re-embedding.

Speech retains Whisper's `base` model and English language, Piper's
`en_US-lessac-medium` default voice, and the original model/voice data files.
Whisper uses `HF_HUB_OFFLINE=1` against its retained `/data` cache; restore that
cache before starting it on a recovered host. Original file hashes passed in
the isolated rehearsal and production. Direct Wyoming synthesis/transcription
round trips passed after each service update. OpenWakeWord's protocol inventory
still contains `okay_nabu`, `hey_jarvis`, `hey_mycroft`, `alexa`, and
`hey_rhasspy`, with `hey_jarvis` preloaded. Home Assistant routing is unchanged.

Qdrant's currently empty data store migrated through `1.18.3`, `1.19.2`, and
`1.20.0` in order, following an isolated rehearsal and stopped-state checkpoint.
Do not skip required minor-version migrations or downgrade a migrated store
in place. Restore the matching stopped-state checkpoint with its old image
when a database downgrade is needed. The isolated final-version probe passed
384-dimensional collection creation, upsert, query and deletion.

## Validate and deploy

```bash
docker compose --project-name ai -f docker/9950x-ai/compose.yaml config --quiet
docker compose --project-name ai -f docker/9950x-ai/compose.yaml up -d --pull never --no-build
docker compose --project-name ai -f docker/9950x-ai/compose.yaml ps
```

After recreating Ollama, verify GPU device admission and model offload:

```bash
docker inspect ollama --format '{{json .HostConfig.DeviceRequests}}'
ollama run ask-foothills-qwen35 "Return only: ready"
ollama ps
```

Create or refresh the Ask-specific alias after the underlying weights are
present:

```bash
docker cp docker/9950x-ai/models/ask-foothills-qwen35.Modelfile \
  ollama:/tmp/ask-foothills-qwen35.Modelfile
docker exec ollama ollama create ask-foothills-qwen35 \
  -f /tmp/ask-foothills-qwen35.Modelfile
```

The alias shares the underlying `qwen3.5:35b` model layers and therefore adds
only manifest metadata, not another full copy. The evaluated `gemma4:26b`
candidate was retired because it did not reliably satisfy the strict
structured-answer contract.

Use project name `ai` so this source manages the existing Compose project. Recreate and health-check one service at a time during a binding change; do not restart the whole AI stack blindly.

An image upgrade is a separate maintenance gate: select and record an exact
digest, review its release and compatibility impact, pull that digest
explicitly, recreate one service, and complete GPU/dependency/health and client
acceptance before changing the tracked pin. Never replace this process with a
broad `docker compose pull` or an unreviewed `latest` refresh.

## Restore

Mount `/mnt/ai-store` first, confirm the `ai-net` network, then use the ordered container-recovery controller. A missing AI Store is a hard stop. Model/cache data is classified separately by the backup catalog; do not assume every model is protected.
