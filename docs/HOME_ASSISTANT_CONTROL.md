# Phil Home Assistant control

Verified 2026-10-06 (America/New_York), on the normal 9950x account.

## Route and authority

Use `edcore-control ha api` from Phil's delegated local Codex task. The installed
`edcore-control` entry point resolves to this repository's
`scripts/ops/edcore-control.py`, which invokes `edcore-ha-api.py` and sends a
bounded request through existing key-only SSH to `pve-node3`, QEMU guest exec
on VMID 300, and the existing `hassio_supervisor` container. The worker uses
the official Supervisor/Core Unix socket, discovered from
`supervisor.const.SOCKET_CORE` (currently `/run/os/core.sock`).

This is an **existing privileged operator route**, not a dedicated least-privilege
LAN integration. Its authority comes from the existing root SSH/guest route and
Supervisor's protected socket. No account, credential, permission, firewall,
listener, MCP configuration, or HA service startup setting is added or changed.
No ordinary agent is installed inside HAOS. HTTP clients on the LAN continue
to require their own proper HA identity.

The former qualification attempted `http://supervisor/core/api/` using the
Core container's `SUPERVISOR_TOKEN`. That token authorizes Core calls to
Supervisor; the Core API proxy accepts permitted app identities instead.
HTTP 401 on that combination is expected and is not a broken Core service.
Do not extract browser credentials, repurpose another app's credential, mint a
token, or weaken authentication to work around it.

## Reads

```bash
edcore-control ha api inspect
edcore-control ha api entities --domain light --limit 20
edcore-control ha api entities --filter driveway --limit 10
edcore-control ha api state sun.sun
edcore-control ha api services --domain input_boolean
edcore-control ha api template '{{17*19}}|{{states|list|count}}'
edcore-control ha api websocket '{"type":"config/entity_registry/list"}' --limit 20
edcore-control ha api websocket '{"type":"config_entries/get"}' --limit 20
edcore-control ha api websocket '{"type":"lovelace/dashboards/list"}' --limit 20
```

`camera camera.ENTITY` reads one bounded image and returns only its byte count,
type, and SHA-256; pixels are discarded. Entity reads may contain private
household information. Keep outputs local and task-scoped. Credential keys and
camera URL query credentials are redacted, but that is not a general-purpose
public-export sanitizer.

## Explicitly authorized actions

The caller must retain Jeremy's actual action/target authorization. `--execute`
records deliberate execution; it is not an approval grant. For example, after
an explicit request naming a particular helper:

```bash
edcore-control ha api call-service input_boolean turn_on \
  --data '{"entity_id":"input_boolean.owner_selected_helper"}' --execute
edcore-control ha api state input_boolean.owner_selected_helper
```

Replace the example identifier with an actual selected entity. Broad `all`
targets are rejected. Device-control domains require an explicit entity,
device, area, or floor target. Non-allowlisted WebSocket commands require
`--execute`; this supports deliberately scoped helper, dashboard, and registry
changes. Authentication and Supervisor-management WebSocket commands are
excluded from this Core helper. Existing separate restart, integration,
backup, and household-device approval requirements still apply.

Reads are limited to two MiB per response, 1..50 listed rows, and bounded
timeouts. Payloads travel over SSH stdin; credentials are never passed to the
client. Truncated, rejected, and ambiguous responses fail visibly. An action
timeout is not safe evidence of failure: inspect actual state before any retry.
There is no automatic action retry or alternate credential fallback.

## Acceptance and limits

Core 2026.9.4 returned 742 live states, 75 service domains and 300 action names.
Entity/service/search/template, entity/integration registry and dashboard reads
passed. A camera returned a valid bounded JPEG, discarded immediately.
A unique disposable input_boolean was created through the native UI WebSocket
API, turned on and off through actual `input_boolean.turn_on`/`turn_off`
service calls with state readback, renamed, and deleted. Its state then returned
404 and the helper collection was empty.

After HA's deferred storage save settled, all 982 original registered entities,
integration/disabled policies, 18 sampled configuration/authentication files,
and all ten existing containers matched preflight. A newly initialized empty
input_boolean collection and a deleted-entity tombstone may remain as normal
HA storage bookkeeping; no live fixture remains. No restart, authentication
change, household actuator, phone notification, music playback, or disabled
vehicle alert was triggered. Eight camera states were available; 85 other
states were unavailable and were not repaired by this access change.

Fresh iPhone voice acceptance, each physical-device action, history retrieval,
and existing dashboard writes remain untested. API/control-route success does
not prove every device is online. Prior qualification files remain dated
snapshots; this acceptance supersedes their Home Assistant access blocker.

## Verification and rollback

```bash
python3 -m py_compile scripts/ops/edcore-control.py \
  scripts/ops/edcore-ha-api.py scripts/ops/edcore-ha-remote.py
/home/jeremy/code/edsys-code-intelligence/.venv/bin/python -m pytest -q \
  scripts/ops/tests/test_edcore_control.py scripts/ops/tests/test_edcore_ha_api.py
edcore-control ha api inspect
```

The 24 focused regressions cover write rejection before transport, explicit
targets, path injection, credential redaction, multi-chunk response reads,
bounded output, and ambiguous guest results. Dependencies are already installed;
the 9950x helper uses only Python's standard library.

Rollback is a source-level revert of the helper addition and dispatcher change.
The exact prior dispatcher is retained privately with the repair evidence.
No HA restart, configuration restore, token revocation, or permission change is
needed. Keep runtime evidence and rollback copies outside Git/RAG.

Official references:
- [Supervisor Core API client](https://github.com/home-assistant/supervisor/blob/main/supervisor/homeassistant/api.py)
- [App-to-Core API authentication](https://developers.home-assistant.io/docs/apps/communication/)
- [HA REST API and actual service actions](https://developers.home-assistant.io/docs/api/rest/)
