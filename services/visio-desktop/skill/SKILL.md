---
name: visio-desktop-pilot
description: Create, inspect, or update editable Microsoft Visio diagrams through the official local MCP on Nimo, or prepare a bounded handoff from 9950x. Use for source-linked architecture and process diagrams; use normal charting or CAD workflows for charts or engineering drawings.
---

# Visio desktop evaluation

Microsoft's pinned Visio MCP is an evaluation preview. Use evaluation drawings
and copies of existing files; operational adoption requires suitable vendor
release status and terms. Preserve the user's requested read/edit/save scope.

## Select the execution route

- On Nimo's native Windows desktop, use the connected `visio-local` MCP. The
  native worker supplies isolated model/server settings per invocation. Outside
  the worker, use the workspace's `Open-VisioPilot.ps1` intake or
  `run_native_codex.py` with a staged brief. Keep Visio `required=true`; a
  successful config lookup alone does not prove runtime tools are available.
  The native CLI profile connection passed an actual schema call; interactive
  TUI and direct Codex Desktop app use are not independently qualified.
- On 9950x, use `/srv/edsys/edsys-infrastructure/services/visio-desktop/handoff.py`.
  `status` checks readiness; `run --prompt /absolute/prompt.txt` stages a bounded
  prompt and launches the existing interactive Windows worker. For inspection,
  add `--read-only` to exclude drawing-write MCP tools. `collect
  --destination /absolute/private/folder` retrieves the generated artifacts.
  Add `--job <exact-job-name-from-status>` to collect that inspection's answer
  and receipt rather than potentially unrelated drawing artifacts. A launch
  result means started, not completed. Inspect status and actual outputs.
- Do not launch the Visio server in SSH, WSL, a container, a Windows service, or
  a cloud workspace. Run client, server and Visio as the same signed-in Windows
  user. Respect a paused or unavailable Nimo desktop.

The operating guide is `/home/jeremy/code/EdSys-Master/docs/VISIO_AI_PILOT.md`
on 9950x. The implementation and recovery procedure are in the sibling
`services/visio-desktop/README.md`. On Nimo, the workspace is
`%USERPROFILE%\Documents\VisioMcp\EdSysPilot`.

## Author and maintain

Identify the requested drawing/page, source, allowed changes and save policy.
Keep observed, documented, proposed and example states explicit. Missing source
values remain unknown. Construction record status, approval and field
verification are separate facts; a diagram does not change source records.

For new content, discover genuine masters with `content_search` and fetch
`get_visual_model_schema`; reuse returned master/source pairs. Use actual
connectors, native containers and genuine swimlane membership. Store stable
source IDs, provenance and status in Shape Data; add approved source hyperlinks.
Use visible titles, legends and readable labels. Split dense work into linked
overview/detail pages when useful.

Before editing, read the saved/live drawing at the required scope. Use returned
shape identities, preserve unrelated content, and avoid simultaneous human or
agent edits. Session-scoped diagram IDs are not durable source identifiers.
Apply focused batches; inspect operation results and partial changes before
retrying. Never close/reopen as a generic recovery action.

For a read-only request, do not save, repair, export or reopen the drawing.
Read the exact file or supported live identity and report saved/live coverage.
Do not substitute another file if the requested one is missing. Inventory all
semantic shapes, including those without SourceID, before judging evidence
coverage. Native lane membership and resolved source evidence require their own
checks; a rectangle, group, label or nearby citation is insufficient.
Saved-file/hyperlink inspection can retain a document in the managed instance.
A subsequent application close may return CONFLICT. Report that state; do not
add drawing-write tools, save the source, or force termination to clear it.
Separately authorized operator cleanup can normally close the identified owned
window after inspecting state and preserving the source bytes; leave unrelated
instances untouched.

## Commissioned starting points

The workspace artifacts include `edsys-ai-evaluation-v1.vsdx` and the illustrative
Foothills RFI example. Use accepted native templates
`architecture-starter.vstx`, `swimlane-starter-final.vstx`, and
`comparison-starter.vstx` through manage_document create with options.template;
create a new drawing rather than modifying the template. Earlier/recovery
versions are not accepted starting points. Native stencil fields such as empty
owners or default zero costs/dates are not source facts.

## Verify and deliver

Reconcile exact source IDs, labels, directed relationships, metadata and native
membership. Retrieve all issued readback chunks; partial coverage cannot prove
absence. This preview has produced an incorrect connector summary count: use
complete detailed records and independent saved-package checks where practical.
`verify_vsdx.py drawing.vsdx --model source.json` checks saved source-ID nodes,
labels, statuses and glued endpoint relationships; it does not validate all
Visio features, business truth, or visual appearance.

Run visual validation separately, report skipped checks, and inspect exported
previews for authorized creation/editing. Repair presentation without changing
meaning only within that authorization. Explicitly save the VSDX when requested;
exporting does not save it. Use new versioned filenames/overwrite=false
when preserving earlier outputs. Reopen and read the saved file for significant
creation/edit acceptance. These save/reopen checks do not apply to read-only
inspection. Close only generated saved documents and the managed
instance when safe; preserve original user drawings and automation settings.

Deliver the editable drawing, useful PNG/SVG/PDF previews, a structured/text
summary, source/revision references and genuine limitations. Keep runtime logs,
private drawings, raw receipts, backups and account state outside Git/RAG.
Publication or source-record writes require the user's requested scope.
