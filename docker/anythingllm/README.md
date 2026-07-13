# AnythingLLM on 9950x

Sanitized deployable source for the existing AnythingLLM Compose project.

- Docker publishes host UI port `3002` only on loopback and the `9950x` LAN address. The exact Tailnet address is provided by the reviewed FreeBind socket proxy in `../../scripts/network/README.md`.
- Persistent application state and the read-only reviewed RAG source live under `/mnt/ai-store`.
- Runtime provider and broker settings are read from root-managed files under `/etc/edsys-secrets`; their contents never belong in Git.
- The external `ai-net` network provides private service-name access to Ollama.

```bash
docker compose --project-name anythingllm -f docker/anythingllm/compose.yaml config --quiet
docker compose --project-name anythingllm -f docker/anythingllm/compose.yaml up -d --pull never --no-build
docker compose --project-name anythingllm -f docker/anythingllm/compose.yaml ps
```

Use project name `anythingllm` so this source manages the existing Compose project. Confirm `/mnt/ai-store` and both private env files before recovery. Do not create replacement env files in the repository.

The image is pinned to the digest verified live on 2026-07-13. Upgrade only in
a separate maintenance gate: review a candidate, pull its exact digest, test it
against the retained data and both managed workspaces, and update the tracked
pin only after acceptance. Do not run an unreviewed `docker compose pull`.
