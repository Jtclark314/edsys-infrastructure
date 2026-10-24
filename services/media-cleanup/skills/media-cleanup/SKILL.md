---
name: media-cleanup
description: Delete requested movies or whole TV series and their associated files from Jeremy's Plex library in one call. Use for named titles or readable Plex screenshots, including dry-run previews.
---

# Media Cleanup

For an explicit deletion request, call `media_delete(titles=[...])` once. Movies are the default; use `kind="show"` for whole TV series. Read requested titles and visible years from screenshots before calling.

Clear matches are deleted immediately, including library files and confirmed title/year download folders with their subtitles, archives, artwork, and alternate encodes. No media backups or full-video comparisons are made.

- Ask only when the requested title/selection is ambiguous or unreadable, or the tool returns `needs_clarification`. Resolve that ambiguity, then call with the clarified titles. Do not ask for a second deletion confirmation.
- For a preview, discussion requiring a lookup, or testing, use `dry_run=true`. A request to create or test the tool does not authorize deleting real media.
- For a whole franchise, resolve its film titles (including differently named prequels) and pass them together; set `include_collections=true` only for that explicit scope. Recorded collections can supplement the list but do not prove completeness. Otherwise, sequels stay in place.
- Season/episode-only deletion is unsupported; report that limit rather than deleting the whole show.
- Playback, pending downloads, and unresolved folder ownership are reported as exceptions. Do not stop playback or change services to clear them.

If a call is interrupted or reports failed work, resume using `media_delete(operation_id=...)`. The tool reads what completed and avoids repeating uncertain deletions. Skipped titles stay skipped in that operation; report them rather than retrying them. `media_status(plan_id=operation_id)` reads the recorded result.

If the MCP tool is unavailable, the same current-source workflow is `/home/jeremy/.local/bin/edsys-media-cleanup delete 'Title (year)'`; add `--dry-run` for a preview or `--operation-id ID` to resume.

Keep the response short: removed titles, approximate observed space change when useful, and any missing/skipped/failed titles or retained files. Only provide detailed accounting when requested. Private operation records and title inventories stay outside Git/RAG. Re-download prevention applies to verified ARR-managed exclusions.
