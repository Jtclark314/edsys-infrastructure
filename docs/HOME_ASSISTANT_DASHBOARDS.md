# Home Assistant Dashboard Design

## Accepted scope: 2026-10-11

The owner authorized a complete dashboard review and redesign. The live audit
on Core 2026.10.1 covered all ten stored dashboards, the generated built-in
dashboards, 92 devices, 1,079 registered entities, 792 live entities, twelve
areas, and 37 integration entries. These are dated observations, not ongoing
health guarantees.

The redesign produced twenty curated views across nine stored dashboards. The
integration-generated iCloud3 dashboard remains unchanged. Existing dashboard/view URLs, sidebar
metadata, and the Systems dashboard's admin-only restriction are preserved.
The system default was changed through the native Dashboards UI to `edsys-home`;
opening the base URL was verified to land on `/edsys-home/home`. Explicit
per-profile dashboard selections remain Home Assistant's responsibility.

## Layout and navigation

- Home: daily lighting, current playback, people/batteries, cleaning, scenes,
  room navigation, a door preview, real unavailable controls, and update alerts.
- Lighting: all relevant lights and plug-backed lighting, actual power/today
  counters, existing scenes, and power history. Indicator lights are not counted
  as household lighting. No mains-energy or whole-home measurement is invented.
- Household: existing calendars, groceries, shopping, and reminders.
- Spaces: all twelve areas across Inside and Outside; both new plugs and the
  movie-room Roku are present in their relevant room views.
- Media: room-based playback, native application/input selectors, active Plex
  sessions, the existing Music Assistant launcher, and separate native Roku
  directional/playback remote subviews.
- Cameras: eight actual previews, activity/occupancy, recording/detection health,
  and the existing Camera AI view/history without modifications.
- Systems: admin-only compute, VM, update, automation, printer, plug and mobile
  health; resource bars and history replace repeated large gauges.
- Network: access-point connection, load, clients, uptime, firmware and history.
- Apple: actual current tracker IDs, useful people/device summaries, map, and
  separate tracking diagnostics. Raw tracker diagnostic strings are not treated
  as proof of current GPS freshness or battery charging.
- Map and Vehicles: curated trackers, readable vehicle status, actual camera
  context, timestamps, and existing recognition data. Unknown remains unknown.

Use native Sections, Heading, Tile, Shortcut, media, calendar, list, map, and
history cards. Sections are unframed; no theme, HACS package, new frontend
resource, or external service was installed. The native generated dashboards
remain available. The unconfigured native Energy setup was left untouched.

## Verification and boundaries

All nine saved configurations read back exactly, with no missing entity
references, invalid internal view links, or invalid configured action targets.
Every redesigned view was opened through the existing signed-in native browser.
The generated dashboard routes were also reviewed. Desktop and 375/400-pixel
phone layouts, camera images, current playback, calendars/lists, and history
charts were inspected. Camera AI's existing preview and stored analyses rendered;
no new paid analysis was requested.

SHA-256 comparisons confirmed `automations.yaml`, `scenes.yaml`, integration
configuration, entity/device registries, and iCloud3 configuration unchanged.
Dashboard metadata and frontend resources matched the baseline, including the
admin boundary. The new plugs, both phone trackers, and movie-room Roku/remote
were available; all eight camera states were available. Existing unavailable
devices and the intentionally unavailable vehicle-notification automation were
not repaired, enabled, deleted, or hidden as healthy.

No household actuator, scene, Roku command, vacuum operation, notification,
integration reload, service restart, or upgrade was triggered by the redesign.
Physical-control acceptance is therefore not renewed by these UI/data checks.

## Bedroom media and device recovery: 2026-10-11

A subsequent owner-authorized recovery added the existing master-bedroom LG
and its Plex client to Home, Spaces, and Media before diagnosing unavailable
devices. Media now includes `/edsys-media/master-bedroom`, a responsive native
view with LG applications/inputs, volume/mute, and separate Plex playback.
The nine curated dashboards now have twenty-one views; prior URLs remain valid.

The LG integration's configured address was stale. The active Plex session
identified the TV's current LAN address; both LG control ports were reachable
there. Native LG reconfiguration validated the existing device identity and
pairing, updated only that connection, and recovered live on/app/volume data.
Plex became available when the owner opened its app. Read-only LG inspection
confirmed `external_arc` audio output. The dashboard's LG volume/mute controls
are the intended HDMI ARC path to the connected Polk soundbar, but audible
control and soundbar power synchronization remain physically untested. No
volume, playback, input, or power command was sent during acceptance.

The owner confirmed the remaining hardware dependencies: the driveway
floodlight is broken and needs replacement; the unavailable Apple Watch needs
charging; the old Living Room Vizio Cast TV is no longer used. Only that Cast
entity was disabled through the native entity registry, its restored stale
state removed, and its references removed from Home and Systems. Its device,
registry record, and history remain; the other Cast TVs and Roku endpoints were
not changed. A single native iCloud account refresh succeeded without recovering
the uncharged watch. Both iCloud3 phone trackers continued to report normally.
Inactive Plex client entries and the intentionally unavailable vehicle
notification automation were not deleted, enabled, or represented as healthy.
Optional unavailable iPhone pace/cadence sensors were left unchanged; their
availability was not evidence of a whole-phone or iCloud3 outage.

Saved configurations and links were checked against current entities. Device
registry, automations, scenes, iCloud3 configuration, dashboard metadata, and
frontend resources retained their prior fingerprints/readbacks. The expected
changes are the LG config entry and one disabled entity-registry record. The
bedroom native controls rendered on desktop and a 375-pixel phone viewport.
The browser also logged a native media-control initialization error while cards
still rendered and playback progress updated; this is not a clean-console or
physical-command acceptance. No Home Assistant restart, integration upgrade,
account reset, or notification was used to mask an unavailable device.

The subsequent 2026-10-11 media-card repair resolved that initialization warning
with one versioned local frontend resource. It leaves native cards, layouts and
installed frontend packages intact; see the
[initialization guard runbook](../services/home-assistant-frontend/README.md).
Fresh signed-in media views and desktop/375-pixel framing were verified with
no media-card console errors. State updates, artwork where present, and native
progress display remained functional. No playback/volume/power command was
sent. Only resource metadata and the new local asset changed; dashboard and
protected configuration/native frontend fingerprints remained unchanged.

Follow-up runtime snapshots and journals are owner-private under
`~/.local/state/ha-device-recovery-20261011-*/`. Do not rerun earlier dashboard
apply scripts: their expected after-version predates this follow-up. For an
authorized narrow rollback, compare live JSON with the follow-up after-version
before restoring its before-version. Reenable the retired Cast entity through
the native registry only if that device returns to service. Restore an LG host
only after identifying the actual TV and validating its current address through
native reconfiguration; do not write pairing keys or storage files directly.

## Private recovery and further edits

Full before/after dashboard JSON, inventory, intermediate versions, guarded
apply/rollback scripts, journals, and verification remain in the owner's
mode-0700 directory under
`~/.local/state/ha-dashboard-redesign-20261011-*/`. Its runtime files are mode
0600 and must not enter Git, Obsidian, or RAG: they can contain household data,
opaque provider identifiers, and private tracking information.

A VM300 snapshot named `pre-dashboard-ui-20261011` was created on pve-node3
before writes. It is an additional recovery checkpoint, not a newly verified
full Supervisor/off-site backup policy. Do not restore the whole VM for an
ordinary dashboard rollback or without separate restore authorization.

Prefer a narrow rollback:

1. Locate the private checkpoint and read its current verification/journal.
2. Preview `python3 apply_dashboards.py --rollback` from that directory.
3. After authorized restore, run it with `--rollback --execute`; use
   `--dashboard URL_PATH` to limit restoration to one dashboard.
4. It compares current configuration to the saved after-version before writing
   the original. Any intervening user edit blocks the restore for review.
5. Reopen affected desktop/mobile views and verify exact configuration readback.
6. To undo the system starting-page change, use Settings > Dashboards and set
   the built-in Overview as default. Do not overwrite frontend user/system
   storage wholesale or modify explicit profile preferences.

Do not rerun the original inventory collector after edits if its `before/`
directory already exists. Read the live configuration before further changes.
Use the existing protected route in [Home Assistant Control](HOME_ASSISTANT_CONTROL.md);
never extract browser credentials or weaken authentication for dashboard work.

Native references:
- [Sections](https://www.home-assistant.io/dashboards/sections/)
- [Tile](https://www.home-assistant.io/dashboards/tile/)
- [Shortcut](https://www.home-assistant.io/dashboards/shortcut/)
- [Roku commands](https://www.home-assistant.io/integrations/roku/)

## Master Bedroom LG remote: 2026-10-14

The owner authorized a fuller native remote in the existing Master Bedroom
view. Its URL and original LG media card, app/input selector, volume slider and
paired Plex card are retained. Two added native sections provide D-pad/OK,
Home/Back/Exit/Settings, wake/off, volume/mute, playback, app/input shortcuts,
numbers, channel functions and colored keys. Navigation controls hide when the
TV is off/unknown/unavailable; the existing Ethernet wake remains accessible.
No other view or dashboard is changed, and no HA restart, new HACS dependency,
frontend resource or voice tool is added.

Nimo Voice 0.3.0 adds a separate Master Bedroom TV Remote desktop shortcut/window
using its existing local sign-in. Focused keyboard controls route arrows,
Enter/Escape/Home/digits only to the reviewed LG binding. It denies microphone
access and registers no global shortcut; ordinary voice retains Alt+V. The
helper rechecks connection, identity, area, availability, route and dialogs,
drops held/concurrent keys and does not replay failures or expired preflights.

Three layout/scope and fourteen keyboard regressions passed, as did native C#
compilation, PowerShell parsing and pinned SDK publisher checks. Saved config
readback was exact; original cards and the other views matched baseline.
Desktop and phone-width layouts rendered. Live keyboard/registry preflight was
qualified with dispatch suppressed, then all probes were removed and production
script/source digests matched. A real webOS volume-down command changed the LG
state; its original volume was restored and Plex remained playing in the same
app. The owner confirmed Home, arrows, OK and Back respond correctly on the TV.
Individual app-specific playback, tuner and colored-key behavior remains to be
confirmed when used. This does not renew the separate voice/audio, Wi-Fi or
physical soundbar-power acceptance. Private full dashboard and Nimo binary/
script baselines support narrow recovery without discarding later edits.

Owning source: `services/home-assistant-lg-remote/` and `services/nimo-voice/`.

## Master Bedroom Pi Plex page: 2026-10-14

The owner's requested Media → Bedroom Pi Plex page (`/edsys-media/bedroom-pi`)
adds a native Kodi media/transport/navigation view for the actual Pi, with LG
HDMI 1 and soundbar volume controls alongside. Media overview and Master Bedroom
link to it. Existing view/card contents are preserved; exact save/readback and
three preservation/target/firewall checks plus five LG regressions passed.
Actual signed-in Nimo browser clicks changed Pi play/pause and seek state and
showed/dismissed Info via Info/Back. Position/playback were restored, desktop/
narrow layouts inspected, and the owner independently confirmed playback control.

The existing Plex-reported PM4K session lacked transport features, so HA's native
Kodi integration is used. New endpoint-only HTTP credentials remain private;
8080 is admitted only from HA on the Pi firewall. WebSocket 9090/discovery stay
disabled; native HTTP polling applies. No frontend dependency or HA restart.
The Pi presentation-event/voice integration remains a separate work package.
See `../services/home-assistant-pi-plex/README.md` for setup and narrow recovery;
never restore an old complete dashboard or private profile over later changes.

## Music Assistant access: 2026-10-16

Fourteen native teal **Music Assistant** shortcuts, described as **Music &
audiobooks**, now sit with playback controls across Home, four indoor TV rooms
in Spaces, all five Media views, and Bedroom Pi's Player, Music & books, Night &
art and LG remote. These cover eleven views in four dashboards. Existing Music
launchers were replaced; other cards/actions and view order were preserved.

Each uses the existing HA app ingress route, preserving the current HA access
path instead of requiring the direct private MA LAN URL. Opening a shortcut
only navigates; it neither selects an output nor starts playback or changes
TV power/input. Native cards need no theme or new frontend resource. The Pi's
Music & books guidance identifies the bottom-right current-device-name button
as MA's output picker. The adjacent Group members control is separate.

Live Nimo inspection found the Pi available in the actual MA picker. Selecting
Bedroom Pi Plex changed its displayed output without starting music; Kodi's
paused time remained unchanged. No provider reconfiguration/reload was needed.
Four dashboard before/after pairs passed exact saved readback and a structural
comparison proving that only launcher cards and the output-picker help paragraph
changed. Six existing native Pi/LG dashboard checks and syntax checks pass.

Owning builder/deployer and narrow rollback:
`../services/music-assistant/dashboard_access.py` and
`../services/music-assistant/README.md`. Keep private dashboard checkpoints
outside Git/RAG; compare the saved after-version before restoring owned cards.

The native registered frontend target is `/app/d5369777_music_assistant` on this
HA version. The old `/hassio/ingress/…` route is not registered and falls back to
Home. The deployer now checks for the `app` panel. Native teal tiles rendered on
Nimo; actual Home and Master Bedroom remote clicks reached MA with Bedroom Pi
Plex selected. The paused Pi position remained unchanged. No music/book sound,
voice selection or speech interruption was tested by this navigation check.
