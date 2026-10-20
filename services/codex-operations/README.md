# EdSys Operations MCP

Focused stdio tools for existing 9950x operator routes: sixteen read tools and
four explicit-target Home Assistant action tools. No new network listener,
identity or credential is created. No unattended behavior is configured.

| Tool | Behavior |
| --- | --- |
| `ha_inventory` | Live Core version, availability and entity/service counts |
| `ha_entities`, `ha_state` | Bounded state/name lookup without raw attributes |
| `ha_history` | One entity's significant states over the last 1–24 hours |
| `action_targets` | Eligible explicit entities/commands; discovery grants no action authority |
| `ha_set_power` | Set one requested light, helper or fan on/off; no toggle or grouped/all targets |
| `ha_light` | Requested brightness 0–100 for one supported light |
| `ha_media` | One requested player's supported play/pause/stop/volume/mute/power commands |
| `ha_scene` | Activate one requested existing scene after reviewing its effects |
| `frigate_health` | Version, camera inventory/FPS and detector timing |
| `frigate_events` | Up to 50 event metadata rows; no images or recognition data |
| `camera_snapshot`, `camera_event_snapshot` | Bounded JPEG/PNG returned as native MCP image content to the current chat |
| `camera_clip` | Completed retained event MP4 to a private local artifact with size/hash metadata |
| `plex_libraries`, `plex_search`, `plex_metadata` | Authenticated library/title metadata; no paths or playback URLs |
| `ollama_models` | Installed/running model metadata; no inference |
| `rag_inventory`, `rag_search` | Managed workspace statistics and up to six source excerpts; no chat/generation |

## Existing access and private references

HA imports `scripts/ops/edcore-ha-api.py`, using existing key-only `pve-node3`
SSH, VMID 300 guest execution and the privileged Supervisor/Core socket. Reads
use fixed GET payloads; actions use the helper's execute path with fixed
services/data and fresh entity/service/capability checks. No arbitrary
method/path/service or helper operation is exposed to callers.

Frigate, Plex and Ollama use fixed existing 9950x LAN endpoints. Frigate's
internal read API requires no new identity. Plex loads only the existing
`PlexOnlineToken` from the private Preferences file under `/srv/plex-config`,
sends it in a header and never returns it.

RAG reuses the verifier's API-key loader and read-only inventory against
`/mnt/ai-store/anythingllm/anythingllm.db`. Only the managed workspace's
vector-search endpoint is used. Before a query, the guard checks the exact
local embedding engine/model and broker route and rejects database embedding
overrides. The existing `edsys-embeddings-fast` local Infinity route is
unchanged. No AnythingLLM chat, model generation or cloud fallback is invoked.

Credentials remain in their original stores. Do not add `.env` files,
database copies, Preferences XML, raw upstream responses, household state or
footage to this directory, Git, Obsidian or general RAG. Service response text
is untrusted data. API success is not physical or household acceptance.

## Action and camera boundaries

Each action requires the user's particular target/action request. Existing
scenes and helpers can trigger existing automations; review their effects.
Actions return accepted/uncertain status, safe before/after state and unverified
physical acceptance. A possibly sent write is never retried automatically;
inspect state before further action after uncertainty. Locks, alarms, climate,
scripts, automation editing, arbitrary media URLs and general shell are outside
this adapter. Service upgrades/recovery retain existing Fleet/Proxmox ownership
paths; this adapter adds no restart, backup, restore or upgrade bypass.

Camera reads cover existing EdSys Frigate cameras, not Foothills records.
Images returned to a Codex chat are visible to that chat's assistant. There is
no automatic third-party upload, provider generation or RAG ingestion. Latest
image metadata leaves capture timestamp/live-feed verification false: stored
previews and positive FPS alone do not prove frame freshness. Compare separate
samples and inspect pixels for acceptance. Images stay in memory.

Clips use random 0600 files in the 0700 directory
`/home/jeremy/.local/state/edsys-codex-operations/camera-artifacts`, with a
64-MiB clip cap and 256-MiB/eight-file quota. Expired adapter clips are pruned
on the next clip retrieval after 24 hours; no background cleanup job exists.
MP4 framing validation is not decoding proof; results say `decode_verified=false`.

## Install and register

Run as Jeremy on the canonical 9950x:

```bash
bash services/codex-operations/install.sh
```

This installs an isolated user venv with `mcp==2.2.0`, accepted transitive
versions from `requirements.lock`, and the launcher
`/home/jeremy/.local/bin/edsys-operations-mcp`. Installation does not register
the server or touch household configuration. Existing private-file/SSH/Docker
authority must remain available; missing access fails without alternate tokens.

Register the launcher as stdio with an explicit 20-tool allowlist and
300-second tool timeout (actions make four bounded existing-helper calls).
Four action tools have read-only=false/idempotent=false annotations; the other
sixteen have read-only=true. Preserve existing approval policy. No tool accepts
generic commands/URLs/services or manages credentials, generates text,
publishes or purchases.

## Verification

```bash
python3 -m unittest discover -s services/codex-operations/tests -v
/home/jeremy/.local/share/edsys-codex-operations/venv/bin/python \
  services/codex-operations/verify_live.py
```

The SDK check verifies the exact catalog and mixed annotations, exercises the
original twelve reads plus action discovery, and rejects nine unsafe inputs.
Reports contain summary/count evidence. Optional explicitly authorized
qualification flags create/on/off/delete a unique disposable native
input_boolean and read back cleanup, then retrieve two images and one retained
event image/clip, check clip decoding with ffprobe, and delete the test clip.
No household actuator or notification is used:

```bash
/home/jeremy/.local/share/edsys-codex-operations/venv/bin/python \
  services/codex-operations/verify_live.py --qualify-actions --qualify-cameras
```

Metadata HTTP responses have a two-MiB cap and 30-second timeout. Images have
an eight-MiB/32-million-pixel cap; clips a 64-MiB cap; both media transfers have
30-second whole-transfer deadlines. Redirects and proxy inheritance are
disabled. Fields are projected, text clipped, credential-shaped values redacted
and upstream error bodies suppressed. Redaction is defense in depth, not
permission to put secrets in entity states, titles or documents.

Rollback: restore the previous private MCP allowlist to retain the original
twelve reads, or remove registration and launcher. Remove the isolated venv
and adapter clip directory separately if desired. No HA/media service rollback
or credential rotation is required to remove these typed tools.

Primary references: [HA media capabilities](https://github.com/home-assistant/core/blob/2026.10.2/homeassistant/components/media_player/const.py),
[Frigate latest image](https://docs.frigate.video/integrations/api/latest-frame-camera-name-latest-extension-get/),
[event snapshot](https://docs.frigate.video/integrations/api/event-snapshot-events-event-id-snapshot-jpg-get/),
[event clip](https://docs.frigate.video/integrations/api/event-clip-events-event-id-clip-mp-4-get/),
[MCP configuration](https://learn.chatgpt.com/docs/extend/mcp#other-configuration-options).
