# Pi5 Master Bedroom display pilot

## YouTube authorization recovery and native dashboard — October 20, 2026

**Later owner re-login: partial authorization recovered.** After repeated
device approvals, the profile initially held no completed tokens, then saved TV
and personal pairs while **VR remained missing**. An in-flight prompt returned
`authorization_pending`; Google success alone did not establish Kodi completion.
The two working pairs were preserved, and one bounded native Sign In action
resumed only the missing VR approval. Its fresh on-TV code was transferred
privately to Nimo's Google device page; the intended account and existing
YouTube-only permission were verified. Google completed the VR consent and Kodi
saved all three access/refresh pairs. Token introspection accepted all three with
the existing YouTube scope. Native root now returns twenty entries, Sign Out and
**no Sign In**; Jeremy independently confirmed it stays signed in. Current video
continued and was returned to full screen without restart, seek or volume change.
Before/partial/after private account checkpoints are verified on-Pi and off-Pi.

For future diagnosis, **Sign Out alone is insufficient**: this add-on can show it
while partially signed in. Check all three TV/personal/VR pairs plus the absence
of Sign In and owner acceptance. Three distinct approvals can be normal; do not
reset/revoke the good pairs merely because another client is incomplete. The
native action's HTTP caller can time out while the device flow continues: inspect
the dialog/token state and do not replay it. **Default** is a local arbitrary
profile label, not a Google identity result. YouTube-only token metadata exposed
no email/subject, so it cannot independently prove matching identities across
all three clients. No additional identity scope or channel creation was used.

Earlier in the session, an observed **401 / authError / Invalid Credentials** cleared during
native account/browsing recovery. The existing personal Data API request passed;
all three saved TV, personal and VR refresh grants returned valid access tokens.
Native root browsing returned nineteen items including Sign Out, with no Sign In
or new authorization error. No key/client replacement, grant revocation, new
Google consent, add-on update or restart was required for that earlier recovery. The original failure's
trigger is unproven; do not attribute it to seven-day Testing expiry when the
existing grants still refresh. The October 19 console-state observation below
was not re-audited in this recovery.

The previously accepted real 4K source passed a guarded thirty-second run:
28 samples of **3840×1608 VP9** with **3840×2160 at 23.976–24 Hz**, no new
authorization, stream-proxy or large audio-sync failure and no throttling.
Startup was muted; low-volume audio followed twenty clean seconds. After the
asynchronous Stop settled, actual idle and restored normal volume/mute were
verified. The owner then confirmed the error cleared and subsequent YouTube
picture/sound were smooth without a warning. A different owner-selected video
began later; further control tests stopped and that playback was left running.

The native **Bedroom Pi → YouTube** tab is `/edsys-pi/youtube`. It adds one
Player link and one view to the latest dashboard, retaining all seven existing
views, metadata, resources and the separate Media dashboard. It provides normal
YouTube root/Search/New uploads/Subscribed channels/Playlists/Recommendations
folders, Pi transport/navigation/stream details and independent LG HDMI 1,
volume, wake and full-remote links. Search opens the TV's native search list and
keyboard. Account, Watch later and History use the current add-on's root menu;
their remote/local playlist mappings are not guessed. These are controls for
the TV's add-on, not an embedded signed-in YouTube website or feed on Nimo.

Source: `youtube_dashboard.py` exports pure `extend` and `remove` transforms.
Supply the latest private dashboard and reviewed distinct Kodi/LG entities;
preserve private before/candidate files, validate registry identities, compare
the current dashboard again immediately before save, and require exact native
readback. Never rebuild the older full dashboard as an update. Seven focused
tests cover preservation, idempotence, fixed routes/targets, independent
availability guards, ownership-conflict refusal and narrow recovery. HA/Kodi
service/schema review found no payload blocker. A native service response is
not physical acceptance of every control.

Actual signed-in Nimo Chrome rendered the dashboard and its Open YouTube click
reached the native root. Search keyboard, each subscription/playlist route,
every transport/TV button and physical phone layout remain unqualified; the
owner started watching before further tests. Page loading sends no action.
Pi connection status does not establish Google authorization. No new listener,
frontend resource, integration or automation was added.

Recovery material is private on the Pi under
`/var/backups/edsys-bedroom-display/youtube-20261020/`, with a verified off-Pi
copy on 9950x. All eighteen protected archive files passed hash verification.
YouTube/ISA settings, API configuration and the exact October 19 source patch
remain unchanged; all native setting values are preserved, while normal
volume/mute are confirmed both live and persisted. Kodi's PID is unchanged.
This was an archive/hash check, not a full application restore or a new verified
offsite copy. No credential overwrite occurred, so there is no auth rollback to
apply. To remove the dashboard, run `remove` against the latest configuration;
it refuses modified owned cards and preserves unrelated later edits. Save only
after a fresh comparison and verify the exact result. Never restore an old
entire profile or watch position over current use.

## YouTube Premium setup and streaming repair — October 19, 2026

Official YouTube **7.4.4** and InputStream Adaptive **21.5.9** are configured.
The owner completed all three Google authorizations for the Premium account;
native browsing shows **Sign Out** and **Subscriptions**. This establishes
signed-in access, not parity with every official-app Premium benefit.

The saved quality default is **up to 2160p, SDR, at most 30 fps**, with
MPEG-DASH/InputStream Adaptive and automatic selection. AVC and VP9 are enabled;
AV1, HDR and high-frame-rate selection are unselected. ISA's
`overrides.ignore.screen.res=true` allows playback above the retained 1080p60
GUI. YouTube is the only currently identified ISA consumer; review this global
ISA option before adding another consumer. Existing HDMI whitelist, stereo PCM,
Plex Original quality and other Kodi decoder/render settings were retained.

The first VP9 trials failed physical acceptance: the owner reported a frozen
picture/noise. Both had actual **ANDROID_VR CDN HTTP 403** failures; the 4K run
also accumulated large audio-sync errors despite advancing player clocks and
zero throttling. Player time and selected resolution alone did not prove clean
playback. Kodi was immediately muted/stopped. A repeated sign-in action modal
was cleared during recovery; use the normal YouTube root/menu for account work,
not a persistent browser folder at the sign-in action URI.

A narrowly reviewed [upstream PR1482](https://github.com/anxdpanic/plugin.video.youtube/pull/1482)
workaround adds only `_use_adaptive: False` to AndroidVR's profile. Authentication
and history code remain intact; adaptive media use an existing alternative
client. This is a **local one-line patch, not an official released fix**. The
[patch artifact](patches/youtube-7.4.4-android-vr-adaptive.patch) applies only to
`resources/lib/youtube_plugin/youtube/client/request_client.py` in stock 7.4.4.
Stock SHA256 is `816df34a46408456e4f3e95a24c6f4d8c3d267dfd737dfba9a2a3194c42aec0e`;
patched SHA256 is `5f402c889b49c788f287b7a436c85fd2a33de7195ef79dedbba48f54065df029`.
Both files parse, and exact forward/reverse application was verified. Reassess
on an add-on update; never apply blindly to a different version/hash.

After the patch, a two-minute AVC recovery check produced 58 samples of
1920×804 H.264 at 24 fps with 1080p24 output, no new proxy/audio-sync failures
and zero throttling. Actual selected video/audio resources used **ANDROID**.
The owner confirmed moving picture and clean sound. Two startup PRIME filter
warnings remained, so those warnings alone are not a failure indicator.

**Guarded 4K retest accepted.** After the repair, the two-minute run produced
58 native samples of **3840×1608 VP9 SDR** and **3840×2160/23.976–24 Hz HDMI**.
Both video and AAC audio resources used ANDROID; no CDN 403/proxy failures,
large audio-sync errors, stalls or invalid decoder data occurred during playback.
Temperatures were 35.1–39.5 °C with zero throttling. The owner confirmed smooth
moving picture, clean sound and no covering sign-in box. Sound started muted,
then used volume 20 only after 20 clean seconds. This qualifies one bounded
network source; it does not qualify HDR, AV1, 4K60 or every future upload.

Normal YouTube root browsing returned with no active player or blocking dialog;
GUI is 1080p60 and normal Kodi volume 100/unmuted is restored. A video-output
timeout occurred only after the deliberate stop, during teardown. Startup
PRIME warnings remain; do not represent the entire log as error-free.

The dedicated Google project/key is separate from Maps. The API key is
restricted to YouTube Data API v3 and passed a live API read. OAuth uses a TV
limited-input client. The owner accepted Google's policy and consent screens.
The personal app remains **External / Testing**: [Google's testing-token
rules](https://developers.google.com/identity/protocols/oauth2#expiration) can
require reauthorization after seven days. Production setup is not completed;
Google currently requires further public branding URLs. No new Maps billing,
quota or key restriction was changed.

YouTube's helper remains **127.0.0.1:50152**, with its API configuration page
**disabled**. Credential profiles are owner-only; keys, tokens, codes, raw URLs,
logs and account receipts stay outside Git/Obsidian/RAG. Temporary transferred
activation-code files were removed. An unused credential was replaced before
account linking. A scoped Kodi restart after the earlier manual installation
loaded its intact English language table and removed untranslated-ID labels;
no add-on language/source translation patch was needed.

For rollback, first stop playback and capture fresh owner activity. With Kodi
stopped, restore only the reviewed stock source file or reverse this patch
against the exact expected hash; preserve current account/profile state. A
protected known-good 1080p AVC setting snapshot exists for narrow rollback of
YouTube's quality/features and ISA's ignore-screen option. Do not restore an
old entire profile or stale watch position. The fresh switch snapshot in this
session already had no active media, so recovery preserves that stopped state.
The separately deferred Plex remembered-version selections remain deferred.

## Native remembered 4K choices selected; live setup held — October 19, 2026

The owner chose PM4K's native per-movie **Choose Version** memory rather than a
custom client policy. Local, remote and online quality already default to
**Original** (index 16); Direct Play, remux and 4K are enabled by installed
defaults. No setting override or client patch was required for those defaults.
Native preplay can choose the first Media entry or a remembered version;
its cached choice can bypass the media decision engine's resolution ranking.
There is no verified native global "always prefer 4K" setting.

One unmatched UHD copy was verified against its matched 1080p counterpart by
audio-content comparison, then merged into the existing matched movie using
Plex's native API. Readback confirmed both exact source parts, unchanged source
file size/mtime/inode and retained canonical viewing-history fields. Search
now returns one item with both versions, with UHD first. No other duplicates,
editions or unmatched items were merged. Private identities/receipts remain
outside Git/RAG. Use [Plex Merge/Split](https://support.plex.tv/articles/201018248-merge-or-split-items/)
for narrow recovery; preserve the canonical item and later watch progress.

**Remembered 4K selections are not yet saved.** The owner asked to wait until
watching finishes. A Back action during investigation stopped playback;
subsequent readback found the same episode playing with retained audio/subtitles.
No claim of uninterrupted playback or operator-restored position is made.
PM4K Back can stop its player: do not navigate the live player to configure
versions during this hold. No background watcher or scheduled task was added.

After the owner releases the hold, use each confirmed mixed-resolution movie's
native preplay **Choose Version → 4K**. Verify its stored `media_version`
preference and fresh native preplay readback without starting a movie unless
requested. Keep Original quality. A remembered choice is specific to this
PM4K profile/movie/source ID; new multi-version titles and replaced source IDs
may require another choice. Do not promise a universal future-title preference.

## Real Plex Original-quality UHD accepted — October 19, 2026

The owner authorized a genuine library movie test at Original quality followed
by return to the current show. The selected source was independently probed as
**3840×1600 HEVC Main10, 23.976 fps, BT.709 SDR**, about **6.4 Mb/s**, with AAC
5.1. Its cropped cinema frame was presented within **3840×2160 at 23.976 Hz**
HDMI output. This was the native PM4K network path, not the local test fixture.

The 45-second run produced **22 native samples** of that exact source and UHD
output, corroborated by active DRM CRTC readback. **24 authenticated Plex
samples** identified PM4K on Raspberry Pi, the same source part and
**`directplay`**, with **no transcode sessions**. Native source URLs matched
the original library part rather than a transcode resource. Temperatures were
36.2–38.4 °C, with `throttled=0x0` throughout. The owner confirmed that this
verified UHD run had **smooth picture and sound**.

Plex search contained two identically titled/year entries with different source
resolutions. The first was 1080p and was excluded from the UHD result. For
future tests, verify the preplay **4K** badge, actual source dimensions and
part/file identity; title and year alone cannot select a specific version.

The automated return stopped at a UI guard; subsequent native UI readback
confirmed the exact original show playing near its captured position, with
unchanged audio and subtitle selections. This run does not establish automatic
exact-position restoration. Do not seek again after the owner resumes playback.
No new quality/display setting or library configuration change was required.
Raw URLs, account state, movie/show identities, watch progress and test receipts
remain in protected private checkpoints, outside Git/RAG.

This qualifies one bounded SDR Original/Direct Play case. Sustained playback,
high-bitrate/remux sources, reliable dropped-frame counts, HDR and 4K60 remain
separate. Earlier dated sections below retain their original acceptance scope.

## 4K movie output enabled — October 19, 2026

The owner authorized 4K output and a 30-second synthetic test followed by
restoration of the current show. Native Kodi settings now select
`videoplayer.adjustrefreshrate=2` (**On start/stop**) and a 13-mode whitelist:
progressive **1920×1080** at 23.976, 24, 25, 29.97, 30, 50, 59.94 and 60 Hz;
**3840×2160** at 23.976, 24, 25, 29.97 and 30 Hz. All values came from this
connected LG's actual `Settings.GetSettings` options. GUI remains 1080p60;
playback can switch to the matching whitelisted resolution/frame rate.
UHD50/59.94/60, 4096-wide modes, HDR and other new modes are not enabled by
this trial. The base fresh-profile installer retains its original 1080p
baseline; this is an explicit native-profile override for the current sink.

Only those two setting values changed. HDMI stereo, sync-to-display off,
DRM PRIME decoder/render selection, GUI mode and every other returned setting
value were retained. No Kodi/OS/add-on restart, boot edit or listener change
was required. The native settings API updates memory; persistence was verified
separately after opening `systemsettings` (window 10016) and using `Input.Back`,
whose native category Back action saves settings. `guisettings.xml` contains
refresh value 2 and all 13 comma-separated modes. Do not edit that file over a
running Kodi process or infer persistence from API readback alone.

The local, hash-verified synthetic 3840×2160/24 HEVC SDR/AAC stereo source ran
for its 30-second duration. Fifteen samples showed actual 3840×2160 at 24 Hz,
corroborated by active DRM CRTC/framebuffer readback and advancing playback.
Kodi reported `ff-hevc-drm_prime`; this label alone is not a separate hardware
acceleration qualification. Temperatures were 35.6–37.8 °C and throttling flags
remained zero. On completion output returned to 1080p60. The unchanged original
PM4K item was reopened through its selected native UI entry, then restored
within 0.1 second of the captured position and its prior paused state; audio and
subtitle selections matched. Its 1080p/23.976 video now selects a matching
1080p23.976 output, while the saved GUI baseline remains 1080p60.

At the owner's request, the 30-second test was repeated, again with 15 samples
of actual UHD24 output. The owner confirmed **picture and sound were smooth**.
The original PM4K item, audio/subtitles and latest playing state were restored
within 0.1 second of the newly captured position. The earlier paused restoration
above describes the first test; the repeat preserved the latest playing state.

A read-only Plex check matched the exact current episode: its sole attached
version is 1920×1080 H.264, with no server transcode session. The owner selected
native 1080p rather than 4K upscaling; genuine 4K requires a 4K source. PM4K's
installed, unoverridden defaults are local quality index 16 (Original), direct
play/remux and `allow_4k`, with a 2160-line direct-play maximum. No client quality
setting needed changing. The later real Plex trial above qualifies one actual
4K network session separately.

Dropped-frame counters were unavailable through the queried labels.
Long/high-bitrate playback, 4K60 and HDR remain separate from this output test.
Protected baseline/settings,
media identity, logs and restoration receipts stay outside Git; the temporary
Pi fixture copy was removed, retaining the existing server test library/source.

For narrow rollback, compare current state, restore only the two recorded prior
values (refresh 0 and empty whitelist) through the authenticated native API,
then use the same native settings Back/save path and verify durable values.
Preserve account state, databases and subsequent watch progress. Do not restore
an old entire profile/database. Resolution switching can briefly blank HDMI at
playback start/stop. See [Kodi display settings](https://kodi.wiki/view/Settings/System/Display)
and [the 21.3 native save path](https://github.com/xbmc/xbmc/blob/21.3-Omega/xbmc/settings/windows/GUIWindowSettingsCategory.cpp#L173-L205).

## First live voice check; remaining tests stopped — October 17

With the current 17-file HA 0.6.1 package active and Nimo's frontend refreshed,
the owner confirmed a real microphone time question produced Phil animation and
one audible LG/Polk reply while already-paused Plex stayed paused. Native same-item
hash/time, actual reply playback and local speaking frames corroborate this
bounded result. Pi 0.7.1's 602 installed files matched its reviewed package.

The owner cancelled further tests and asked to remove Phil from the LG. Nimo
capture was closed, native manual clear and local synthetic dismiss acknowledged,
and final readback showed voice/manual overlays hidden, reply cancelled and video
paused. No permanent animation feature disable was applied. Playing-video return,
music/book acceptance, YouTube setup/playback and the prepared 4K source test
remain deferred. HDMI output remains 1080p60; no 4K output trial occurred.

## YouTube installed and 4K Plex test prepared — October 16 evening

YouTube **7.4.4** from the official Kodi Omega repository is installed and
enabled. Debian's native ARM64 **inputstream.adaptive 21.5.9** and `libwebm1`
were added; the existing enabled requests 2.31.0 was retained. Kodi reports
both new add-ons enabled and not broken. Optional inputstreamhelper/PySocks
were not needed. YouTube has **not been opened**: its setup wizard, Google
authorization, playback and Premium behavior remain unaccepted.

The official ZIP and its SHA256 sidecar matched
`d744e5ba2d2b50924a9fa5624f4d209c2be95b97ef1ad1682cafbed160fb428f`.
Extraction refused an existing destination and checked all 220 members for
path traversal/symlinks before publishing only the new owned add-on directory.
An online Addons33 database backup and affected profile settings were captured
privately first. No live Kodi database was edited. A temporary **loopback-only**
native EventServer accepted one `UpdateLocalAddons` command without a HELO
notification. The installer waited for its client to expire before restoring
EventServer/TCP 9090 off. Native HTTP/HA access was retained; YouTube's own
normal HTTP helper listens only at **127.0.0.1:50152**. Kodi's PID, media
identity/play state/position, GUI, HDMI stereo and 1080p60 settings matched
before and after. No Kodi/HA/Nimo restart or playback operation occurred.

The 9950x Plex server has one dedicated **Pi Playback Tests** personal-video
library, using `Plex Video Files` / `tv.plex.agents.none`. Its sole directory is
`/media/downloads/pi-playback-tests` inside Plex, backed by
`/mnt/media/downloads/pi-playback-tests` on the host. Global dashboard inclusion,
cinema trailers and preview-thumbnail generation are disabled for this library.
Existing libraries and server/transcoder settings were retained.

The test item is **synthetic-4k24-hevc-main-sdr-30s**: 3840×2160, 24 fps,
720 frames, HEVC Main 8-bit BT.709 limited-range SDR, AAC 48 kHz stereo, with
quiet tones/fades and visible frame/time counters. Plex indexes it as 4K HEVC
with AAC stereo; it is unplayed. This synthetic baseline does not establish
real high-bitrate movie, HDR, 60 fps, HDMI or physical playback acceptance.

### Generate the fixture offline

`tools/prepare_4k_fixture.py` uses only local CPU ffmpeg/ffprobe. It limits
affinity to at most eight logical CPUs with nice 10 and idle I/O, refuses
existing output, validates the full software decode and three synthetic review
frames, and writes measured metadata/hashes in a private manifest. It never
contacts the Pi/Plex, creates a library or plays media. Generated media, frames,
manifests, account data and recovery copies stay outside Git/RAG.

```bash
python3 services/pi5-bedroom-display/tools/prepare_4k_fixture.py \
  --output-dir /private/runtime/pi-4k-fixture
python3 -m unittest discover -s services/pi5-bedroom-display/tests -q
```

### Held test sequence

**Wait for Jeremy to say his show is over.** No automatic post-show task exists.

1. Capture the current media identity/progress and TV source before any switch.
   Open YouTube from Kodi's video add-ons, complete its setup/sign-in privately,
   and then check basic browsing/playback. Keep Google codes and credentials on
   the owner's devices. Account linking and Premium/paid-content behavior need
   their own verification; installation alone proves neither.
2. In PM4K, select **Pi Playback Tests** and the synthetic item. Start with the
   existing **1080p60 HDMI output** and Original quality/Direct Play enabled.
   Confirm the server delivers the 4K HEVC video without video transcoding and
   Kodi uses the Pi's hardware decoder. A 4K source decoded and scaled to 1080p
   is a useful test even on a 1080p panel. Record decoder, frame drops/skips,
   buffering, audio, temperature/throttling and sustained behavior.
3. After the baseline passes, qualify Phil/camera composition separately, then
   suitable owner-selected real content. Treat server video transcoding as a
   different result from Pi 4K decoding. No silent fallback may count as pass.
4. Only then consider a separately agreed 4K HDMI output trial. The connected LG
   EDID advertises 2160p input modes; this does **not** prove its physical panel
   resolution. Confirm the model/panel first and retain a 1080p rollback. HDR,
   10-bit and 4K60 are later separate qualification cases.

Rollback removes only the newly owned YouTube add-on/data, optional newly added
native dependency packages if no longer used, and this test library/file. Disable
the add-on through Kodi before removing its directory. Do not restore an old
whole Kodi/Plex database over later account or viewing progress; compare current
ownership and use the narrow native controls first.

## Deployment checkpoint — October 16 evening

Add-on 0.7.1 is installed with 602 exact file hashes, including 576 unchanged
approved Phil frames. Only the add-on was disabled/re-enabled; Kodi's process,
media identity/state and settings were preserved. The source fixes cover
player-aware rejection and named ALSA continuing-progress guards; 52 offline
Pi display checks pass. This is software/readback verification. No voice,
playback or overlay test was triggered; the owner's show-end hold remains.
HA 0.6.1 is staged separately and still requires a normal Core restart.


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

## Source-prepared named ALSA supervision — 2026-10-16

Prepared source only: no deployment, add-on/service restart, audio/playback test,
TV/input action or physical qualification occurred. The owner's live-test hold
remains. Existing installed 0.7.0/601-file results above remain historical;
the new helper increases the closed source-built package to 602 files.

`kodi/alsa_status.py` resolves the configured `vc4hdmi0` card name via each
`/proc/asound/cardN/id` observation rather than assuming card 0. Missing,
ambiguous, changed or malformed card/status mappings fail closed. Its status
reader handles ALSA's padded field names and `closed` sentinel. The fixed player
still targets only named HDMI ALSA and accepts only the existing private bounded
PCM file; it adds no audio device, passthrough path or network listener.

The reply helper confirms its own `aplay` PID and advancing hardware pointer
in ALSA `RUNNING` or `DRAINING` playback state before announcing start, then
continuously checks progress. ALSA draining still plays pending frames; a short
reply may reach that state before the first observation. This does not relax
ownership/progress evidence or the requirement for a clean player exit.
See [ALSA PCM drain semantics](https://www.alsa-project.org/alsa-doc/alsa-lib/group___p_c_m.html).
It terminates on
missing status/changed ownership, a three-second unconfirmed start, a two-second
post-start progress stall, parent EOF/heartbeat loss or the existing 63-second
ceiling. A stalled/uncertain reply reports failure, never clean completion or
permission to resume media. Silent fixtures prove source policy only; physical
HDMI startup/continuous playback/reacquisition still need attended acceptance.

Retain `Restart=no`, the connected-HDMI start condition and explicit operator
display recovery. No hotplug watcher or automatic Kodi restart is added: native
PM4K may restore prior media/UI state, so a no-replay automatic restart has not
been demonstrated. After a physical unplug/boot-without-TV, inspect fresh player,
TV/input and display service state before any owner-authorized start; do not
automatically restore an item, overlay or voice lease. An in-flight reply failure
forfeits return, and recovery must not resume that media later.

For later authorized add-on deployment, preserve the linked profile and compare
the reviewed current owned files before disabling only this add-on/updating its
closed ZIP/re-enabling it. The builder and both closed installer allowlists ship
`alsa_status.py` with its callers. Back up the prior package; when withdrawing
this source change restore only its owned callers and remove the helper only if
no retained caller uses it. Do not restore a whole profile or restart Kodi merely
to replace supervision code. HA/Pi speech changes may add dependencies and must
be reviewed/deployed as one matching package.
