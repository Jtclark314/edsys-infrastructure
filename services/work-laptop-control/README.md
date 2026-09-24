# Work laptop computer control

Source for Codex on 9950x to operate the work laptop's existing Windows desktop.
Human viewing remains Sunshine/Moonlight; Fleet inventory remains a separate service.

## Architecture

- Microsoft WinApp CLI **0.6.0**, official x64 portable release. SHA-256:
  `f6dc42e3b4e4709c8f617003008e2cfdd9a51735e04e7170d60edda258db78a8`.
  Require a valid Microsoft Authenticode signature before installation.
- `agent.ps1`: limited interactive user task, starts at logon, tray pause/resume,
  request expiration, process timeout, metadata-only audit, hourly artifact expiry.
- `controller.py`: authenticated SSH carries atomic JSON requests and private
  SCP artifacts. Serializes hub callers. No request replay after an uncertain result.
- `mcp_server.py`: seven discoverable Codex tools. Python runtime pins `mcp==2.2.0`.
  No additional model API, API key, server port, firewall rule, or public endpoint.

## Installation

Keep runtime config, package downloads, screenshots, and logs outside Git.
Download `winappcli-x64.zip` from the official Microsoft winappCli v0.6.0 release,
verify its hash, extract into `<user-local-root>/bin`, and verify Authenticode.
Copy `agent.ps1` and `install.ps1` into the user-local root. As administrator run:

```powershell
./install.ps1 -Root '<user-local-root>' -User '<domain>\<user>'
```

The task requires the user to be signed in. The installer applies an ACL restricted
to that user, SYSTEM and administrators. No login credentials are stored.
Create a dedicated hub Python venv and install `mcp==2.2.0`. Hub private config:
`~/.local/share/edsys-work-laptop-control/config.json`, mode 0600:

```json
{"ssh_config":"<existing-private-ssh-config>","host":"<existing-ssh-alias>","remote_root":"<user-local-root>"}
```

The SSH alias must retain the existing pinned host identity and dedicated hub key.
The hub wrappers `edsys-work-laptop` and `edsys-work-laptop-mcp` execute the Python
controller and MCP source. Add the MCP wrapper under `[mcp_servers.edsys-work-laptop]`
in the hub Codex configuration. Existing tasks may need a tool refresh/new task;
the CLI is immediately usable without restarting Codex or its managed gateway.

## Operation

```sh
edsys-work-laptop status
edsys-work-laptop ui list-windows --json
edsys-work-laptop ui inspect -w <observed-hwnd> --depth 5 --json
edsys-work-laptop ui invoke <observed-selector> -w <observed-hwnd> --json
edsys-work-laptop screenshot --monitor 0
edsys-work-laptop ui send-keys 'ctrl+l' -w <observed-hwnd> --via send-input --json
edsys-work-laptop powershell --file /absolute/private/script.ps1
edsys-work-laptop powershell --admin --file /absolute/private/admin-script.ps1
edsys-work-laptop pause
```

Use observed HWNDs to avoid ambiguous applications. Inspect before acting and
verify the resulting state. UIA selectors provide browser form control as well as
Windows application control; this is not a Chrome DevTools/DOM connection.
Desktop coordinates are physical pixels across the virtual desktop. The status
response includes each monitor's origin; add that origin to screenshot coordinates.
The screenshot command defaults to the primary display, not screen index zero.

Prefer UIA `invoke`/`set-value`, with physical input for controls that require it.
`ui <command> --help` provides installed-version syntax. UI output includes the
actual exit code; successful transport alone does not establish successful input.
Screenshots/video download into a private hub directory, with 24-hour pruning on
artifact retrieval. Remote artifacts/results expire after one hour while running.
Do not publish UI captures or raw observations in Git, Obsidian, or RAG.

Local pause/resume is on the EdSys tray icon. While paused, status still works and
desktop requests fail. Administrative SSH is independent and remains available.
Pausing interrupts the current command but cannot undo actions already delivered.
An action may be partial on cancellation/timeout: inspect before retrying.

## Boundaries and recovery

The worker uses a normal user token. Locked/secure desktops and elevated-window
input are not bypassed. Use administrative SSH for system tasks; unlock Windows
for visual interaction. Existing work, browser profiles, Sentinel policy and
Sunshine are preserved. A reboot/sign-out recovery test must be recorded separately;
a registered logon trigger is not proof of natural reboot acceptance.

The task restarts on failure up to three times at one-minute intervals. Heartbeat
older than 90 seconds rejects enqueue. Requests expire after 40 seconds, execute
once, and child commands have a 25-second limit. After a crash, `.running` files
are never replayed. Restart only this task after examining its startup error and
metadata audit. The tray pause flag persists across restarts.

Rollback: stop and unregister `EdSys-Codex-Desktop-Control`; remove only its hub
MCP entry/wrappers. Retain or remove this dedicated runtime directory after any
needed private evidence review. This does not alter SSH, Sunshine, Fleet, or
employer security policy. Backup source and private configuration; screenshots
and request/response state are ephemeral and should not be backed up as source.

## Validation

`python -m unittest discover -s tests -v`, Python compilation, Windows PowerShell
parser check, actual MCP discovery/calls, real scratch-window and browser UI
input/capture checks, pause rejection/resume, and agent-only restart acceptance.
Record dated live results in Hub Operations; source presence is not runtime proof.

References: [Microsoft UI Automation](https://learn.microsoft.com/en-us/windows/apps/dev-tools/winapp-cli/ui-automation),
[official release](https://github.com/microsoft/winappCli/releases/tag/v0.6.0).

Verified transport detail: Windows PowerShell 5.1 must read request/response JSON
with explicit UTF-8. Native Windows ACL tools require normalized backslash paths;
set the root ACL once and reset child inheritance rather than removing it recursively.

Live acceptance on 2026-09-23: all seven MCP tools discovered; status and PNG
delivery; UIA read/set/invoke; physical clicks and exact Unicode text readback;
three monitor captures and window drag between displays; Chrome and Edge form
entry/result verification; page scrolling; local pause/rejection/resume; worker-only
restart; real timeout/child termination with worker recovery. Edge first-load blank
content resolved by refreshing the test page. No business records were edited.
