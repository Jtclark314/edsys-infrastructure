# Nimo Home Assistant voice

## OK Nabu preparation — October 18

The owner requested **prepare OK Nabu on Nimo** while live voice/playback tests
remain held. The candidate **0.4.0** is prepared alongside active **0.3.0**;
preparation never starts the candidate or opens audio capture. The active
executable, three existing scripts, settings, private sign-in profile, remote,
Alt+V, Startup shortcuts and process are preserved. HA/Pi configuration does not
need changing. See [the local detector](wake/README.md) for exact package/model
pins and protocol.

`prepare-wake.ps1` uses existing user-local Python 3.11 x64, creates an isolated
`wake-runtime`, installs only hashed official wheels, and checks three exact
upstream TFLite assets with Windows LiteRT and openWakeWord. There is no provider
key, inbound listener, cloud wake-audio stream, recording, transcript or new task.
The real detector's `--check` runs synthetic silence and enumerates input metadata;
it cannot open capture. The worker starts disarmed and accepts explicit local
parent commands. Two consecutive 80 ms scores at least 0.5 are an initial setting,
not a physically tuned or accepted wake threshold.

The candidate starts with wake mode **OFF**, including after logon/restart.
Enable it explicitly through **Nimo Voice tray → Enable OK Nabu (microphone)**.
Detection releases its input stream before invoking the existing native Assist
path; say **OK Nabu**, then wait for HA's listening indicator before the command.
The wake phrase is not forwarded as command audio. Existing HA speech/AI/reply
routing stays authoritative. Alt+V uses the same guarded handoff and remains the
fallback. Disable, lock/suspend and process exit retire the local listener.

Wake capture stays off while the native conversation, recorder, reply or camera
dialog is open. **Close the Assist dialog or Nimo window to rearm for another
wake**; automatic rearming on pipeline run-end is deliberately unsupported because
that event precedes reply completion. Closing cancels pending requests and late
capture; a fresh OK Nabu/Alt+V turn restores admission. Use those entry points
for a fresh conversation after cancel. Native page microphone actions outside
an admitted turn are denied while wake mode is enabled. The existing direct
native behavior remains when wake mode has never been enabled in that session.
This frontend bridge requires qualification after HA/WebView updates.

Preparation artifact: `%LOCALAPPDATA%\EdSys\NimoVoice\prepared-ok-nabu`.
The private receipt/activation manifest records exact baselines and candidate
hashes. Nothing in that directory is a running listener. After the owner releases
the hold, exit Nimo Voice and its TV remote normally, then run the reviewed
`activate-wake.ps1` from the matching staged source. It requires exact baselines,
backs up the executable/source and changed start/stop scripts, installs the
candidate and guards, and leaves it **OFF**. Launch the ordinary existing shortcut;
then enable the tray mode for explicit live acceptance. No profile is copied.

Verified preparation: **118 new offline checks** (26 worker, 31 capture,
16 idle, 23 cancellation, 22 native epoch) and **66 existing Phil/LG/keyboard
checks** passed. Native Windows compilation, PowerShell parse, dependency
consistency, all three model hashes and fifty synthetic-silence frames passed;
maximum silence score was below 0.001. Ten input entries were enumerated without
capture. Exact readback matched all sixteen staged source files, five candidate
artifacts and eight protected active files. The active 0.3.0 process/hotkey and
browser readiness were retained; Windows reported no microphone use. The final
0.4.0 candidate has not been launched or activated. These are preparation checks.

Remaining acceptance: actual microphone selection/16 kHz opening, nearby/far speech,
false wakes and TV sound, one reply only, command start timing, cancellation,
manual Alt+V, lock/sleep/logon/network recovery and the existing LG/Plex return
checks. None is established by synthetic/model/compilation checks. The owner's
live voice and playback hold remains in force. Rollback: exit both app modes,
restore only matching files from `recovery/before-ok-nabu-*`, and launch the old
client. Private runtime/models may remain inert; do not restore settings/profile
or overwrite later unrelated changes.


## Fresh frontend and one spoken check — October 17

Nimo Voice was exited through its actual tray **Exit** and launched normally
with the existing executable, sign-in/profile, current three scripts and Alt+V
binding. The fresh 0.3.0 client was signed in with browser/hotkey ready. HA's
current 17-file 0.6.1 package is loaded; Pi 0.7.1 remains installed.

The owner confirmed Phil animation, one LG/Polk reply and preserved already-paused
Plex after a real Alt+V time question. Native media identity/time remained exact.
Further tests were cancelled before playing-video acceptance. The voice window
was closed to stop capture; the client remains available in the tray. Phil/manual
overlays and reply audio were cleared, and Pi playback was left paused. Resume
testing only after a new owner request; no network, logon or edge-case acceptance
is implied by this one spoken check.

## Deployment checkpoint — October 16 evening

The corrected three-script priority package is installed on disk with exact
hash readback; the 0.3.0 executable, profile, Alt+V binding and existing process
were retained. All 66 targeted offline checks pass. An already-open WebView
can retain its old decorator, so installed scripts do not establish active
frontend behavior. Fresh initialization and spoken acceptance remain on the
owner's show-end hold. No desktop helper watchdog or Scheduled Task was changed.


Status, 2026-10-14: deployed native Windows client **0.3.0**. The owner pressed **Alt+V**,
asked “What time is it?” and confirmed hearing Home Assistant's spoken reply.
This acceptance used Ethernet. Wi-Fi handover, sleep and Windows logon recovery
remain to be confirmed; the client uses the same HTTPS hostname on either NIC.
The reviewed camera pilot displays HA's native camera dialog in this window;
the correct Front Porch feed and advancing video passed an authenticated typed
request. Microphone capture was released when the camera opened. Spoken camera
acceptance remains pending; an unattended microphone/typed-camera test showed
HA's native audio playback alert, which needs a real spoken acceptance check.

## Use and speech path

Press Alt+V from a normal desktop application, then speak when HA opens its
microphone. Replies use the eligible LG route below when the TV is already on
Pi HDMI 1; other starting TV states use Windows' selected output. The window's close
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
Home Assistant Cloud handles STT/TTS; the native HA agent and owner-selected
en-US AndrewNeural voice are shared with the prepared Pi endpoint. Internet, existing Nabu Casa
remote access, HA and its Cloud subscription are dependencies. The preferred
household profile is retained. The 9950x, Docker Desktop and WSL are not added
to the speech path. The existing HA conversational backend handles general chat,
web search and reviewed tools; it has no infrastructure-command authority.

## Native camera presentation

Ask to show a uniquely located camera, such as “Bring up the Front Porch camera
on the screen.” The shared profile sends only a typed response card for a
reviewed camera. `camera-view.js` attaches Nimo's registered client identity to
that profile's native pipeline subscription, validates the current turn/card,
and opens HA's authenticated native more-info dialog. The app distinguishes
advancing video, a visible image and an unverified failure. Camera frames are
not sent to the voice OpenAI backend; camera analysis remains disabled.

Superseded turns and duplicate cards are ignored. Pipeline subscriptions are
not resubmitted after reconnect, so a Wi-Fi/Ethernet handover or Core restart
cannot automatically replay a household command. Other HA subscriptions retain
their normal behavior. Close the Nimo Voice window to end native capture and
camera viewing. Camera presentation also releases the retained native Assist
recorder while retaining the native reply-audio lifecycle; Windows' recording
indication cleared during live camera viewing. This does not establish human
audibility. The unattended test's native audio alert remains a spoken acceptance
item. The bridge is qualified against Core/frontend 2026.10.1 and needs
requalification after frontend changes.

## Master Bedroom remote window

The **Master Bedroom TV Remote** desktop shortcut runs this executable with
`--remote`, using a separate mutex/window and generic `remote-status.json`.
It opens the existing HA Master Bedroom media page and shares the private HA
sign-in in place. It registers no global hotkey and denies microphone access;
ordinary Nimo Voice retains Alt+V and its existing Startup shortcut. Closing the
remote exits only that window. Launching it again restores the existing window.

Click the top **Keyboard remote** button to focus it. Arrows, Enter, Escape,
Home and digits then control the bound TV. Clicking the page releases input;
modified shortcuts remain with their normal controls. Held/concurrent keys are
not queued. The helper validates the exact route, native modal state, live
socket, TV availability, and private entity/device/config-entry/room binding.
Blur, changed route and expired preflight cannot dispatch a delayed command.
Service failures/timeouts are uncertain and not retried. Async JavaScript
results return over the existing same-origin message channel; WebView's script
execution method alone does not await JavaScript Promises.

The approved binding lives in private `lg-remote.json`; it contains no credential
and must remain outside Git/RAG. Deployment, layout and rollback are in
[LG remote](../home-assistant-lg-remote/README.md). Native C# compilation, script/
installer checks, fourteen offline keyboard guards and live no-actuation
keyboard/preflight probes passed. Production script/source digests matched
following probe removal. Desktop and phone-width layouts rendered correctly.
The owner confirmed Home/arrows/OK/Back navigation on the actual TV. This does
not renew the earlier voice/audio or network acceptance items.

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
The pilot deployment retained a private previous binary/scripts under
`recovery\pilot-20261013`; restore those in place after exiting only this client
to withdraw the new camera bridge while retaining its existing sign-in profile.

Architecture: EdSys-Master `docs/NIMO_HA_VOICE.md` and `docs/PI5_EDGE_LIVINGROOM.md`.
Native Assist action: https://www.home-assistant.io/voice_control/start_assist_from_dashboard/

## LG reply audio and guarded Plex return: 2026-10-15

Jeremy selected **LG replies: pause Plex during speech, then resume safely**.
HA/Pi software is installed and silently qualified. On October 15 evening,
Nimo's three-script update was installed in the existing Nimo Voice app. All three
installed and three recovery hashes match; executable/settings/LG keyboard
script, browser profile, running process and Alt+V registration were retained.
Jeremy is home and requested that live voice/Phil/audio/playback tests wait until
his show ends. Fresh frontend initialization is required; native frontend binding,
audibility and actual movie pause/resume remain pending. No live voice test,
HA/Pi restart, TV power/input or playback command was issued during installation.

Nimo keeps Alt+V and microphone capture. The same HA pipeline and Andrew voice
produce the reply. When LG is already awake on Pi HDMI 1, HA binds one native
STT-to-TTS subscription to the authenticated voice lease and consumes only that
pipeline's trusted TTS result. No client-supplied audio URL or provider key is
accepted. Bounded mono PCM travels encrypted through existing HA-only Kodi HTTP;
no new listener, package, provider or profile is added. TV-off/other-input turns
retain ordinary Nimo speech. Manual dashboard cards retain their existing behavior.

The Pi explicitly pauses only a playing internal video session, requires its
native pause acknowledgement, releases Kodi's HDMI sink and runs a supervised
fixed player. Phil speaks only after that player's ALSA device ownership and
advancing audio pointer are observed. Clean completion restores Kodi audio;
fresh HA permission/TV checks plus a one-use Pi lease can resume only the same
unchanged owned pause. Already-paused media stays paused. Manual pause/seek/stop,
source/power changes, dismissal, expiry, disconnect and uncertainty forfeit
resume. Existing Kodi volume/mute is respected without restoring old settings.

Nimo's completion proxy prevents duplicate local speech and delays native
follow-up listening until Pi playback finishes. A confirmed failure before a
speech commit may play the same generated reply once on Nimo; an attempted or
uncertain commit never triggers automatic fallback/replay. Household commands
are submitted only once. Native camera/device identity and reconnect guards remain.

Verification: 28 Pi tests, 73 isolated checks in installed HA Core, 18 existing
Nimo lifecycle checks, 14 LG-audio lifecycle checks and 14 keyboard checks pass.
Silent encrypted HA-to-Pi WAV tests observed real ALSA startup, local speaking
motion, clean release and cancellation. Kodi reacquired the HDMI device, no
reply player/WAV/recovery marker remained, and the exact prepaused item/time was
retained. All 601 Pi and 15 HA files match source. HA configuration passed and
one normal restart loaded handlers; all four pipeline definitions and protected
bindings matched. Live off-TV begin returned inactive. Kodi PID/profile,
firewall/listeners and sleep-timer Off baseline were retained; LG stayed off.
These checks do not establish spoken, active-movie or physical acceptance.

`start-assist.js` installs the optional decorator in `camera-view.js`;
`stop-assist.js` retires only its current voice lease. Only the selected pipeline's
STT turns participate. The native synchronous run-start Audio constructor is
substituted only during that callback, then immediately restored. Native capture,
intent, camera-card handling and device ID remain; no executable, SDK, sign-in,
hotkey, microphone or Windows audio setting changes are required.

Stage the reviewed three scripts and a private manifest mapping each filename
to exact before/after SHA256. `update-phil.ps1 -StagedPath` runs as the ordinary
Nimo user after reachability/local-pause review. It rejects unknown edits, backs
up the three scripts, checks source/installed hashes and retains the executable,
session and settings. Next Alt+V loads them. No delayed install/task is created.
Nimo had not received the earlier metadata-only stage. The installed update used
the original before hashes; all three installed and recovery hashes were verified.

To withdraw, compare and restore only the matching three-script recovery copy;
restart only Nimo Voice to discard the decorator. Preserve sign-in/settings.
Actual Nimo DOM/native Audio binding, spoken behavior and audibility remain
pending. The current frontend dependency must be requalified after HA updates.

## Pending-start lifecycle hardening: 2026-10-16 candidate

The Phil decorator now gives each start a generation. A superseded or cancelled
pending start cannot submit a native pipeline, replay a household command, or
construct reply Audio from a late event. A late begin response cancels only its
own never-bound session; it cannot dismiss the newer turn. Begin timeout still
uses ordinary Nimo speech once, and the late optional Pi lease is retired.
Stop, disconnect, page hide and unload remove the decorator's owned timers and
connection/window/Audio listeners. A late subscription failure cannot cancel
the current generation. Loss of an optional Nimo display lease leaves that
current native capture/intent/TTS subscription running; a retired Pi speech
lease still fails closed. Native capture, profile, sign-in and Alt+V stay owned
by the existing app and Home Assistant.

Source-only verification uses fake HA, Audio and timers; no real voice or media
is exercised:

```sh
node --check services/nimo-voice/camera-view.js
node services/nimo-voice/test-phil.cjs
node services/nimo-voice/test-phil-lg.cjs
node services/nimo-voice/test-phil-start.cjs
```

The new start suite covers both reply orders, cancellation while begin is
pending, same-tick supersession, cancellation at lease handoff, timeout then a
late lease, connection replacement, late native callbacks/failures, in-flight
RPC retirement and listener cleanup. Deployment uses the existing reviewed
three-script hash/backup procedure. Candidate checks do not release the owner's
hold on actual frontend, microphone, spoken reply or playback acceptance.
