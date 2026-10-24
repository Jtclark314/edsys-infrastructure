# EdSys Media Cleanup

One call resolves requested movie titles, removes library and associated download
files, updates Plex/Radarr/Sonarr, and returns a short result. Whole TV series
are also supported. There are no media backups, full-video comparisons, new
credentials, listeners, scheduled deletions, or media-service restarts.

## Normal use

```python
media_delete(titles=["Example Title (2001)", "Another Movie"])
```

Movies are the default. Set `kind="show"` for whole series or `kind="any"`
when explicitly needed. Exact normalized titles match automatically; a year
resolves remakes. A nonexact candidate or multiple exact matches returns
`needs_clarification` before any title in that batch is deleted. Missing titles
are reported. A clear deletion request needs no second confirmation.
Plex display titles attached to the same mapped ARR library root are retained
as aliases, so either exact display title resolves automatically.

Use `dry_run=true` for a preview or test. Set `include_collections=true` only
for an explicitly requested whole franchise/collection. This expands recorded
collection membership and turns off its automatic additions; it cannot establish
that the library contains every film in a franchise. Season/episode deletion
is unsupported and must never become whole-series deletion.

```bash
/home/jeremy/.local/bin/edsys-media-cleanup delete 'Example Title (2001)'
/home/jeremy/.local/bin/edsys-media-cleanup delete --dry-run 'Example Title (2001)'
```

The installed skill is `/home/jeremy/.codex/skills/media-cleanup/SKILL.md`.
Keep assistant reporting to removed titles, an approximate observed space change
when useful, and specific missing/skipped/failed titles or retained files.

## Files and execution

- Fixed library roots are `/mnt/media/movies` and `/mnt/media/tv`; managed
  titles normally own one direct directory. A Plex-only movie may instead map to one
  exact top-level video file, including matching same-stem sidecars with supported
  language/accessibility suffixes. The library base is never a deletion target
  or recursively traversed for a standalone movie. Flat TV files are unsupported.
  Movie requests read the movie catalog without enumerating TV libraries.
  A normal request reads its catalog once.
- Additional library versions of one ARR movie are included only when Plex's
  sole TMDB GUID and year match that record, every direct directory name matches
  its canonical/Plex title and release year, and no other ARR/Plex record owns
  those roots. Ownership is read again before native deletion and local file
  cleanup, including resumed work. All confirmed versions are removed. Native ARR inventory and
  deletion stay anchored to the primary directory; exact-manifest cleanup
  removes the additional versions. Shared or unsupported multi-folder mappings
  remain reported exceptions. This does not enable multiple roots for TV or
  unmanaged Plex movies.
- Associated downloads are exact normalized title/year release directories
  below `/mnt/media/downloads`, including folders under up to four wrapper
  levels. Whole-series releases use title/season names. The search indexes
  ownership once, prunes other known titles, and ignores unreadable unrelated
  branches while reporting incomplete download coverage.
- All files in a confirmed release are included: alternate encodes, Blu-ray
  structures, subtitles, artwork, and archives. No equality with the library
  encode is required and video contents are not hashed. Mixed/shared-title
  releases and matching symlinks are retained and reported. Unknown/renamed
  releases without title/year ownership are outside this automatic match.
- File identities, physical mergerfs paths, supported roots, playback, pending
  downloads, and native library mappings are checked. Active/queued titles and
  unsupported folder mappings are skipped and reported; unrelated titles can
  still complete. No service or playback changes clear these exceptions.
- Native ARR deletion removes library media and adds import exclusions. Exact
  manifest cleanup removes remaining associated files. Plex entries and final
  file absence are verified. Empty selected directories are removed when possible.
  Plex-only titles have no ARR exclusion.
- Native deletes are sent once. Readback reconciles lost responses and accommodates
  asynchronous ARR events; a short read-only absence poll handles stale local
  FUSE/NFS views after native file removal. Changed/new files are retained and
  reported rather than replaced or recursively deleted.
- Free-space change is measured across distinct backing filesystems. Concurrent
  writes, open files, and hard links affect that approximate measurement. External
  hard links remain; a logical file-size sum is not a reclaimed-space claim.

## Interrupted operations

Private signed manifests and small progress records live under
`/home/jeremy/.local/state/edsys-media-cleanup`, outside Git/RAG. They contain
no media backup. Execution uses one global lock.

Resume interrupted/failed work with `media_delete(operation_id=ID)` or:

```bash
/home/jeremy/.local/bin/edsys-media-cleanup delete --operation-id ID
/home/jeremy/.local/bin/edsys-media-cleanup status ID
```

Resume validates original identities and current ownership, reads what completed,
and proceeds without repeating an uncertain ARR/Plex deletion. If readback cannot
establish completion, that title stays incomplete. Completed operations return
their recorded result with no further media/service writes. Initially skipped
titles stay skipped in that operation; report them rather than repeatedly resuming.

The old `find`, `plan`, `execute`, and `recover-plan` CLI commands remain for
existing signed plans and diagnostics. They are not the normal assistant workflow
and are no longer exposed as MCP tools. Their 30-minute plans and historical
quotas do not apply to new one-step operations.
Legacy `plan` refuses standalone movie files and multi-folder movies, directing
them to the one-step `delete` command.

## Install and qualify

```bash
cd /srv/edsys/edsys-infrastructure
bash services/media-cleanup/install.sh
python3 services/media-cleanup/register.py
python3 -m unittest discover -s services/media-cleanup/tests -v
python3 /home/jeremy/.codex/skills/.system/skill-creator/scripts/quick_validate.py services/media-cleanup/skills/media-cleanup
bash -n services/media-cleanup/install.sh services/media-cleanup/edsys-media-cleanup services/media-cleanup/edsys-media-cleanup-mcp
/home/jeremy/.local/share/edsys-media-cleanup/venv/bin/python services/media-cleanup/verify_live.py
```

The registrar migrates only this server's tool allowlist to `media_delete` and
`media_status`, preserving other settings. Its small private config backup is
not a media backup. A fresh MCP session loads the new tool catalog; the CLI
loads current source on every call. No shared backend restart is required.

`verify_live.py` launches a dedicated MCP session with a server-side
`EDSYS_MEDIA_CLEANUP_READ_ONLY=1` guard, exercises the actual one-call dry-run,
and proves that a non-dry-run request is rejected. Destructive tests use only
disposable files and fake services. A separate explicit owner deletion request
is necessary for any real-media execution.
The isolated suite now covers 156 tests, including Plex display aliases,
exact standalone movie/sidecar handling, and confirmed additional movie versions.
These fixture tests never delete real library media.

## Source and removal

Operational documentation: `/home/jeremy/code/EdSys-Master/docs/MEDIA_CLEANUP.md`.
Existing ARR SSH and Plex credential stores are reused without returning secrets.
Private manifests, title inventories, screenshots, and service responses never
belong in Git, Obsidian exports, or general RAG.

To uninstall, remove only this server's Codex MCP table and its installed CLI,
MCP launcher, and skill links. Keep operation records unless their removal is
requested. Uninstalling does not restore deleted movies or remove exclusions.
