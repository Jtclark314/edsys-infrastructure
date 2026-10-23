# EdSys Media Cleanup

A dedicated stdio MCP server, CLI, and Codex skill for removing exact movies or
whole TV series from Jeremy's existing Plex/ARR library. The assistant resolves
names or readable screenshot titles; deterministic code inventories, removes,
and verifies the selected media. No AI API, OCR service, new credential, network
listener, timer, or background deletion job is added.

## Tool contract

| Tool | Behavior |
| --- | --- |
| `media_find(queries, kind="any", offset=0)` | Read-only batch lookup of 1–25 titles; returns exact refs, title/year/type, management and collection information. Follow each query's `next_offset` until null for complete results. |
| `media_plan(target_refs, include_collections=false, include_download_copies=true)` | Builds a private 30-minute preview for 1–50 exact refs; no media changes. For checked staging recovery, omit `target_refs` and pass `recovery_plan_id=SOURCE_PLAN_ID`. |
| `media_execute(plan_id, selection_hash, user_requested_delete)` | Applies the checked, explicitly requested deletion once, then verifies files, library records, and managed exclusions |
| `media_status(plan_id)` | Reads completion/uncertainty and remaining files without retrying writes |

Collection expansion is opt-in for an explicitly requested full franchise and
is capped at 100 titles. Entire series are supported; season/episode-only
removal is not. Screenshot interpretation remains the calling model's job.
Unreadable posters, remakes, uncertain selection marks, or movie/show ambiguity
must be resolved before execution. Existing clear deletion authorization does
not require a second approval question. A preview, discussion, or request to
build/test the tool does not authorize deleting real media.

The [skill](skills/media-cleanup/SKILL.md) keeps this workflow short enough for
routine use by a smaller model. It remains available for automatic discovery
and explicit `$media-cleanup` invocation.

## Existing access and scope

- Host: canonical `9950x`, running as Jeremy through an isolated Python venv.
- Media pool: existing `/mnt/media` mergerfs mount; one direct movie directory
  below `/mnt/media/movies` or series directory below `/mnt/media/tv` per title.
- Old download-copy cleanup: fixed `/mnt/media/downloads` tree. Physical inode
  identity or complete SHA-256 equality identifies video copies; arbitrary
  title substrings do not authorize file deletion. Sidecars are included only
  in safely identified release directories.
  Release directories positively owned by unselected catalogue titles are
  pruned before traversal; unreadable unknown or selected directories still block.
  Full-content copy verification allows up to 12 minutes and 64 GiB of reads
  per preview; exceeding either limit stops before deletion.
- ARR: existing `arr-server` SSH route and private Radarr/Sonarr stores. The
  fixed remote helper uses only bounded, predefined operations and never
  returns credentials. Native deletion adds Radarr import exclusions or Sonarr
  import-list exclusions. Whole-collection requests also disable the selected
  Radarr collection's automatic additions.
  Post-delete confirmation polls read-only record/exclusion endpoints for up
  to 10 seconds to accommodate asynchronous ARR events; DELETE is sent once.
- Plex: fixed loopback API using the existing Preferences credential under
  `/srv/plex-config`. Exact selected entries are removed; unrelated library
  entries and global trash settings are preserved.

Unmanaged Plex-only items can be removed when their paths map unambiguously to
one supported directory. They have no ARR-managed exclusion; do not claim that
their re-downloads are prevented. Missing tracked titles can be removed and
excluded without claiming any media-space gain.

Execution refuses stale/changed mounts, changed or added files, unsupported or
overlapping roots, symlink paths, active playback (including episodes within a
selected series), pending ARR downloads, and configured ARR recycle bins.
The native ARR delete also rechecks the container's own file view immediately
before dispatch. Neither the tool nor the skill restarts services or clears
these blockers automatically.

Hard-linked data is counted once by physical device/inode. Unknown links
outside the selected library/download scope are retained and reported.
`expected_reclaimed_bytes` describes reclaimable allocation;
`allocated_bytes_unlinked` is not proof that every block became free.
`observed_available_bytes_delta` is measured across distinct backing
filesystems and includes concurrent writes or delayed release from open files.
When requested, the skill maintains task-scoped running totals in private
`~/.local/state/edsys-media-cleanup/totals/` ledgers, deduplicated by plan ID;
uncaptured measurements from interrupted calls remain unknown.
Execution persists its initial space reading before destructive work and
records an available stopped-state reading when execution ends early. These
readings cannot reconstruct historical measurements that were never captured.

## Install and register

Run as Jeremy on `9950x`:

```bash
cd /srv/edsys/edsys-infrastructure
bash services/media-cleanup/install.sh
```

The installer creates an isolated, locked-dependency venv under
`/home/jeremy/.local/share/edsys-media-cleanup`, installs the two launcher links
under `/home/jeremy/.local/bin`, and links the source skill into
`/home/jeremy/.codex/skills/media-cleanup`. It does not change media or register
the MCP server.

Register only this server in the existing private Codex configuration:

```bash
python3 services/media-cleanup/register.py
```

The registrar backs up the private configuration and adds only the following
block; it verifies that all other settings are unchanged:

```toml
[mcp_servers.edsys-media-cleanup]
command = "/home/jeremy/.local/bin/edsys-media-cleanup-mcp"
enabled = true
enabled_tools = ["media_find", "media_plan", "media_execute", "media_status"]
startup_timeout_sec = 20
tool_timeout_sec = 900

[mcp_servers.edsys-media-cleanup.env]
PYTHONUNBUFFERED = "1"
```

Preserve existing approval settings and unrelated MCP registrations. A client
configuration reload or a fresh Codex chat may be needed to discover the new
catalogue. Media services do not need restarting. The tools mark lookup/status
read-only. After source updates, use a fresh MCP session or the CLI to load
the updated Python modules. The CLI loads current source on every invocation.
The remaining annotations mark
planning as a private nondestructive write, and execution as a
destructive write. See official [MCP configuration](https://learn.chatgpt.com/docs/extend/mcp)
and [skills guidance](https://learn.chatgpt.com/docs/skills-and-plugins).

## CLI

The CLI uses the same engine, checks, private plans, and journal:

```bash
/home/jeremy/.local/bin/edsys-media-cleanup find --kind movie 'Example Title (2001)'
/home/jeremy/.local/bin/edsys-media-cleanup plan movie:radarr:123
/home/jeremy/.local/bin/edsys-media-cleanup status PLAN_ID_FROM_PREVIEW
/home/jeremy/.local/bin/edsys-media-cleanup recover-plan SOURCE_PLAN_ID
/home/jeremy/.local/bin/edsys-media-cleanup execute PLAN_ID_FROM_PREVIEW SELECTION_HASH_FROM_PREVIEW --user-requested-delete
```

The identifiers above are examples, not a deletion request. Obtain actual refs
from `find`, check the full `plan` response, and use its exact plan ID/hash only
for the user's selected titles. CLI errors and partial/uncertain execution
return exit status `2`.

## Verification

From the infrastructure repository:

```bash
python3 -m unittest discover -s services/media-cleanup/tests -v
python3 /home/jeremy/.codex/skills/.system/skill-creator/scripts/quick_validate.py services/media-cleanup/skills/media-cleanup
bash -n services/media-cleanup/install.sh services/media-cleanup/edsys-media-cleanup services/media-cleanup/edsys-media-cleanup-mcp
/home/jeremy/.local/share/edsys-media-cleanup/venv/bin/python services/media-cleanup/verify_live.py --movie-query 'Exact Stored Movie (2001)' --show-query 'Exact Stored Show'
```

Use live `find` and `plan` calls for read-only access qualification. A preview
creates only private runtime evidence. Test destructive execution against
isolated fixtures; creating this tool does not authorize deleting a real title
as a test. Verify SDK discovery, tool annotations, invalid-input rejection,
and read-only live calls after installation before declaring the integration
available.

## State, recovery, and rollback

Plans, signing material, and action journals live in the private directory
`/home/jeremy/.local/state/edsys-media-cleanup` with restricted permissions.
Plans expire after 30 minutes. The audit store refuses new plans at its count
or size quota rather than silently discarding evidence. Review/archive private
evidence separately when required; no cleanup timer is installed.

Execution takes a global lock and records intent before each destructive
operation. A completed plan returns its recorded result when called again.
Partial, uncertain, or interrupted execution requires `media_status` and live
inspection; writes are never blindly retried. A checked recovery plan is
available only when a signed partial/uncertain source plan has verified that
all selected library files and Plex/ARR records are absent and required managed
exclusions are present, leaving only original staging files.

Call `media_plan(recovery_plan_id=SOURCE_PLAN_ID)` without `target_refs`, or
`edsys-media-cleanup recover-plan SOURCE_PLAN_ID`. Recovery revalidates the
original full-content proof, virtual and physical file identities, media pool,
ownership, overlaps, playback, and queues. A new signed 30-minute preview names
its source as `recovery_of`; check its exact remaining files and `ready=true`,
then execute its own plan ID/hash. The original clear deletion request covers
these same files. Recovery removes only validated remaining staging files;
it does not repeat any ARR, Plex, or collection write. Other incomplete states
require inspection and cannot use this shortcut.

File deletion is permanent: a plan/journal is evidence, not a backup or a media restore.
Existing media backups retain their own recovery procedures and are outside
this tool's deletion scope.

Back up reviewed source and this small service's installation/configuration
references through normal infrastructure maintenance. Keep runtime evidence,
screenshots, private inventories, Credentials/Preferences files, and API
responses out of Git, Obsidian, and general RAG.

To remove the integration, remove only the `edsys-media-cleanup` MCP table
(including its `env` subtable) and these installed links:

- `/home/jeremy/.local/bin/edsys-media-cleanup`
- `/home/jeremy/.local/bin/edsys-media-cleanup-mcp`
- `/home/jeremy/.codex/skills/media-cleanup`

The dedicated venv may then be removed separately. Retain private audit
evidence unless its deletion is requested. Removing the integration does not
restore deleted media or remove recorded ARR exclusions. No Plex, ARR,
downloader, VPN, or shared Codex backend restart is required for rollback.
