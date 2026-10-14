# Pi5 Master Bedroom display pilot

Native Kodi 21.3/GBM on the existing Pi5, with an original animated butler and
bounded presentation cards above Kodi's GUI/video. Home Assistant remains
household authority. The owner selected existing Nimo voice for initial control;
its microphone, Andrew voice and reply output stay on Nimo. Pi hands-free voice
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
choices and failures. They contain no household data. Live camera frames,
briefing/data adapters, media ownership and a Nimo-to-HA-to-Pi transport are not
connected. `PlaybackLease` is a pure ownership-policy prototype, not an enabled
pause/resume controller. Kodi screenshots can omit the DRM video plane;
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
python3 services/pi5-bedroom-display/tools/build_kodi.py PRIVATE_ADDON_ZIP
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

For source updates, stop the unit, back up its private profile and exact owned
files, compare deployed hashes, then replace only reviewed add-on files. The
fresh installer refuses overwrite. Kodi upgrades can replace the system
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
subtitles, audio and longer simultaneous operation before promotion. Live HA
transport/camera/data acceptance follows the HA owner's implementation.

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
