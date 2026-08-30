# 9950x AI Foundations

Deployable source for the shared Ollama, Qdrant, Infinity, and Wyoming voice foundations on `9950x`.

## Exposure policy

- Ollama `11434`, Qdrant HTTP `6333`, and Infinity `7997` publish from Docker only on loopback plus the `9950x` LAN address. The exact Tailnet address is provided by the FreeBind systemd socket proxies documented in `../../scripts/network/README.md`.
- Piper `10200`, Whisper `10300`, and OpenWakeWord `10400` bind only to loopback plus the LAN because their intended consumer is the private Home Assistant/voice path.
- No service binds `0.0.0.0` or `[::]` on the host. Container-internal listeners may still use `0.0.0.0` inside the isolated Docker network.
- The external `ai-net` network must already exist.

Runtime data remains under `/mnt/ai-store`; it does not belong in Git. All six
images are pinned to reviewed digests. The Ollama `0.33.2` digest was verified
live on 2026-08-30; the other five image digests were verified on 2026-07-13.

Ollama and Infinity both reserve the NVIDIA GPU. Ollama automatically chooses
full or partial offload based on model and context size; verify the actual
split with `ollama ps` instead of assuming the GPU is active from container
environment variables alone. Ask Foothills uses model-specific Ollama aliases
with a 32K context so its retrieval prompts do not inherit Ollama's smaller
GPU-derived default context. Do not raise the global context for voice and
other lightweight clients.

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
