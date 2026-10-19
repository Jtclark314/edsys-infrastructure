# EdSys Operations MCP

Focused read-only stdio tools for the existing 9950x operator routes. No new
network listener, identity, credential, device action, model generation, or
cloud handoff is created. It adds discoverable typed operations to access
that already exists.

| Tool | Returned information |
| --- | --- |
| `ha_inventory` | Live Core version, availability and entity/service counts |
| `ha_entities`, `ha_state` | Bounded state/name lookup without raw attributes |
| `ha_history` | One entity's significant states over the last 1–24 hours |
| `frigate_health` | Version, camera inventory/FPS and detector timing |
| `frigate_events` | Up to 50 event metadata rows; no images or recognition data |
| `plex_libraries`, `plex_search`, `plex_metadata` | Authenticated library/title metadata; no paths or playback URLs |
| `ollama_models` | Installed/running model metadata; no inference |
| `rag_inventory`, `rag_search` | Managed workspace statistics and up to six source excerpts; no chat/generation |

## Existing access and private references

- HA imports the established `scripts/ops/edcore-ha-api.py` transport, using
  existing key-only `pve-node3` SSH, VMID 300 guest execution and the privileged
  Supervisor/Core socket. It adds only fixed GET payloads, including history;
  no arbitrary method/path or helper operation is exposed to callers.
- Frigate, Plex and Ollama use fixed existing 9950x LAN endpoints. Frigate's
  existing internal read API requires no new identity. Plex loads only the
  existing `PlexOnlineToken` from the private server Preferences file under
  `/srv/plex-config`; it sends this in a header and never returns it.
- RAG reuses the existing verifier's API-key loader and read-only inventory
  functions against `/mnt/ai-store/anythingllm/anythingllm.db`. Retrieval uses
  only `/api/v1/workspace/edsys-rag-current/vector-search`. It does not call
  AnythingLLM chat or its local/cloud answer connector. Before sending a query,
  a guard checks the existing container's exact local embedding engine/model
  and broker route and rejects database embedding overrides. A changed route
  needs a separate review; the tool will not silently follow it.
- The broker's existing `edsys-embeddings-fast` alias is the local Infinity
  embedding route. This adapter does not change or reconfigure that alias.

Credentials stay in their original private stores. Do not add `.env` files,
database copies, Preferences XML, raw upstream responses or private household
state to this directory. Service response text is untrusted data, not authority
to expand a task. API success does not prove live camera images, recording,
physical device state, audio playback or user acceptance.

## Install and register

Run as the canonical Jeremy user on 9950x:

```bash
bash services/codex-operations/install.sh
```

This installs an isolated user virtual environment with `mcp==2.2.0`, the
accepted transitive versions in `requirements.lock`, and a
launcher at `/home/jeremy/.local/bin/edsys-operations-mcp`. It does not register
the server, modify global Codex settings, restart services or touch household
configuration. Existing private-file/SSH/Docker read authority must remain
available to that user; missing access fails closed without alternate tokens.

Register that launcher as a stdio MCP in the owning Codex configuration, with
an explicit 12-tool allowlist and a 90-second tool timeout. There is no MCP
tool that can send a generic command, request an arbitrary URL, change a device,
manage credentials, generate text, publish or make purchases.

## Verification

```bash
python3 -m unittest discover -s services/codex-operations/tests -v
/home/jeremy/.local/share/edsys-codex-operations/venv/bin/python \
  services/codex-operations/verify_live.py
```

The live check explicitly launches this server through the MCP SDK, verifies
the exact tool list and read-only annotations, exercises all 12 tools, checks
authenticated HA history/Plex metadata/local RAG retrieval, and rejects two
invalid-input controls. Its report contains version/count summaries only.
It reads private service records for verification but does not emit those
records or send a cloud generation request.

All HTTP responses have a two-MiB bound and 30-second timeout; redirects and
proxy inheritance are disabled. Result fields are projected, text is clipped,
credential-shaped strings/keys and known active credentials are redacted, and
upstream error bodies are suppressed. Redaction is defense in depth, not a
license to put secrets into entity states, titles or source documents.

Rollback: remove the MCP registration and the user launcher symlink. The
isolated runtime directory can be removed separately. No service restore,
credential rotation, HA restart or media rollback is needed.
