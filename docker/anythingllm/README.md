# AnythingLLM on 9950x

Sanitized deployable source for the existing AnythingLLM Compose project.

The default Ollama model is the qualified `ask-foothills-qwen35:latest` alias.
This reuses the shared Qwen 3.5 35B weights instead of retaining a separate
AnythingLLM-only model. Individual AnythingLLM workspaces may still have their
own saved provider/model selection; verify those settings before assuming the
Compose default controls an existing workspace.

On 2026-11-12, the managed `EdSys-RAG-Current` workspace explicitly uses
LiteLLM with `edsys-coder-local` in strict Query mode and `router_id=null`.
The additional `Testing` workspace retains its inherited provider/model
settings. The upgrade preserved both workspaces and their saved settings.
Three immutable read-only provider mounts enable the optional
`edsys-knowledge-local-first` application alias. Only that alias attempts
`edsys-chat-local` before a bounded `edsys-knowledge-openai` handoff; ordinary
models such as the current managed selection retain upstream behavior and
the configured embedder. See the
[connector policy and recovery guide](../../services/anythingllm-rag-assist/README.md).
Its workspace prompt describes the selected chat model separately from the
embedding model and vector store. HA conversational voice has its own OpenAI
backend and is independent of this setting.

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

The image is pinned to AnythingLLM `1.19.0`, digest
`sha256:daf5a6ae9d363ed578e7d41e2d0668c7eaa604bd90b0ff27435f893b0906833e`,
qualified and deployed on 2026-11-12 with adapter release `v1.1.0`. A stopped
storage copy and SQLite integrity check preceded migration. An isolated
rehearsal and production verification preserved the two workspaces, 21 chats,
77 documents and 863 vectors. All 14 positive and 10 negative retrieval cases
passed; no embedding model or stored vector was replaced.

The older global verifier additionally assumes a sole workspace and active
`edsys-knowledge-local-first` selection. Those profile checks do not describe
the preserved current settings. Use the retrieval gate with explicit
workspace-baseline equality; do not delete `Testing` or change models to make
that historical profile check pass.

Upgrade only in a separate maintenance gate:
back up the retained data, review and pull a candidate by exact digest, then
test it against the retained workspaces before updating
the tracked pin. Do not run an unreviewed `docker compose pull`.
