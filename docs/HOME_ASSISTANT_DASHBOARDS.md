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
