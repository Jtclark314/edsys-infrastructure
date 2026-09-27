# Nimo desktop control from 9950x

Purpose: let a Codex task running on the canonical 9950x hub operate Jeremy's
existing Nimo Windows session, including its signed-in Vivaldi and Windows apps.
This is an SSH-carried Windows UI bridge, separate from native Codex Computer Use.
It does not create a listener or move browser profiles or credentials to 9950x.

## Source and deployment

The Nimo-specific hub controller, MCP tools and Windows worker are in this
folder. The administrator installer is shared with the accepted work-laptop
path at `../work-laptop-control/install.ps1`. Nimo's worker adds exact-window
activation for physical input and refuses input without an observed HWND.
The hosts have independent runtime roots and Scheduled Tasks; changing one host
does not restart the other. The shared worker uses signed Microsoft WinApp CLI
0.6.0; verify official archive SHA-256
`f6dc42e3b4e4709c8f617003008e2cfdd9a51735e04e7170d60edda258db78a8`
and a valid Microsoft Authenticode signature on `winapp.exe` before installing.

Nimo private root: `C:\ProgramData\EdSys\NimoDesktopControl`. The
`EdSys-Codex-Desktop-Control` Scheduled Task runs as `NIMO-LAPTOP\jtcla`,
`Limited` and `Interactive`, at logon. The root ACL admits only that user,
SYSTEM, and Administrators. The worker needs the user signed in; physical input
requires an unlocked desktop. Locked and secure UAC desktops are not bypassed.

Hub state is `~/.local/share/edsys-nimo-desktop-control`, mode 0700, with a
0600 `config.json` naming the existing pinned SSH config/alias and Nimo root.
Symlink `edsys-nimo`, `edsys-nimo-mcp`, and `edsys-nimo-view` into `~/.local/bin`; the MCP wrapper
uses its own venv pinned to `mcp==2.2.0`. Register `edsys-nimo` as a stdio MCP
using the wrapper. Install `edsys-nimo-view.desktop` into
`~/.local/share/applications`. The viewer reads a private 0600 `stream.json`
containing Nimo's Tailnet address and launches the locally pinned Moonlight
6.1.0 AppImage. Keep config, Moonlight pairing certificates, captures, requests
and audit outside Git/RAG.

## Operation and acceptance

```sh
edsys-nimo status
edsys-nimo ui list-windows --json
edsys-nimo ui inspect -w <observed-hwnd> --depth 5 --json
edsys-nimo screenshot --monitor -1
edsys-nimo pause
```

Inspect before acting; target observed HWNDs and selectors. Prefer UIA
`invoke`/`set-value`, using physical input where necessary. Confirm the actual
result through UIA or screenshot. On the tested Vivaldi 8.2 profile, WinApp
exposed the browser window and pane but not page elements; the live page form
was operated with guarded physical input and screenshot readback. This bridge
is not a DOM or Chrome DevTools connection. Use a separate automation profile
if a future workflow needs a Nimo-local Playwright browser. Do not enable remote
debugging on the normal Vivaldi profile or expose a DevTools port.
Physical input checks that the HWND belongs to this user session, brings it to
the foreground, and lets WinApp recheck the target immediately before injection.
When Windows foreground lock denies activation, the worker sends one Alt key
event and retries the exact HWND once; a second failure stops without input.
Coordinate drags must begin inside that window's current screen rectangle;
screenshot-relative coordinates must first be translated to screen coordinates.

The local tray menu can pause/resume control. A pause rejects desktop requests;
administrative SSH remains available. Requests expire, are serialized, and are
never replayed after an uncertain timeout. Screenshots and recordings are
short-lived private artifacts. PowerShell in the interactive session is limited;
administrative PowerShell uses the separate SSH session. An action may be partial
when interrupted; inspect before retrying.

Acceptance requires live status, MCP discovery, scratch-window input/readback,
Vivaldi page input/readback, a Windows app inspection, capture delivery,
file-transfer readback, pause/rejection/resume, and worker restart. Natural sleep
and reboot recovery must be observed separately. Source and Scheduled Task state
alone do not establish interactive control.

On 2026-09-27, the installed Nimo worker passed those checks: Notepad UIA and
physical input/readback, a Vivaldi local test page with a visible result, Excel
cell set/readback, SSH file hash match, pause/rejection/resume, MCP discovery and
worker restart. Bluebeam launched, but its sign-in returned 403; its document
workflow remains unverified. An existing recovery prompt was deferred with
"Later" so no unsaved document was discarded. Existing browser profile and
extensions were preserved. The Codex app may need a new chat or reload to
discover the newly registered MCP server; the CLI and direct stdio MCP worked.

## Live desktop view from 9950x

`edsys-nimo-view` or the **Nimo Desktop** application launcher opens a windowed
1080p60, 15 Mbps Moonlight session using the AppImage's persistent pairing.
The host installation procedure is `../sunshine-desktop/install-nimo-host.ps1`;
see its README for the exact-peer firewall and recovery boundary. Use
`Ctrl+Alt+Shift+Q` to disconnect without ending Nimo's Windows session. The
Moonlight CLI `list <Nimo Tailnet IPv4>` should still return `Desktop` after
the GUI closes. Do not copy `Moonlight.conf` to Git or another device.

Live acceptance on 2026-09-27 included a visible Nimo desktop, Windows Start
opened by input inside the stream, normal disconnect, reconnect, and persistent
pairing. Human-confirmed audio, long-session stability, and natural lock,
sleep/lid and reboot recovery remain to be confirmed. Keep Nimo signed in,
unlocked and awake for interactive actions.

Rollback: stop/unregister only Nimo's `EdSys-Codex-Desktop-Control` task, remove
only the `edsys-nimo` hub MCP/wrappers and viewer launcher, then retain or remove
this dedicated runtime root after any needed private evidence review. Revoke the
one current `9950x Moonlight` Sunshine pairing before removing the AppImage or
its private client state. Existing SSH, browser profile, apps and work-laptop
control are independent.
