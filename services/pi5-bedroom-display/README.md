# Pi5 Master Bedroom display pilot

Native Kodi 21.3/GBM on the existing Pi5, with dimensional Phil animations and
bounded presentation cards above Kodi's GUI/video. Home Assistant remains
household authority. The owner selected existing Nimo voice for initial control;
its microphone stays on Nimo. The selected LG reply route is described below. Pi hands-free voice
is deferred until its physical microphone/speaker gate and acceptance pass.

## Verified scope

On 2026-10-14 the owner confirmed Kodi on LG physical HDMI 1, smooth 1080p H.264
test video, the butler over Kodi home, and video and butler visible together.
Output is 1920×1080 at 60 Hz. A ten-second simultaneous smoke check used about
0.30 CPU core, 329 MiB RSS, 35.3 °C and no throttling flags. This is not sustained
Plex/codec/subtitle qualification or a 24-hour soak. PM4K for Plex 1.0.6 is
installed from Kodi's official Omega repository; the owner completed account linking, and the library survived a controlled Pi
reboot. An owner-selected 1080p HEVC episode resumed its existing progress; the
owner confirmed smooth video, audible audio and the butler over it. Subtitle/
additional-codec and sustained combined qualification remain separate.

The 13 synthetic scenes exercise voice states, correlated speaking animation,
camera-unavailable, morning/bedtime, answers, notifications, media, ambient,
choices and failures. They contain no household data. A separate manual dashboard path now renders
local camera previews and source-backed cards, as described below. Automatic
voice integration is implemented below, with Nimo installation and spoken acceptance
pending. `PlaybackLease` guards the narrow HDMI reply handoff; the broader
presentation contract remains unpromoted. Kodi screenshots can omit the DRM video plane;
physical combined playback confirmation is recorded separately.

## Ownership and interface

The HA chat owns `services/home-assistant-voice`, dashboards and the shared
presentation contract. This track accepted proposal v1 at SHA-256
`b77dd77883e0a0cd412cf0d99fae037f3edb310837ebcbb19f09d9b53e6bfa24` after
manifest and fixture qualification. `contract_adapter.py` consumes an injected
HA-owned receiver. No schema is copied here or production transport implied.
Hidden/expired/cancelled/disconnected presentations clear. Audio generation does
not authorize mouth animation: exact local playback correlation is required.
Nimo playback is not evidence of Pi audio playback.

`local_control.py` is an operator qualification tool, not the shared wire
protocol or a user-facing remote. It accepts fixed synthetic scenes, bounded
duration, fixed navigation keys, a fixed generated clip, screenshots, and fixed
PM4K install/open operations. No arbitrary commands or URLs. The mode-0600 Unix
socket is inside a mode-0700 runtime directory; peer UID must be root or the
service owner. Requests are bounded, queued with deadlines, deduplicated
in-process and never automatically replayed after uncertainty.

## Host, ports and state

The owner-requested **Media → Bedroom Pi Plex** dashboard now uses HA's native
Kodi integration for this player. Standard Kodi HTTP on 8080 is authenticated
with a generated endpoint-only credential and admitted only from HA on eth0.
WebSocket/TCP 9090 and discovery remain disabled; state uses HTTP polling.
This connection controls playback/navigation and is separate from the pending
presentation-event transport. See `../home-assistant-pi-plex/README.md` for the
page, setup, privacy, verification and narrow rollback.

- Host: existing `edsys-edge-livingroom`, physically in Master Bedroom; hostname
  retained. Runtime: Debian 13 ARM64, native systemd Kodi/GBM, no container.
- User: existing unprivileged `edsys-voice`, also owning the retained PipeWire
  session. Kodi uses explicit ALSA HDMI stereo; eventual USB voice is separate.
- Unit: `/etc/systemd/system/edsys-bedroom-display.service`, dedicated VT 7.
- Profile: `/var/lib/edsys-voice/.kodi` (0700), including private Plex account
  state after linking. Cache: `/var/cache/edsys-bedroom-display`.
- Bridge/captures: `/run/edsys-bedroom-display`, deleted at service stop.
- Add-on: `/usr/share/kodi/addons/service.edsys.bedroom-display` and one optional
  system add-on manifest entry. No direct Kodi database writes.
- The base installer disables Kodi HTTP/event/UPnP/AirPlay/zeroconf and
  WS-Discovery. The separate `pi/enable-ha-control.py` opts into the HA-only
  authenticated HTTP connection above. No HA credential is installed. Recheck listeners after
  add-on changes; future PM4K versions need separate qualification.
- CEC devices are blocked and native CEC defaults disabled. No TV wake/off or
  LG-remote-to-Kodi navigation is qualified.

TV HDMI 1 is currently labeled **PC**. The repaired HA **HDMI 1 · Pi** button
addresses physical `HDMI_1`, independent of that label. HDMI 2/ARC was not
changed. LG native Plex remains an independent fallback.

## Build, install and operate

Fresh-profile installation only; inspect before applying. Run root commands on
the Pi via established SSH. The installer refuses an existing profile/unit and
makes a private pre-install checkpoint. It does not upgrade the OS, edit boot
configuration, start or enable Kodi.

```bash
python3 services/pi5-bedroom-display/tools/build_kodi.py PRIVATE_ADDON_ZIP --animation-dir REVIEWED_REMOTION_OUTPUT
# Copy source pi/ and ZIP privately to the reviewed Pi.
sudo bash PRIVATE_SOURCE/pi/install-kodi.sh --apply
sudo python3 PRIVATE_SOURCE/pi/install-addon.py PRIVATE_ADDON_ZIP
sudo systemctl start edsys-bedroom-display
sudo python3 /usr/share/kodi/addons/service.edsys.bedroom-display/local_control.py status
sudo python3 /usr/share/kodi/addons/service.edsys.bedroom-display/local_control.py scene listening --duration-seconds 30
sudo python3 /usr/share/kodi/addons/service.edsys.bedroom-display/local_control.py dismiss
```

The unit is enabled; a controlled reboot restored Kodi at 1080p60 and retained
Plex sign-in. Voice and boot files matched their private pre-install baseline;
only SSH listened after reboot. The unit starts only with a connected HDMI
sink, does not restart after a crash,
and has no hotplug watcher. Boot enablement and an actual reboot are distinct
checks. If the TV was disconnected at boot, start explicitly after connecting
it. Never replace a running movie for a test. `play_test` refuses existing media;
`stop_test` stops only the fixed qualification clip. The generated private clip
is not packaged or published.

Native `update_repos`, `install_plex` and `open_plex` target only `script.plexmod`.
Installation may need confirmation in Kodi's native dialog. Complete account
linking on the TV and a personal device; do not export the linking code or
profile. PM4K is a client of the existing Plex server, not a new server/transcoder.

For a reviewed add-on-only update, disable only this add-on through native
`Addons.SetAddonEnabled`, run `pi/update-addon.py` against the exact ZIP, then
re-enable it. The updater backs up the owned files and records the profile hash;
Kodi/player keep running. It refuses while the owned control socket is active.
For unit/profile changes, stop the unit and preserve the linked private profile
first. The fresh installer refuses overwrite. Kodi upgrades can replace the system
manifest/CEC defaults; compare and reapply only the optional add-on entry/CEC
policy while preserving new vendor definitions.

## Verification and recovery

```bash
python3 -m unittest discover -s services/pi5-bedroom-display/tests
python3 services/pi5-bedroom-display/tools/check_contract.py --contract-dir HA_PROPOSAL_DIR
python3 services/pi5-bedroom-display/demo_server.py
# Synthetic browser preview only: http://127.0.0.1:8766
```

Runtime checks: unit health, exact 1080p/HDMI audio settings, Unix socket ownership,
listeners, expiry/dismissal, advancing video plus overlay, temperature and power
flags. Capture helper requests one frame and waits for that exact complete PNG;
write outside source and never capture linking codes. Config/process health is
not physical acceptance. Qualify real Plex direct play, resume/watch state,
subtitles, audio and longer simultaneous operation before promotion. Automatic voice-event acceptance follows the HA owner's implementation;
manual camera/dashboard acceptance is recorded below.

Recovery: stop/disable `edsys-bedroom-display` independently; switch LG to native
Plex. Private `/var/backups/edsys-bedroom-display` checkpoints hold original
voice/boot files, package/manual lists, Kodi profile, system manifest and CEC
definitions. Preserve subsequently linked profiles before restoring. Remove
only the owned unit/add-on/manifest entry and restore only the owned CEC change,
preserving intervening changes. Review saved package deltas before removing
new packages; never remove existing voice dependencies or blindly autoremove.
Voice configuration remains unchanged. No backups, logs, private bindings,
media or account state belong in Git/RAG.

Owner: Jeremy. Architecture: EdSys-Master `docs/PI5_EDGE_LIVINGROOM.md`.
References: [Kodi service add-ons](https://kodi.wiki/view/Service_add-ons),
[PM4K](https://github.com/pannal/plex-for-kodi).

## Manual Bedroom Pi dashboard: 2026-10-14

The sidebar **Bedroom Pi** dashboard at `/edsys-pi/player` adds manual butler,
refreshing camera popup/large preview, media details, clock, dark screen, local
Family/Home/Reminders briefing, bedroom overview and connection cards. Jeremy
confirmed Front Porch updating above a playing Plex episode. The actual HA
dashboard Dismiss button closed it and removed its private frame files. Large
Back Patio preview refreshed, expired and removed its images. Every card returned
a renderer acknowledgement; the clock layout was visually checked. This does
not establish full-motion video, spoken screen commands or a combined soak.

`manual_protocol.py` is the single source for this closed manual protocol and is
bundled into the HA component. It does not implement or promote the separate
HA-owned automatic voice-event proposal. Requests carry UUIDs, sequence, issued
time and bounded expiry; dismissed/replaced frames cannot reopen a view. A
stalled preview closes after six seconds. No player, audio, URL or OS command is
accepted. TV must already be awake on physical HDMI 1. No implicit TV wake/input
change occurs. Plex play/pause/position are never changed by screen actions.

Messages use the existing authenticated HA-only Kodi HTTP client and AES-256-GCM
content encryption. This prevents PM4K's notification logger from retaining
plaintext images or household records; it does not make HTTP Basic TLS. An
endpoint-only display key is generated by `configure_screen.py`, stored only
in HA's private screen config and `/var/lib/edsys-voice/bedroom-screen-key`
(0600, service owner), never in Git/RAG or command arguments. It is not an HA,
Plex or camera credential. Debian's existing `python3-cryptography` is required.
Kodi's embedded subinterpreter cannot import its Rust module, so the fixed
`decrypt_screen.py` helper decrypts through private parent/child pipes with a
two-second timeout, no arguments/listener/logging. Missing keys fail closed.

Kodi HTTP JSON-RPC has a 65,536-byte body limit. HA removes metadata, resizes and
re-encodes frames to at most 32,000 JPEG bytes; the encrypted envelope remains
under 62,000 bytes. Refresh requests are about one second apart plus capture/
transport time. Camera files rotate across three private runtime JPEGs and are
deleted on close, expiry or add-on restart. Restarts discard views; no replay.
Only original code/assets belong in the closed add-on ZIP. Private camera data,
calendar text, Plex URLs/logs and account state must stay outside model prompts,
Git and RAG. Recovery: disable the owned add-on, compare current files and restore
only its matching private `manual-screen-*` backup, preserving the linked profile
and current movie. Remove the new screen service/dashboard separately if needed.

## Original artwork mode

`tools/build_art.py` generates four original geometric PNG presets, bundled in
the add-on. Art is a closed manual layout with a numeric preset index;
it accepts no path, URL or external image. It rotates every 20 seconds, hides its
initial hint, supports Next/Back/Return and expires within two hours. Other
manual views retain their five-minute maximum. Art is explicitly requested,
never an automatic idle takeover, and it never changes media/audio playback.
Personal photo folders remain future selected input. The HA Night & art tab
controls this mode; its sleep timer and native subtitle/audio settings are
separate from the renderer and the pending automatic voice-event contract.

## Dimensional Phil v1 animations: 2026-10-15

Jeremy selected dimensional Phil design B. Its existing identity/outfit is
retained in six four-second, 12 fps loops: idle (breathing/blink), listening,
thinking, speaking (mouth/hand), greeting (wave) and error. The original source
poses are 1024×1536; native playback uses 288 transparent 512×768 PNG frames.
Remotion renders on 9950x, and the Pi uses its existing Kodi image layer/cache.
No browser, second video decoder, new package, network listener or HA restart
is required on the Pi. Character art does not itself prove a running voice flow.

Manual **Show butler** greets once then idles; other manual household/media cards
idle without speaking. Native fixture scenes use the matching listening/thinking/
speaking/error states. Speaking still requires responding plus the existing
audio-playing flag; fixture speech is explicitly simulated/silent. Actual Nimo
audio correlation and automatic presentation routing remain HA-owned work.

See [Remotion build source and provenance](animation/README.md). Original source
poses and rendered media are protected project assets outside Git/RAG; the code,
exact dependency lock, prompts and source hashes are tracked. Build requires the
full reviewed output manifest and rejects missing/changed frames. The 310-file
ZIP includes the existing 21-file manual/art foundation, `character.py` and 288
fixed local frames. Fresh/updating installers reject extra/duplicate/oversized
entries and wrong frame dimensions. Legacy simple assets remain only for the
older browser qualification surface/recovery; Kodi now uses dimensional Phil.

Verification: 17 Pi tests pass, including manual greeting-to-idle, frame bounds,
no repeated same-frame loads and audio-state gating. Aligned Remotion/TypeScript,
full six-state render and a color-verified 24-second NVENC review passed. A mesh
seam found in visual QA was corrected before installation. Actual native GUI
capture shows dimensional Phil; five fixture states advanced 13–14 frames in
1.1-second samples. A 7.6-second paused-playback smoke averaged about 0.30 CPU
core; this is not active-playback performance or sustained acceptance. All 310
deployed file hashes match the package. Kodi PID, profile, paused item/position,
listener and firewall baselines were retained; operator overlays were dismissed.
The LG was off, so physical TV/combined-playback acceptance remains pending.

Recovery: disable only this add-on, compare the current owned files with the
reviewed deployment, and restore the prior `manual-screen-*` add-on backup.
When reverting v1 to the simple butler, remove only v1's added `character.py`/frame directory;
preserve linked Plex/profile and any subsequent user changes. Native LG Plex
remains an independent recovery path. Do not restart Kodi or restore a whole
profile merely to roll back character artwork.

## Frontal, expressive Phil v2: 2026-10-15

Jeremy requested stronger motion and a more front-facing pose after reviewing
v1. Matching frontal artwork now keeps level eyes and shoulders toward the
viewer. Larger waves, listening nods, thinking tilts and speaking gestures run
at 24 fps: six four-second loops, 96 frames each, 576 transparent 512×768 PNGs.
The raised-arm mask was narrowed after visual QA found hair following the wave.
The review is 24 seconds at 1080p/24 fps, NVENC H.264 with BT.709 limited range.
This remains an illustration puppet; actual reply-audio correlation is pending.

Add-on 0.5.0 has 598 closed files. The frame scheduler checks every 25 ms; no
new Pi runtime/dependency/listener is introduced. Seventeen Pi tests and aligned
Remotion/TypeScript checks pass. All deployed hashes match; native GUI capture
shows frontal Phil and all five synthetic state samples advanced 27 frames in
1.1 seconds. The 7.4-second paused-item smoke averaged 0.41 CPU core and about
128 MiB Kodi RSS. It is not an active-playback or sustained-load result.

Kodi PID, profile, exact paused item/position, firewall and listeners matched
before/after. Operator overlays were dismissed. LG remained off; no playback
action, TV wake, Kodi/HA restart or pipeline/provider change occurred. Owner
review of this revised motion and physical/combined acceptance remain pending.

Recovery from v2 to v1: disable only the owned add-on; compare current owned
files against this deployment and the private prior add-on backup. Restore the
backup's files and remove only v2 frame names 048–095 for each of the six states
that are absent from that backup. Retain v1's `character.py` and frames 000–047.
Preserve the linked profile and later user changes; do not restore a whole
profile or restart Kodi to revert artwork.

## Earlier metadata-only revision: 2026-10-15

This 0.6.0 historical revision is superseded by the LG reply implementation below.

Add-on 0.6.0 has 599 closed files, retaining the accepted frontal 24 fps artwork.
`voice_display.py` adds a transparent Phil/short-status overlay independent of
manual camera/art cards. The encrypted manual transport now accepts only the
closed `voice` state/audio-playing fields. No text, URL, TTS or player command
is added. Nimo/HA own the real capture/playback correlation and user/TV gate.
The renderer never pauses/resumes media, changes volume or wakes/switches LG.
Manual-card avatars hide while the voice avatar is visible, then return.

A turn must begin with a fresh listening/sequence-zero event. Dismissal,
replacement, seven-second heartbeat expiry and add-on restart prevent older
heartbeats from reopening it. Back closes the overlay; HA observes that closure
and forfeits that turn. A lost connection needs a new user-started voice turn.
Speaking requires responding plus the correlated native-audio-playing flag;
generation or a reply-ready event alone leaves the mouth idle. Buffering stops
speech motion. Actual microphone/audio stay on Nimo for this revision.

Nineteen Pi tests, 18 Nimo lifecycle checks and 66 native HA checks pass. Silent
HA-to-Pi encrypted HTTP fixtures advanced listening/thinking/idle/speaking/error
by 22–27 updates per 1.1-second sample; simulated audio was explicitly a fixture.
Each pose and final dismissal were acknowledged, with the exact paused item and
position retained. The first hub-round-trip harness correctly expired its slow
heartbeat; moving the fixed fixture inside HA qualified normal transport timing.
LG stayed off. This is software transport/rendering evidence, not spoken Nimo,
physical display or audio acceptance. Nimo is offline and its update is staged.

To withdraw this addition, compare owned hashes, disable only this add-on and
restore its prior v2 backup, removing only added `voice_display.py`. Retain all
576 v2 artwork frames, character.py, linked Plex/profile and later user changes.
Restore the prior HA component/private binding and Nimo scripts separately using
their runbooks. Do not deploy an older 21-file/310-file animation package.

## LG reply audio and guarded Plex return: 2026-10-15

Jeremy selected **LG replies: pause Plex during speech, then resume safely**.
HA/Pi software is installed and silently qualified. Nimo remains offline; its
three-script update is staged, not installed. Spoken Nimo, actual movie pause/
resume and physical LG/soundbar acceptance wait until Nimo is reachable and
Jeremy is home. No audible speech or TV wake/input change was tested remotely.

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

Add-on 0.7.0 has 601 closed files and retains all 576 accepted frontal 24 fps
frames. `reply_audio.py` owns the bounded handoff; `reply_player.py` has no
network access and accepts only a private owned runtime WAV. Parent-pipe EOF,
heartbeat loss, cancellation or a 63-second ceiling terminates the player.
A private runtime marker lets add-on restart restore Kodi's audio engine, never
resume an old movie. Native player notifications invalidate the lease; notification
queue overflow forfeits recovery. PCM is 24 kHz/mono/16-bit, at most 60 seconds.
The encrypted closed protocol accepts only sequential, hash-verified chunks
bound to an already-visible current voice owner. No URL/executable command is
accepted. Audio-engine suspend waits for the asynchronous ALSA sink release
before launching aplay. Hardware progress parsing handles padded ALSA keys.

To withdraw this addition, disable only the add-on, compare current hashes and
restore the matching prior 599-file backup, removing only `reply_audio.py` and
`reply_player.py`. Retain v2 frames, character.py, linked Plex/profile and later
user changes; enable the add-on without restarting Kodi. Restore HA's matching
14-file component/output binding and Nimo scripts separately as their runbooks
specify. Do not restore an entire profile/VM or older art-only package.
