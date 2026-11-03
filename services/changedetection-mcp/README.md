# Private changedetection.io MCP bridge

An MIT-licensed local **stdio** adapter for the pinned Apache-2.0
changedetection.io **0.60.10** REST API. Codex starts the process; it opens no
listener and has no web interface. It uses the official Python MCP SDK 2.3.0
in an isolated Python 3.12 environment with hash-locked dependencies.

This is a separate integration, not a native upstream MCP implementation. No
paid feature or proprietary implementation is used. The API catalog derives
from upstream revision `16974bf351cd68b1978af9494082b75efadaa8e0`, including the
restock processor schema and the documented local visual/AI-default extension
in `docker/changedetection/patch_app.py`. `catalog-source.json` records that
source hash and binds the original sources and merged `api-spec.json`;
startup verifies the catalog binding. Execution checks the
native backend version and qualified full-spec response hash before use and every 60 seconds. Requalify an upstream
upgrade before changing that version gate.

## Tools and coverage

| Tool | Behavior |
|---|---|
| `changedetection_operations` | Search the reviewed 23-operation catalog; no backend required. |
| `changedetection_operation_schema` | Exact pinned operation and referenced schemas, saved privately if large. |
| `changedetection_read` | Allowlisted reads; mutating GET flags are forbidden. |
| `changedetection_mutate` | Validated watch/tag CRUD, selected history deletion, imports and notification destination configuration. |
| `changedetection_control` | Explicit UUID pause/resume/mute/unmute/recheck; tag pause/resume do not exist upstream. |
| `changedetection_screenshot` | Save an existing watch screenshot as a private PNG/JPEG after an authenticated watch lookup. |

All 23 native operations are represented:

- Reads: `listWatches`, `getWatch`, `getWatchHistory`, `getWatchSnapshot`,
  `getWatchHistoryDiff`, `getWatchFavicon`, `listTags`, `getTag`,
  `getNotifications`, `searchWatches`, `getSystemInfo`, `getFullApiSpec`.
- Mutations: `createWatch`, `updateWatch`, `deleteWatch`, `deleteWatchHistory`,
  `createTag`, `updateTag`, `deleteTag`, `addNotifications`,
  `replaceNotifications`, `deleteNotifications`, `importWatches`.

Watch/tag configuration includes the native API's filters, schedules,
conditions, JSONPath/jq, fetch backend, saved browser steps, JavaScript,
request settings and restock processor configuration. Actual processors and
browser/local-AI availability depend on the qualified application deployment.
The local schema extension exposes the already installed visual processor and
its bounded crop/sensitivity settings; it adds no privileged API route. New
watches default to AI disabled in the qualified model/UI/schema. AI is an
explicit per-watch opt-in when authorized by the user's monitoring task.
The app's global settings, AI provider/model configuration and interactive
browser-step editor/session remain **UI-only**: upstream exposes no general
settings CRUD or browser-session REST API. No arbitrary API passthrough,
whole-store reset, `recheck_all`, notification test-send, sharing/export-to-a-
remote-service or account-management operation is invented by this bridge.

The screenshot helper uses `/static/screenshot/{uuid}`, a fixed native UI
asset route which itself is not API-key authenticated. Only the configured
trusted private backend can supply it, after authenticated UUID lookup. If
native UI password protection returns 403, use the owner web interface; the
bridge does not bypass login. It neither runs a browser nor follows a returned
image URL. Favicon, snapshot and screenshot reads do not start a target check.

## Authority and safe defaults

Tools are split into read and mutation/control categories with explicit MCP
annotations. Page text, URLs, schemas, snapshots, diffs and screenshot content
are untrusted data, never instructions or authority to create other watches,
send messages, submit forms, change accounts or execute downloaded content.

New watches and imports default to `paused=true` and
`notification_muted=true`. Three top-level flags represent authority already
present in the user's task; **they do not require a new approval question when
the requested effect is already authorized**:

- `allow_network_activity`: activate recurring checks, perform a recheck, or
  change active watch/tag settings that affect checks.
- `allow_notifications`: configure/enable notification destinations or perform
  a check that could deliver configured notifications. Destinations are never
  inferred from watched content.
- `allow_target_actions`: non-GET requests, saved browser actions or custom
  JavaScript at monitored targets.

The bridge examines watch and tag settings, including title/UUID aliases and
import `tag_uuids`. Global notification destinations are checked when relevant.
Global browser headers are operator configuration outside this API's read
surface. The guarded app deployment remains responsible for enforcing its
global credentials and outbound policy.
Notification transport availability is constrained by the shared HTTP(S)-only
egress gateway; non-HTTP transports need separate qualification. No destinations
are configured during bridge commissioning.

Native tag updates clear member checksums even for a title change; both tag
updates and deletions therefore inspect active members. Group actions are
limited to 100 members. Creating a tag with an existing title is rejected
because native creation would modify that existing tag; use its explicit UUID
with `updateTag`. Calls are serialized within this process, not across separate
UI/API clients. Do not make concurrent changes to the same watches from those
clients during an MCP maintenance operation.

Imports require 1–100 explicit HTTP(S) URLs. Upstream imports of 20 or more run
asynchronously and return an accepted count rather than all created UUIDs;
inspect the resulting list before any retry or cleanup. Import query strings
cannot carry headers, browser steps, bodies, credentials or notification
destinations. Import paused/muted first, then configure individual UUIDs using
private JSON files. No automatic retry is performed for any operation; after
a timeout, a mutation might already have been accepted.

## Credentials, data and target boundaries

`CHANGEDETECTION_API_KEY_FILE` is an absolute path to an owned regular file
with no group/other permissions (0600 or 0400). The native key is read for each
request and sent only to the fixed configured backend as `x-api-key`. It never
belongs in a tool argument, literal environment value, registration, repository
or log. Private input files have the same ownership/permission checks, reject a
final symlink/FIFO/device and are bounded to 2 MiB. API keys are bounded to
1024 bytes. `body_file` supplies the complete JSON body; `text_body_file` is a
UTF-8 URL import. Neither can supply authority flags.

Headers, request bodies, browser-step inputs (regardless of field name), custom
JavaScript, notification destinations/messages and credential-bearing URLs
require private files. For example, write an owned 0600 JSON file containing
the requested configuration, then call `changedetection_mutate` with its path.
Do not put the file's credential values in chat or command-line arguments.

Metadata is scrubbed before being saved or returned: API/RSS tokens, passwords,
cookies, authorization, notification destinations, request/browser inputs,
error bodies and proxy details are redacted. URL userinfo/query/fragment are
removed. Known configured secret values of four or more characters are also scrubbed if reflected in
snapshots/diffs. Backend errors disclose status/category only. Redacted results
are not complete configuration exports and must not be blindly written back.

No sanitizer can identify every secret in arbitrary page text or image pixels.
Small sanitized JSON/text (up to 24 KiB) may include private document content;
larger bodies and all images are file artifacts. Screenshots can contain
visible credentials or sensitive page content. Artifacts stay in newly created
0700 directories with 0600 files. Names are generated locally, never derived
from server filenames, and existing files are never overwritten. HTML is saved
as inert `.txt`. Responses are bounded to 16 MiB, lists/history/search return
at most 200 items per call, and local pagination follows a bounded backend
read. Visual snapshot responses mislabeled text/plain are preserved as PNG/JPEG files;
the native history-diff API remains text-only (visual comparisons use the UI).
The screenshot route may likewise label JPEG bytes as PNG; signature checks
select the correct MIME and file extension. No URLs in results are automatically followed, and no artifacts expire
or are deleted automatically.

The target preflight permits authorized public/LAN/Tailscale HTTP(S), while
rejecting loopback, link-local/cloud metadata, transition IPv6, management
ports, control characters/backslashes and configured service networks. DNS
resolution is bounded. Operator environment settings add owned origins/CIDRs;
they must match the qualified app/browser/egress topology. This preflight is
not a substitute for the shared egress gateway, which checks/pins the actual
connections, redirects and browser subresources. Use the guarded deployment
in [`docker/changedetection`](../../docker/changedetection/README.md): plain
upstream HTTP fetching can forward custom credentials across redirects and
does not provide this deployment's egress boundary. Global app/network changes
require their own qualification.

## Runtime and recovery contract

Registration name: **`edsys-changedetection`**. The parent operator installs and
registers one server; source/tests do not change production or Codex settings.

```
~/.local/share/edsys-changedetection-mcp/releases/TIMESTAMP-0.1.0/
  bridge.py server.py run.sh requirements.lock api-spec.json catalog-source.json
  LICENSE LICENSE.upstream NOTICE venv/
~/.local/share/edsys-changedetection-mcp/current -> releases/TIMESTAMP-0.1.0
~/.local/state/edsys-changedetection-mcp/artifacts/
/mnt/ai-store/edsys-private-services/deploy/
  changedetection-mcp-api-key                 # 0600; never in Git
  changedetection-mcp-registration.toml       # file path only, never the key
  changedetection-mcp-install.json            # receipt/source hashes
  codex-config-before-changedetection-TIMESTAMP.toml
```

Environment:

| Name | Value |
|---|---|
| `CHANGEDETECTION_BASE_URL` | Default/production `http://10.254.230.18:5000`; candidate `http://10.254.230.2:5000`. Only these approved Docker addresses or numeric loopback can receive the key. No host port is published. |
| `CHANGEDETECTION_API_KEY_FILE` | Default `/mnt/ai-store/edsys-private-services/deploy/changedetection-mcp-api-key`. |
| `CHANGEDETECTION_ARTIFACT_DIR` | Default `~/.local/state/edsys-changedetection-mcp/artifacts`. |
| `CHANGEDETECTION_DENY_ORIGINS_JSON` | Optional JSON array of additional management origins. |
| `CHANGEDETECTION_DENY_CIDRS_JSON` | JSON array of the exact owned app/browser/egress CIDRs, in addition to common forbidden ranges. |

`./install.sh /absolute/new-release-directory` creates only a new isolated
release using `uv` and hash checking. Manage releases as immutable; outputs are
separate. Never run pip against system Python or an existing production venv.
`run.sh` sets a private umask, disables bytecode/user-site loading and clears
Python path overrides. Initializing/listing tools/searching the catalog work
with the backend down and without reading the key.

Retain the qualified release (including venv) as an opaque
`images/changedetection-mcp-runtime-0.1.0.tar.zst`, with metadata and hashes
binding at least `bridge.py`, `server.py`, `run.sh`, `requirements.lock`,
`api-spec.json` and `catalog-source.json`. The existing strict app backup must
not walk runtime venv symlinks. Include the source directory, opaque archive,
receipt, private key and registration in that backup. Verify native datastore
and bridge credentials agree without printing either the key or its hash.
Artifacts are excluded; back up needed content with its owning workflow.

Restore the archived release at its original absolute path on the qualified
host runtime, restore the protected key and registration, and point `current`
to that release. A venv archive is not a portable cross-host Python installer.
Keep the exact prechange Codex configuration for rollback; restore it wholesale
only if the current configuration still matches this deployment's recorded
postchange hash. Otherwise reverse only this MCP entry and preserve later
unrelated edits. Reload MCP configuration without restarting the backend, then
verify a fresh configured-Codex returned-data call. Parent maintenance owns the
final backup/cold-restore test, dashboards and publication.

## Verification

```
PYTHONDONTWRITEBYTECODE=1 /path/to/isolated/venv/bin/python -m unittest -v test_bridge.py
bash -n install.sh run.sh
/path/to/isolated/venv/bin/python verify_live.py --help
```

`verify_live.py` uses a fresh official SDK stdio session, a selected disposable
candidate backend/key and a synthetic fixture file/URL. It first proves startup
with an unreachable backend and missing key, then creates uniquely named
paused/muted fixtures, checks real returned history/diff data and configured
browser output, and deletes only its recorded UUIDs. It never changes global
notifications or real watches. Production synthetic commissioning requires the
explicit `--allow-production-fixtures` switch. Use a new private work
directory for every run; pass the restored candidate route/key to repeat the
same acceptance after recovery.

Qualified source acceptance on 2026-11-01: 37 offline tests and 69 real stdio
calls covered text history/diff, private-header redaction, browser steps and
screenshot, two visual history images, saved crop/sensitivity settings, AI
disabled by default, backend-down startup and cleanup of five synthetic watches
plus one tag. Parent commissioning separately verifies managed-Codex access,
production configuration and cold recovery.

For managed-Codex acceptance without an LLM, import `exercise_api` and supply an
async `call(tool_name, arguments) -> dict` adapter that raises on tool errors,
a new owned 0700 `work` directory, `fixture_url`, `fixture_state_file`, and
optional `browser=True`/`visual=True`. The caller owns transport/initialization.
`secret_markers=(key,)` can verify the privately loaded native credential never
appears in results/artifacts; do not log that argument. The shared exercise
checks the same returned data and deletes only its recorded synthetic UUIDs.
