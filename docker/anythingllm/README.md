# AnythingLLM on 9950x

Sanitized deployable source for the existing AnythingLLM Compose project.

The default Ollama model is the qualified `ask-foothills-qwen35:latest` alias.
This reuses the shared Qwen 3.5 35B weights instead of retaining a separate
AnythingLLM-only model. Individual AnythingLLM workspaces may still have their
own saved provider/model selection; verify those settings before assuming the
Compose default controls an existing workspace.

- Docker publishes host UI port `3002` only on loopback and the `9950x` LAN address. The exact Tailnet address is provided by the reviewed FreeBind socket proxy in `../../scripts/network/README.md`.
- Persistent application state and the read-only reviewed RAG source live under `/mnt/ai-store`.
- Runtime provider and broker settings are read from root-managed files under `/etc/edsys-secrets`; their contents never belong in Git.
- The external `ai-net` network provides private service-name access to Ollama.
- The legacy convenience path `/home/jeremy/stacks/anythingllm/docker-compose.yml` is a symlink to this tracked Compose file so it cannot drift into a second deployment definition.

```bash
docker compose --project-name anythingllm -f docker/anythingllm/compose.yaml config --quiet
docker compose --project-name anythingllm -f docker/anythingllm/compose.yaml up -d --pull never --no-build
docker compose --project-name anythingllm -f docker/anythingllm/compose.yaml ps
```

Use project name `anythingllm` so this source manages the existing Compose project. Confirm `/mnt/ai-store` and both private env files before recovery. Do not create replacement env files in the repository.

The image is pinned to AnythingLLM `1.15.0`, digest
`sha256:00903f6311607b661d40f9e1d0e027d61e28a7b002ea9d1b7cad7763d26099f9`,
verified live on 2026-08-10. Upgrade only in a separate maintenance gate:
back up the retained data, review and pull a candidate by exact digest, then
test it against the sole managed `EdSys-RAG-Current` workspace before updating
the tracked pin. Do not run an unreviewed `docker compose pull`.
