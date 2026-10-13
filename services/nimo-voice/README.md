# Nimo Home Assistant voice

Status, 2026-10-13: deployed native Windows client. The owner pressed **Alt+V**,
asked “What time is it?” and confirmed hearing Home Assistant's spoken reply.
This acceptance used Ethernet. Wi-Fi handover, sleep and Windows logon recovery
remain to be confirmed; the client uses the same HTTPS hostname on either NIC.

## Use and speech path

Press Alt+V from a normal desktop application, then speak when HA opens its
microphone. Replies play through Windows' selected output. The window's close
button stops the native Assist dialog and hides Nimo Voice in its tray. Use the
tray menu's Exit to release the shortcut and stop the application. Desktop and
current-user Startup shortcuts are installed; no administrator startup task is
needed. This is press-to-talk with automatic end-of-speech, not a wake-word
listener or a key that must be held.

Nimo Voice hosts HA's native frontend in Microsoft WebView2. Alt+V invokes the
documented native Assist card action with `start_listening: true`, selecting
`EdSys Living Room Voice` by name. A temporary card inside the current Lovelace
tree is removed immediately; no stored dashboard configuration is changed.
The existing `EdSys Home` route must contain a native button/tile/shortcut card.
Frontend changes can require adjustment of this bridge.

HA on pve-node3 remains the conversation and device-control authority. Existing
Home Assistant Cloud handles STT/TTS; the native HA agent and en-US AmberNeural
voice are shared with the prepared Pi endpoint. Internet, existing Nabu Casa
remote access, HA and its Cloud subscription are dependencies. The preferred
household profile is retained. The 9950x, Docker Desktop and WSL are not added
to the speech path. No infrastructure commands or general AI agent are enabled.

## Deployment and private state

Run `install.ps1` in the ordinary signed-in Windows user session. On the first
installation pass the existing HTTPS HA address through `-HomeAssistantUrl`;
keep the real address private. Sign into HA in the new window using the owner's
normal account. Subsequent installs retain `settings.json` and `browser-profile`.
Exit the running client before updating its executable.

- App/source/cache: `%LOCALAPPDATA%\EdSys\NimoVoice`
- Private address and profile name: `settings.json`
- Local WebView2 authentication/session: `browser-profile`
- Generic health only: `status.json` (no speech text)
- Shortcuts: current user's Desktop and Startup folders
- Incoming ports/listeners: none; normal outbound HTTPS through Windows routing

The directory ACL admits only the current user, SYSTEM and Administrators.
Never copy browser-profile/session data into Git, RAG or the execution hub.
No HA access token is extracted or minted. Navigation and microphone permission
are restricted to the configured HTTPS origin; other device permissions are
denied. Microphone permission is not persisted. Native end-of-speech, dialog
close and app exit end capture. The client does not record audio/transcripts.

The installer pins Microsoft.Web.WebView2 **1.0.4258.31** and its NuGet SHA-256
in `webview2-package.json`, verifies all three runtime DLLs' Microsoft publisher
signatures, and compiles with existing .NET Framework's 64-bit C# compiler.
The existing Microsoft Edge WebView2 runtime is used without a global upgrade.
Windows `RegisterHotKey` owns Alt+V with no repeat, and a local mutex prevents
duplicate clients. A conflicting shortcut is reported rather than replaced.

## Verification and remaining acceptance

JavaScript syntax, PowerShell parse, pinned SDK digest/publisher signatures and
native C# compilation passed. OS-wide Alt+V opened Assist with the correct
profile; Windows UI reported microphone recording, and the owner heard the
reply to their live question. An app update retained sign-in and shortcut
registration. Invoking the native window's X while recording hid the window,
kept the process alive and removed Windows' microphone-recording indication;
reopening without another voice turn confirmed capture remained stopped.

For Wi-Fi acceptance, connect Wi-Fi normally, disconnect Ethernet, then use
Alt+V and a read-only query. Reconnect Ethernet and repeat. Do not change IP
addresses, install another listener, or disable adapters merely to configure
this client. Also check sleep/resume and next Windows sign-in. If voice fails,
check HA/Cloud connectivity, the selected Windows input/output, the tray process
and the native Assist profile. Retest the real spoken workflow after repairs.

## Recovery

Source and pinned SDK are rebuildable. Prefer signing into HA again over
exporting browser credentials. Keep any private settings backup outside Git/RAG.
For rollback, exit only Nimo Voice, remove its two shortcuts and preserve or
remove its private application directory as explicitly intended. No HA account,
integration, dashboard, Windows audio defaults or other application's shortcut
needs deletion. Disable the shared HA speech profile only after checking Pi use.

Architecture: EdSys-Master `docs/NIMO_HA_VOICE.md` and `docs/PI5_EDGE_LIVINGROOM.md`.
Native Assist action: https://www.home-assistant.io/voice_control/start_assist_from_dashboard/
