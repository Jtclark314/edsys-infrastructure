# Visio Desktop AI evaluation

Owner: Jeremy. Runtime: Nimo's native signed-in Windows desktop. Lifecycle:
evaluation pilot, not a production service. No listener, port, hosted MCP,
scheduler, cloud diagram store, or additional AI-provider credential is created.
9950x retains source ownership and uses the existing Nimo desktop dispatcher.

## Package and prerequisites

Official package: `Microsoft.Visio.Mcp@1.0.0-preview.59024787`, obtained from
NuGet.org. Accepted package SHA-256:
`ebdd219eae4bf7b19ddb8b01857a3afdee57c4360571e9d51aa347e6c5df566b`.
Nimo's .NET verification accepted both Microsoft Corporation's author signature
and the NuGet.org repository signature before the confirmed startup.

Prerequisites are licensed/activated Visio Desktop, .NET 10 SDK, a supported
native Codex account/model, and an interactive Windows desktop. The preview
does not support SSH/WSL/container/service-hosted automation. Review the package's
current terms before extending its use beyond evaluation.

## Configuration and operation

The native workspace is `%USERPROFILE%\Documents\VisioMcp\EdSysPilot`. Its
`.codex\config.toml` holds the pinned command and visible output settings.
The accepted native worker uses `codex exec --ignore-user-config` with explicit
pilot-only model/server settings and existing authentication. The Nimo launcher
collects a bounded request and invokes this worker; inspection selects its
read-only tool allowlist. Global defaults are preserved.

An additive `%USERPROFILE%\.codex\visio.config.toml` profile remains available
for native CLI use. Its connection was verified with all eight tools and a real
schema call after making the server required. Interactive TUI and direct Codex
Desktop app use are not independently qualified; the accepted bounded route is
the native worker/launcher or 9950x handoff.

Launch command:

```text
dotnet tool exec Microsoft.Visio.Mcp@1.0.0-preview.59024787 --yes
```

The explicit confirmation flag is supported by the observed .NET SDK and is
necessary for noninteractive package startup. `VISIO_APP_VISIBLE=true`; output
defaults to the workspace's `artifacts` folder. That folder is not a filesystem
sandbox. The MCP has the Windows user's file permissions, and model processing
can occur at the AI provider.

Stage the reviewed helpers and skill in that workspace and the user's Codex
skills directory. After prerequisite/package checks, run `configure_profile.py`
on the native desktop: it creates the workspace/named profile, accepts identical
existing configuration and refuses conflicting files without replacing them.
It never edits the default config. `install-launchers.ps1` creates/reads back the **Visio AI
Pilot** Desktop and Start menu shortcuts. It refuses conflicting existing
launchers. The shortcut accepts a one-line request or UTF-8 brief file, then uses the
qualified worker. It creates or changes a diagram only when requested. Native
CLI worker automation is the accepted route.

From 9950x:

```sh
python3 services/visio-desktop/handoff.py status
python3 services/visio-desktop/handoff.py run --prompt /absolute/private/prompt.txt
python3 services/visio-desktop/handoff.py run --read-only --prompt /absolute/private/inspection.txt
python3 services/visio-desktop/handoff.py collect --job EXACT_JOB_FROM_STATUS --destination /absolute/private/results
# Optional private verification evidence, never publish:
python3 services/visio-desktop/handoff.py collect --job EXACT_JOB_FROM_STATUS --evidence --destination /absolute/private/results
python3 services/visio-desktop/handoff.py collect --destination /absolute/private/results
```

`run` stages one prompt and invokes the existing interactive dispatcher launch
action. It does not run Visio in SSH. No new queue daemon is installed. A started
PID or zero Codex exit code does not establish diagram correctness: inspect the
matching answer, receipt, actual files and source reconciliation. Use the job
selector for inspection answers; the unselected collector retrieves all pilot
drawing artifacts and may include earlier versions. Do not replay a launch after
an uncertain timeout. Inspect existing receipts/processes first.

`run_native_codex.py` reuses existing ChatGPT authentication without copying it,
uses an ephemeral run and read-only shell sandbox, and explicitly supplies only
the pilot MCP settings. `required=true` is mandatory: optional startup produced
tool-less sessions during acceptance; making the server required resolved them.
A deliberate broken-server test failed before model execution. Its `--read-only` mode also removes diagram-management,
creation, mutation and validation tools; application open/read/discovery remain.
Shell read-only mode alone does not restrict MCP writes. An inspection must not
save, export, repair, close/reopen the source, or substitute another drawing.

`mcp_batch.py` is a one-shot deterministic MCP diagnostic client. It refuses
non-console Windows sessions, checks the eight-tool catalog, runs sequential
explicit jobs, records results before continuing and attempts uniquely named
recovery saves only for drawings it created if a batch fails. Runtime jobs and
receipts remain outside Git.

## Verification

```sh
python3 -m unittest discover -s services/visio-desktop/tests -v
python3 services/visio-desktop/verify_vsdx.py /absolute/drawing.vsdx --model services/visio-desktop/source-models/edsys-ai.json
```

The saved-package verifier independently checks supplied source IDs, labels,
statuses, provenance, hyperlinks, declared text decorations and glued directed
relationships. It is supplemental: native lane membership, source authority,
live unsaved content, and visual quality need their own checks. Inspect all
semantic shapes including those without source metadata. Explicitly requested connector provenance is reconciled by exact edge SourceID
as well as directed endpoints. No count checker can prove business truth. Report evaluated/skipped visual checks separately.

Known preview observations: a detailed readback had five connectors while its
summary said six; Visio can expand page geometry during authoring. Reconcile
complete detailed records and the saved package, and verify final page bounds.
Exports do not save the VSDX. Keep prior versions; use explicit saves and
no-overwrite exports. Only one writer should work on a drawing at a time.

## Accepted examples and templates

- `edsys-ai-evaluation-v1.vsdx`: two pages, 19 components/18 directed relationships.
- `foothills-rfi-example-v4.vsdx`: synthetic RFI example, eight steps/eight
  directed relationships and three native lanes; step/connector provenance.
- `architecture-starter.vstx`, `swimlane-starter-final.vstx`,
  `comparison-starter.vstx`: verified native template content types and new
  drawing instantiation, native membership and inspected previews.

All are under the native workspace's `artifacts` folder. The swimlane `final`
file is the accepted template; earlier versions are retained recovery evidence.
Native stencil default zero cost/date/owner fields are not source facts.
The EdSys source model pins the Git revision reproducing its source hashes.

## Backup, recovery, and removal

Private pre-change recovery is under `%LOCALAPPDATA%\EdSys\VisioPilot` and is
not tracked. It includes the original global configuration copy and binary/
original-document baseline. Runtime reports, original account state, raw MCP
logs and private diagrams must not enter Git/RAG.

If a call fails, inspect partial changes and preserve the exact staged recovery
file or make a uniquely named recovery save. Do not restart or close unsaved
drawings to clear an error. Use the owned instance only. Visio automation events
can remain disabled in a managed instance; inspect the original user's setting
against the baseline and restore only a confirmed task-caused change.

Saved-file inspection with hyperlink enrichment can retain an opened drawing.
`manage_visio close` then rejects with CONFLICT until the retained document is
closed. Read-only mode deliberately excludes manage_document. Report this state;
do not expose write tools, save, or force-kill to clear it. An operator may
normally close the identified task-owned window after checking state/source
integrity. Commissioning verified an unchanged file hash after inspection and
normal closure; original user instances remained separate.

To disable this integration, stop its active job normally, preserve wanted
drawings, remove only the dedicated `visio.config.toml` profile and its two
verified shortcuts, and archive the dedicated workspace/skill if retiring it.
Do not restore whole Codex/Office profiles or uninstall shared .NET/Visio.
Global settings must remain semantically unchanged. The observed native CLI
normalized config formatting on startup without changing TOML values; retain
the original private copy and compare semantics as well as binary hashes. Any package update
requires a new pin, signature verification and repeat acceptance; no automatic
update or unattended schedule is configured.

See `/home/jeremy/code/EdSys-Master/docs/VISIO_AI_PILOT.md` for user-facing
operation and acceptance, and Microsoft's
[official documentation](https://microsoft.github.io/Visio-Desktop-MCP-Server/).
