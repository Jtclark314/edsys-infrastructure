# Home Assistant conversational voice

## Post-show activation and stopped acceptance — October 17

Current main's **17-file 0.6.1** component is active after a scoped component
backup, 92 installed-Core candidate checks, configuration validation and one
normal Core restart. Exact source hashes, private bindings, all four pipelines,
dashboard metadata and resources were retained, including the weather helpers.
Do not install the older 15-file priority stage over this current package.

The owner completed one real Nimo microphone test with Plex already paused:
Phil animated, one reply was heard through LG/Polk, and Plex stayed paused.
Native evidence retained the same media identity and exact position; actual
reply playback and local speaking frames were observed. The owner then cancelled
further testing. Playing-video automatic pause/resume and the remaining live
cases are unaccepted. Cleanup closed Nimo capture, dismissed Phil and manual
cards, cancelled reply audio, and left Pi playback paused. Current-state details
and router follow-up are in EdSys-Master `docs/PI5_EDGE_LIVINGROOM.md`.

## Weather screen extension — October 17

The installed 0.6.0 component now has 17 files after a selective weather-screen
extension: only `bedroom_screen.py` changed among its existing files, plus two
new helpers. Its original two-method RPC scope and all voice routing/reply files
were retained. One authorized Core restart loaded the extension; it did not
activate staged 0.6.1. Future priority activation must use current main, including
the weather helpers, rather than overwrite it with the older 15-file stage.
Live voice/playback and physical weather-screen acceptance remain held. See the
[weather rollout](../home-assistant-weather/README.md#manual-pi-tv-weather--october-17-2026).

## Earlier deployment checkpoint — October 16 evening

Priority routing source is version 0.6.1. Its 15 files are staged in
`/config/edsys_voice_knowledge/staged/priority-0.6.1/component`; active 0.6.0
was retained. Activate with a normal Core restart after the owner's show-end
hold is released. Config-entry reload retains cached modules and is insufficient.
The companion Pi 0.7.1 is installed. All 25 HA routing/offline checks pass;
physical reply/interrupt/resume acceptance remains held.


## Player-aware reply routing candidate: 2026-10-16

This source change is an offline-qualified candidate; deployment and live voice/
playback acceptance are separate. Before selecting Pi reply audio, HA reads only
the authenticated Kodi foreground player type and speed. Idle or one explicitly
internal video player at normal/paused speed can use Pi audio. Music, audiobooks,
external/multiple players or unknown metadata retain ordinary Nimo speech, with
Phil animation on the Pi when the existing TV/input/user/exposure gates permit.
No title, file or media URL is fetched for this routing decision. A changed TV,
permission or unavailable display degrades to ordinary Nimo without a Pi lease.

The Pi revalidates its current player at the speech commit, before any pause,
sink suspension or player launch attempt. It can explicitly reject that exact
commit with request ID, audio reference, sequence and prepared WAV digest plus
false started/paused/handoff flags. Only this authenticated, still-owned proof
permits one Nimo playback of the same generated reply. A missing/malformed/lost
acknowledgement, changed turn or any possible handoff keeps fallback disabled.
The pipeline and household command are never run again. This is conservative
Nimo routing during music/books; MA-aware interruption/return is not added.

The Pi sink-release check uses the shared named-card ALSA status helper from
the companion audio fix; deploy that helper and its package allowlists together.
For recovery, compare current owned hashes and restore only these routing/
protocol/reply files from the prior component/add-on checkpoint, retaining native
profiles, provider state, current media/progress and later user edits. Validate
Core before the authorized normal Core restart. Offline tests use only local
Core/transport stubs and fake player backends; installed-Core/frontend callbacks,
human hearing and live interruption cases remain held.

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

HA component 0.6.0 has 15 closed files. `phil_voice.py` owns the authenticated
lease; `phil_reply.py` wraps the installed Core schema/handler without editing
Core, preserving binary input, context, subscriptions and command execution.
Requalify this native handler and frontend callback dependency after HA upgrades.
The private `phil_voice.json` selects the existing pipeline and output `pi`.
Two-second heartbeats, six-second client loss, seven-second Pi expiry and a
three-minute turn ceiling bound ownership; WAV replies are limited to 60 seconds.
The broader bedroom presentation/data contract remains separate and unpromoted.

Recovery: compare current owned hashes, restore the prior 14-file component and
private Phil binding, remove only added `phil_reply.py`, and restart Core after
configuration validation. Preserve speech profiles, provider state, native
bindings/key and later user changes. Pi/client recovery is in their runbooks.

Status, 2026-10-14: general chat, web search, read-only EdSys lookup and a bounded
household pilot and Master Bedroom media remote tools are deployed
in the existing named voice profile. Candidate and production pipeline/audio,
backend-failure and synthetic full-speech checks passed. The owner confirmed
audible native time, chat and knowledge replies through Alt+V on Nimo.
Web audibility, Wi-Fi handover and physical Pi commissioning remain pending.
Qualified native Core version: **2026.10.1**. See the current EdSys-Master voice
record for the final deployment/acceptance checkpoint.

[Owning architecture](https://github.com/Jtclark314/EdSys-Master/blob/main/docs/HA_CONVERSATIONAL_VOICE.md).

## Behavior and authority

Nimo's existing Alt+V and the prepared Pi's voice endpoint use the named
`EdSys Living Room Voice` pipeline. Existing HA Cloud STT/TTS are retained.
Jeremy selected en-US AndrewNeural on 2026-10-13 and confirmed it should stay
selected, superseding initial AmberNeural qualification. Native HA intents run
first; general conversation uses native
OpenAI Conversation with `gpt-6.1-sol`, low reasoning, low verbosity, Standard
service, no Pro mode and a 2048-token response ceiling. Replies usually use
one or two sentences and at most 60 words. Web search is enabled for changing
public facts. This remains dependent on existing Internet/Cloud services.

`profile.json` and `prompt.jinja` are the sanitized settings. Request storage,
home-location disclosure and Code Interpreter are disabled. This is not a
claim of provider zero-retention. The owner created/entered the key directly
in HA's native OpenAI integration. The key stays in protected native HA state;
no provider credential belongs on Nimo, Pi, the hub, Git or RAG. The integration's
default AI Task/STT/TTS subentries are retained but unused by this voice path.

The selected OpenAI backend receives the reviewed `edsys_knowledge` API plus
built-in web search. With the pilot configured, that API provides documentation
search, live pilot lookup, narrowly scoped pilot control and Nimo camera
presentation. It has no broad Assist API, arbitrary service/entity, shell,
messaging, file-write or scheduling tool. Normal existing HA entity exposures and household
profile preferences remain authoritative. Unsupported home requests are not
converted into invented actions. The retired Phil/Gateway stacks stay retired;
ChatGPT Dot memory/apps are not inherited by this API assistant.

## Why the adapter exists

Core 2026.10.1 marks any OpenAI tool/API attachment as CONTROL, including a
read-only documentation reader; this changes native fallback intent filtering.
The small `EdSys Voice` conversation entity uses HA's public conversation API.
Without a pilot it retains the original zero-feature read-only adapter; with
the reviewed pilot it advertises CONTROL. The named production profile enters
the adapter first, where native recognition stays local and pilot controls are
validated and verified before replying. Other matched native intents remain
with HA; unmatched requests go to the existing OpenAI backend. This preserves native handling while attaching the reviewed tools.
No Core or OpenAI integration source is patched. The adapter stays available
when the backend is disabled/unavailable so native commands can continue;
general-chat delegation has a 45-second timeout. Cancellation is not swallowed.
HA Cloud STT/TTS still require their existing connectivity.

## Knowledge scope and freshness

The existing reviewed grounding index on 9950x is exported to a filtered local
SQLite snapshot. Foothills/Procore, financial, Excel, homeowner, meeting and
Kindle records are excluded. Search returns documentation, not live health.
Source audit dates and document locations accompany bounded excerpts; document
locations must not be mistaken for operational service paths.

- Canonical source: `/mnt/ai-store/rag/grounding/edsys-grounding.sqlite`
- HA private catalog: `/config/edsys_voice_knowledge/index.sqlite`
- Query: at most 512 characters; four results, at most two per source
- Excerpts: at most 1200 characters each
- Freshness: actual canonical index build age at most 30 minutes
- Missing, corrupt, symlink, wrong-schema/scope and future/stale catalogs fail closed
- SQLite reads use read-only mode and `query_only`; queries are parameterized

The refresh runs every five minutes as the existing 9950x operator user. It uses
the existing privileged SSH/QEMU-guest route with a fixed VM/container/destination,
atomic replacement, bounded decompression, integrity and SHA-256 readback checks.
No listener, new network credential or immediate hub call is added to a voice
turn. If the hub stops refreshing, knowledge fails closed after its freshness
window; general chat and native home intents remain separate.

The scheduled refresh and HA backup collector serialize their fixed VM300
commands through remote node3 `/run/lock/edsys-ha-qga-300.lock`. Refresh waits
at most two seconds for the lock, then exits unsuccessfully with no receiver
invocation and leaves the prior catalog intact. Its transport timeout is 60
seconds inside the existing 65-second unit timeout. The next existing timer
run can refresh normally; catalog freshness still fails closed after 30
minutes. The collector waits at most ten seconds per attempt and retains its
bounded read-only retries. This is limited to these two workers: operators
must avoid concurrent manual QGA work or take the same remote lock. No global
wrapper, additional credential or unrelated service dependency is added.

## Installation and configuration

Before change, preserve the existing pipeline and a recovery checkpoint. On
2026-10-13 `pre-openai-voice-20261013` completed after the owner entered the key.
Native OpenAI onboarding must use the ordinary owner's secure HA interface.

1. Dry-run `configure.py --private-state PRIVATE_DIR`, then use `--apply` only
   under existing owner authorization. It creates an isolated native candidate
   and retains speech settings and the working/preferred profiles. Native form
   field checks reject unsupported inputs before submission. Inspect actual
   state after failures; creation requests are never automatically replayed.
2. Run `sync_knowledge.py` and `install_knowledge.py`. They write only this
   catalog/component. Validate Core configuration and native imports. A normal
   Core restart may be required to register a newly installed custom domain.
3. Add `EdSys Voice Knowledge` through native config flow, selecting the new
   OpenAI backend. Attach only `edsys_knowledge` to that backend. Point the
   candidate at the adapter, retaining `prefer_local_intents: true`.
4. Verify the candidate, including actual search/tool use, matching sources,
   no-source/stale refusal, reply audio, native routing and backend unavailability.
   Then switch only the existing named target pipeline's conversation engine.
5. Install the two user units in `systemd/`, reload and enable/start the timer.
   Validate a real user-unit refresh, not only a direct shell run.

Use `verify.py` with private state for read-only native time, general chat,
actual web search and reply-audio checks; `--speech-fixture` also traverses
STT and `--knowledge` verifies a real knowledge-tool invocation. These synthetic
fixtures do not establish room acoustics, human audibility or Wi-Fi handover.
Use `--production` for the saved target profile and `--case` for a single
time/general/web/knowledge/unknown-host/backend-unavailable fixture. The latter
requires deliberately disabling only the candidate backend, inspecting actual
state, then restoring and verifying it; never repeat an uncertain mutation.
Python/unittest and native runtime checks must pass after changes; verify the
public `supported_features` property, not Core's transformed private class field.

## Reviewed home pilot

The owner selected **only Gadget Lab Lights** for the room-light phrase; its
desk downlights stay separate. The owner confirmed the Master Bedroom LG TV
and LG Plex client are the same device, and the TV uses Ethernet. Native area
and entity aliases identify lab, primary/main bedroom, the bedroom TV and Plex.
The Plex entity has its own name/area; the shared Plex service device is not
moved. Default computed-name aliases are retained.

Private `/config/edsys_voice_knowledge/pilot.json` binds stable device/config-entry
identities and reviewed area IDs to three control roles and seven located
cameras. The unassigned Security Camera is excluded pending physical mapping.
The dictionary is read from current HA state; each operation rechecks identity,
room, permission, Assist exposure, availability and advertised capability. It
does not use the dated EdSys documentation index to select devices.

Supported pilot actions: main light on/off; TV on/off, mute/unmute, explicit
volume percentage and Plex launch; active bound Plex session pause/resume/stop.
New title selection, ambiguous bedroom, implicit pronouns, compound/conditional/
negated or scheduled commands require clarification. Mixed native groups that
include a pilot device are not partially dispatched. No arbitrary/shared Plex
client is substituted. Replies verify HA-reported state, not independent
physical sensors. Timeouts retain an uncertain outcome and are not retried
automatically. A duplicate tool call in the same turn cannot replay a command.
Unsupported native actions on a bound pilot device are rejected before dispatch.
Spoken control outcomes come from the executor's verified state or explicit
uncertainty; a model cannot paraphrase a failed tool call into success.

The named profile's **`prefer_local_intents: false` is intentional**: native
recognition happens inside the local router so validation precedes execution.
Enabling the outer preference would bypass pilot verification. Speech settings,
Andrew, the household preferred profile and other profiles are retained.
The router uses `conversation.HOME_ASSISTANT_AGENT`; Core 2026.10.1's identifier
is `conversation.home_assistant`. Native language `*` is normalized internally
to HA's language without rewriting the user's speech settings.

The LG address was repaired through native UUID-checked reconfiguration after
its confirmed local Plex client revealed the current interface. Native
`wake_on_lan` plus one explicit `webostv.turn_on` automation supplies Ethernet
wake. Its interface identifier stays in private HA state. Open Plex wakes an
off TV, waits for the live source list, selects a unique current Plex source and
verifies the active app. It does not choose a movie.

Native camera intents and the optional language fallback return only a typed
card to the requesting Nimo pipeline. Nimo displays HA's native more-info camera
view, validates the card and reports advancing video versus an image or failure.
No camera frame reaches the OpenAI conversation API. LLM Vision camera analysis
and its two automations remain mothballed. A headless/non-Nimo client asks which
screen; TV camera playback is not commissioned.

Apply with `configure_pilot.py --bindings PRIVATE_JSON --private-state PRIVATE_DIR
--apply`, install this component and validate/restart Core. Only after native
runtime checks, use `enable_pilot.py --private-state PRIVATE_DIR --apply`. Its
complete-field update preserves speech settings; rejected writes are inspected
before an explicit `--resume`. No command silently retries a mutation.

Recovery: `pre-voice-pilot-20261013` snapshot task completed. Prefer restoring the
private mapping/profile baselines, previous component and only the owned wake
automation/configuration; a full VM rollback would discard later unrelated
changes. Nimo keeps a private previous binary/scripts and its original sign-in
profile. Private identifiers, runtime catalogs, records and screenshots never
belong in Git/RAG.

Verification so far: 14 local catalog/phrase checks, 19 isolated tests against
installed Core libraries, native imports, syntax and configuration validation.
Actual named-profile light on/off returned verified replies and audio; desk
state/change timestamp were unchanged. Native time remained local. Real Ethernet
wake and wake-then-Plex launch reached TV-on/Plex state; app-launch fixture took
about nine seconds. Absolute TV volume changed by one percentage point and
was restored with verified replies/audio. Further media, Nimo camera and human acceptance are recorded
in the owning current-state document, not inferred from these checks.

## Master Bedroom full remote voice controls: 2026-10-14

The owner authorized extending the existing Nimo pipeline to every LG and Pi
Plex dashboard remote action. The optional private `media_remote.json` adds
exactly two reviewed roles, LG and native Kodi. It preserves the original pilot
bindings, camera behavior, provider/key/model, speech settings and profiles.
Simple commands run locally; remaining media phrasing uses the existing cloud
backend's `lookup_bedroom_media` and `control_bedroom_media` tools. Both paths
use the same executor. The legacy cloud control tool becomes light-only while
this media extension is configured; it cannot silently select the old LG Plex
metadata client for a Pi request.

| Say, for example | Result |
| --- | --- |
| Pause Plex / Resume the Pi | Current playback; generic Plex follows the visible LG input |
| Could you lower the sound just a little on the TV? | Cloud interpretation, fixed LG volume-down action |
| Set TV volume to twenty five percent / Mute TV / Unmute TV | LG/soundbar volume, including while Pi is active |
| Show Pi / Switch TV to HDMI one | Physical HDMI 1, independent of its mutable PC label |
| Open Plex on Pi / Open Netflix on LG | Fixed PM4K launch or a unique current LG app/input label |
| Press right on Pi / Press Home on the LG remote | Explicit navigation target |
| Skip back on Pi / Jump to 15:30 on Pi / Skip forward 30 seconds on Pi | Fixed step or validated seek in the current item |
| Press seven on TV / Press guide on TV / Press red on TV | Bounded keypad/channel/colour function |
| Show now playing on Pi / Press captions on LG | Player window or remote caption button |
| Turn subtitles on Pi / Turn TV off / Wake TV | Fixed subtitle action or LG power |

The remote vocabulary also includes previous/next, rewind/fast forward, arrows,
OK, Back, Info, Options, Exit, Settings, all digits, dash/Enter, channel up/down,
and all four colour keys. Apps/inputs must exist in the live source list. A
channel number requires Live TV already active. A new title is chosen through
Open Plex plus on-screen navigation; there is no arbitrary title/URL playback
or library-search tool. Power controls the LG, not the Pi operating system.
Explicit Pi playback can run with the LG off; generic active-screen playback
asks to wake the TV. Opening Pi Plex does not also switch/wake the LG.

The current LG app ID selects the active remote: HDMI 1 means Pi; another app
means LG. The reviewed source label is only a fallback when app ID is absent.
Volume, power, inputs and TV-only keys always address LG. Explicit Pi/LG selects
navigation/playback, while bare bedroom still requires clarification. One
immediate command per authenticated turn is accepted; parameters must match
the utterance. Registry identity, effective room, active user permission,
Assist exposure, availability and advertised native capabilities are rechecked.

Stateful changes return verified only after HA readback. Navigation, captions,
colours, channel keys and LG app transport return submitted/“Sent”; receipt of
a key does not establish the on-screen result. The router owns the spoken
outcome even if the model claims more. Duplicate calls in one turn, uncertain
writes and partial wake/channel sequences are not replayed. Cancellation is
preserved. Questions use lookup without actuation. Hardware/app-specific effects
still depend on the active app and TV support.

Run the policy/catalog tests with `python3 -m unittest discover -s
services/home-assistant-voice/tests -p 'test_*.py'`, then `python3
services/home-assistant-voice/test_native.py`. The latter stages the candidate
in a temporary Core directory, runs fake-device permission/routing/timeout tests
against installed HA libraries, and never installs it or calls real devices.
Dashboard builders are used to check full explicit-button recipe coverage.

Under owner authorization, use `configure_media_remote.py --tv MEDIA_ENTITY
--pi MEDIA_ENTITY --private-state PRIVATE_DIR --apply`. It checks the current
webOS/Kodi registry, Assist exposure and Master Bedroom assignments, saves
pipeline recovery locally, and backs up the installed component in private HA
`/config/edsys_voice_knowledge/recovery/media-remote-TIMESTAMP`. It writes only
the two-role configuration, without registry, actuator or provider changes.
Install with `install_knowledge.py`, validate Core configuration, then restart
Core once. It deliberately refuses an existing media configuration/recovery
record rather than blindly replaying a partial installation.

To withdraw this extension, restore only that saved component, remove the newly
added media configuration, validate and restart Core. Retain the original
pilot, native Kodi/webOS integrations, provider settings, key, profiles and all
later unrelated changes. Private bindings, baselines and fixture/audio logs
stay outside Git/RAG. Deployment and human acceptance results are tracked in
the owning current-state document. Voice-triggered butler presentation events
and Pi microphone commissioning remain separate work packages.

Qualification: 18 policy/catalog checks, 37 isolated Core checks and eight
existing dashboard checks passed. Live named-pipeline fixtures returned audio
for local Pi resume/pause and cloud tool calls for LG volume, Pi seek/Now playing
and LG Netflix selection, followed by local HDMI 1 restoration. Final regression
cases cover one-point exact volume changes and sending explicit play/pause
despite a potentially stale polled snapshot. Native imports and Core config
validation passed. These checks do not establish physical spoken Alt+V use or
every app-specific key effect. Runtime/source/settings readback is recorded
privately; the current-state document tracks owner acceptance separately.

## Recovery and acceptance

Prefer restoring only the target pipeline's saved native conversation engine
to a full VM rollback. Keep the household preferred pipeline untouched. Stop
the refresh timer and unload/remove only this knowledge entry/component when
withdrawing the feature. Retain the owner's OpenAI integration and key unless
explicitly asked to remove them. Private state, catalogs, fixtures, snapshots,
auth and browser profiles stay outside Git/RAG.

Model/provider limits and budgets remain owner-controlled. Do not claim a hard
monthly spend cap or inspected key permissions. Requalify after HA/model changes.
Physical Pi commissioning and room/soak tests remain separate. Additional Pi
services remain deferred until physical voice acceptance.

## Manual TV screen service

The optional `bedroom_screen.json` enables the authenticated
`edsys_voice_knowledge.bedroom_screen` dashboard service and
`sensor.bedroom_pi_screen`. Actions: camera_popup, camera_large, clear, butler,
clock, dark, media, briefing, household, connections, weather_forecast and
weather_radar; duration 10–300 seconds.
The native HA Kodi runtime client is reused without reading/exporting its
credentials. Its qualified runtime interface is checked; an incompatible future
Core update fails closed. Access rechecks HA user permission, Pi Assist exposure,
stable entity/config-entry/room identities and the visible HDMI 1 input. Clear
works even if the LG is off or on another input. Camera access is rechecked for
every frame. Source bindings also pin registry unique ID/platform; null areas
are pinned rather than silently inferred.

Images and Family/Home/Reminders are fetched locally and rendered by Pi; none
are exposed to the OpenAI tools. The closed manual protocol is bundled from
`../pi5-bedroom-display/manual_protocol.py`; it is separate from the proposed
automatic voice presentation contract. AES-GCM content encryption keeps private
payloads out of PM4K notification logs. Only the Pi's existing authenticated HTTP
connection is used, without a listener or new HA/camera credential on Pi. Sensor
state is a software render acknowledgement, not independent physical proof.

Install with `install_knowledge.py`, run candidate `test_native.py`, validate Core
and restart once for changed Python. Provider/key/model/pipeline/Andrew settings
remain intact. The complete configuration, dashboard, private-state and recovery
workflow is in `../home-assistant-pi-plex/README.md`. Automatic voice-to-screen
commands, speaking animation/captions and AI visual answers remain future work.

## Manual nighttime controls and sleep timer

The optional screen setup also registers `edsys_voice_knowledge.bedroom_comfort`,
`sensor.bedroom_pi_night_controls` and `sensor.bedroom_pi_sleep_timer`. Fixed
operations expose Kodi downmix compression, subtitle tracks/text sizes/timing,
native audio/subtitle panels and explicit start/cancel sleep presets. Existing
user permission/identity/room/Pi exposure and visible HDMI 1 checks apply. No
LLM tool, provider setting, broad service dispatch, listener or OS action is
added. The controller reads Kodi state directly rather than assuming a key had
an effect; native panels are acknowledged as submitted. Compression requires
multichannel-to-stereo playback with passthrough off. Audible/readability
acceptance remains an owner check.

The sleep job retains only its requesting HA context and deadline in memory.
Expiry reauthorises, pauses only Pi video, disarms before writing and never
resumes/retries. It includes queued next episodes on that Pi, leaves TV power
alone and cancels on unload/restart. Cancel requires user permission but can
work while the Pi is offline. New source is bundled by `install_knowledge.py`
and covered by isolated native checks. Full setup, verification and rollback:
`../home-assistant-pi-plex/README.md`. Built-in artwork and personal photos remain
local; photos still require a selected source.
