# Home Assistant Dashboard Design

## Bedroom LG connection and remote readiness: 2026-10-17

The owner reported iPad bedroom Media volume/input errors while the TV was
physically on and showing the Pi. HA's saved LG address was stale; native
discovery and the webOS device identity confirmed the same registered TV at
its current address. Native reconfiguration reused its pairing and restored
live on/input, HDMI ARC, volume and mute state. After refreshing the iPad page,
the owner confirmed Volume Down changes the Polk soundbar volume without error,
then reported no remaining failures noticed. This verifies that physical
volume path; individual remote functions were not exhaustively exercised.

The latest Media and Bedroom Pi dashboards were repaired in place with
`services/home-assistant-pi-plex/readiness.py`. Pi transport/navigation now
requires a connected Kodi state, including treating `off` as disconnected.
LG input/volume/mute controls have separate readiness conditions. Connection
messages distinguish HA availability from physical TV power, and labels
distinguish LG native-app navigation from Pi Kodi/Plex navigation. Wake TV,
navigation links and sleep cancellation remain available. No control runs on
page load and no automatic restart/recovery was added.

Twenty-one Pi source checks, Python syntax checks, unchanged action-payload
and unrelated-view comparisons, idempotence, and exact native dashboard
readback passed. Dashboard metadata/resources were preserved. Existing Pi
playback continued; no automated power/input/volume/playback command or
HA/Pi/Nimo restart was sent. Physical offline-message presentation was not
tested, and live voice/playback testing remains held.

DHCP inspection found no address reservation. A reservation for the current
working address is prepared and syntax-qualified, with private recovery
evidence. Activation is deferred until the show is over because it requires a
brief router DNS/DHCP service restart. No router configuration, lease or
service change was applied.

Recovery uses private before/candidate/readback snapshots: compare the latest
dashboard, remove only the owned readiness wrappers/status cards and reverse
the owned labels, preserving later edits. For another stale LG address, verify
the current native device identity and reconfigure the existing entry; do not
restore an old address solely because it appears in a backup. Source and
procedure: [Pi dashboard readiness](../services/home-assistant-pi-plex/README.md#connection-readiness-and-targeted-dashboard-repair)
and [LG remote](../services/home-assistant-lg-remote/README.md).

## Family Hub: 2026-10-17

The new sidebar **Family Hub** at `/edsys-family/home` has Home, Calendar,
Shopping and Help views for phones and the 11-inch Tab A9+. Navigation-only
buttons are added to EdSys Home's Household (`plans`) and Bedroom Pi's
Household views. All other fields in those dashboards, fourteen pre-existing
dashboard metadata rows and six original resources are preserved by exact
native readback. The system default stays EdSys Home.

The new native Local Calendar is named Family. It is explicitly unexposed
from conversation/Alexa/Google, and event contents are not sent to cloud AI.
The add form pins one calendar, supports timed/all-day events and finite
daily/weekly/monthly/yearly repeats using home time. Existing dates open the
native event editor for editing/deleting. One-attempt mutations distinguish
acceptance from readback, retain drafts on uncertainty, and never replay on
reconnect. Drafts and write locks are RAM-only and do not survive reload.

Six native calendar cases passed, including CRUD, exclusive all-day end,
four recurrence frequencies and weekly DST preservation. A signed-in Nimo
browser saved a marked event through the new form, saw it in the week view,
edited it through the native editor and deleted it; UID readback confirmed
each step. Every owned calendar fixture is removed. Twenty-six Node and four builder
checks plus synthetic tablet/phone/landscape browser checks passed. Physical
phone/Tab A9+ acceptance remains to be confirmed.

Pinned unofficial AnyList 0.8.0 is authenticated through the native flow. Final
setup selects only the owner-selected grocery list, with meal-calendar export
off and 60-second polling. The settled registry has one to-do entity and two
settings selects, all explicitly unexposed. Qualification used one isolated test
list. Seven live HA→AnyList checks passed: comma-preserving add, check/uncheck, rename,
completed rename preserving status, completed-name add reusing the UID, and
UID deletion with empty-list readback. Read-only waits observed 6–10-second
convergence after early cached reads; no write was replayed.

Independent AnyList web sign-in also succeeded. A web-created fixture reached
HA in 19 seconds, and its web quantity/note change reached HA in about 23 seconds.
The actual Hub shopping card rendered that same item and quantity/note using the
unchanged frontend asset. Its checkbox completed the same UID while preserving
quantity/note. Actual Hub two-line entry created separate items with an embedded
comma retained. Independent AnyList web showed those additions and a renamed
completed item; HA confirmed completed rename preserved quantity/note. All three
owned shopping fixtures were UID-deleted, with HA and AnyList web both empty.

Final binding changed only entity/display-title settings in two Hub shopping
cards; every other Hub field matched exact native readback. Signed-in Nimo
rendered the final grocery heading, enabled checkboxes, successful-read time,
Refresh and Open AnyList without setup pending. No regular-list item mutation
was issued. Physical phone/Tab A9+ acceptance remains pending.
Selected entities constrain the frontend, not account-wide upstream reads or
broader HA access. Account/list identities, contents and recovery evidence remain
outside Git/RAG.

The Core configuration check and authorized restart succeeded. Voice 0.6.0's
seventeen files and all four app processes are unchanged; 0.6.1 stays staged.
Live voice/playback/TV testing remains held. Native auth storage changed during
authenticated use; byte equality is not claimed for that file. Original entry
metadata is preserved; an existing iCloud entry recovered from setup_error
to loaded after restart. No repair was applied to it. Source and narrow
recovery: [Family Hub](../services/home-assistant-family-hub/README.md).

Protected native pre/post backups `e26287b6` / `5dbf67bb` completed with HA,
database and all four apps, with no reported failures. That post-configuration
checkpoint followed calendar fixture cleanup. Protected backup `53259c99`
then completed after AnyList authentication, final selection/binding and shopping
fixture cleanup, with database/all four apps and no reported failures.
Export/offsite coverage and restore of this generation remain
unverified; HA backups do not back up the upstream AnyList account.

## Google Map marker stability: 2026-10-17

The owner reported the 1/2/H markers blinking roughly once per second. The
renderer rebuilt all map overlays and list rows for every HA update. The
deployed v1.0.3 repair keeps them keyed to selected locations, updates only
changed coordinates/radius/title/list/trails and prunes stale/unavailable
overlays. The owner refreshed/reloaded Map, watched for about 15 seconds and confirmed
the 1/2/H icons stay steady. Eighteen
JS/five Python checks and expanded real Chromium tests passed: five identical
updates caused no overlay position/layer writes, detaches or refits; changed
coordinates/radius/title/history, unavailable marker/circle pruning, recovery
and reopen passed. Layout/isolation and non-loopback HTTP checks also passed.
The renderer HTTPS hash is exact; previous routes are retained. Native
readback confirms only primary/legacy renderer URLs changed, with every other
field/key, fallback/resources/dashboard metadata exact; parent v1.0.2, accepted
wheel/closer zoom, Hybrid/height, key, native fallback, permissions, quotas
and budget stay unchanged. Prior renderer v1.0.2 is retained for narrow
URL-only rollback after current-state and unchanged-field/key checks.

## Google Map wheel and closer zoom: 2026-10-17

Renderer v1.0.2 enables ordinary wheel zoom without Ctrl and raises the zoom
ceiling from 18 to 22. Hover over the map to zoom; sidebar and location-list
scrolling stay normal. Actual Nimo metadata showed zoom 20 and visibly closer
imagery beyond the old limit. The owner tested wheel up/down without Ctrl and
closer zoom and confirmed both work. Zoom 22 and physical image sharpness were
not established; closer detail depends on Google imagery and may be blurry.

The new immutable renderer hash matches HTTPS content. Previous v1.0.0/
v1.0.1 and all other Serve routes are preserved. Parent v1.0.2 is untouched.
Exact native checks confirmed only primary/legacy renderer URLs changed;
key, Hybrid default, height, fallback, permissions, resources, dashboard
metadata, quota and budget are unchanged. Five Python checks and JavaScript
syntax/build/diff checks passed. Narrow rollback changes only those URLs to
the recorded prior route through the protected API after current-state checks.

## Primary Google Map: 2026-10-17

The existing **Map** entry (`/map`) now uses Google Maps with a saved
**Hybrid** initial style. Native promotion and live Nimo Chrome are verified.
The original native map was copied exactly to `/map-fallback`,
shown as **HA Map (Fallback)** and linked by the Google card. Its original
access is retained; Google remains admin-only as in the trial. The legacy
`/google-maps-trial/trial` route stays compatible but is hidden from the sidebar.
EdSys Home remains the system landing dashboard.

Parent v1.0.2 adds validated saved-style/fallback-path settings. Loading stays
manual, and quota/budget/isolation/selected-location/history boundaries are
unchanged. The responsive card fills the available viewport height while
retaining HA navigation/header and map controls, without fullscreen. Eighteen JS and five Python checks pass.
Synthetic desktop/tall/phone/landscape browser checks verified viewport fill
within a 16px margin, retained controls, short-screen scrolling and one Map/SDK
across resizing. The primary promotion retained renderer v1.0.0; the later
interaction update above uses a new immutable version. The native API promotion verifies
the exact fallback before replacing `/map`, retains the key inside HA and compares
all resulting dashboard metadata. Dry-run/apply and exact primary/legacy/
fallback/resource readback passed; protected persisted checks confirmed the
same valid key, Hybrid default and fallback link.

Nimo `/map/0` showed Hybrid selected before loading, then real Hybrid imagery
filled the viewport to the footer in narrow and maximized windows while HA
header/sidebar/toolbar stayed visible. The in-card fallback rendered the
original native map at `/map-fallback/0`; sidebar Map returned to Google with
Hybrid selected. A fresh Hybrid load worked and was left open. No Cloud/key
change, HA/Pi restart, household action or voice change. The trial evidence
below describes the earlier installation.

Rollback restores only the original native Map configuration from the verified
current fallback and original Map metadata through the native API. Delete
only the created fallback if authorized. Use native API path `map-fallback`,
not a guessed storage filename (observed storage ID `map_fallback`); do not
restore whole HA storage, old
dashboard inventories or resource lists. See the owning
[Google Maps runbook](../services/home-assistant-google-maps/README.md).

## Google Maps trial: 2026-10-17

The owner-authorized separate `/google-maps-trial/trial` dashboard is installed
admin-only; the restricted key is now securely entered and natively saved.
It retains the original Map's six explicit locations and two-hour history.
At that trial-installation stage, the existing `/map` configuration, all other
dashboard metadata/resources, and nine protected HA files matched baseline.
Only one new module resource, its two immutable HA assets and one dedicated
9950x static renderer route were added. No device action or HA/Pi restart.

The transparent own-source card loads Google only on request. The SDK is
isolated from HA in a different HTTPS origin, credentialless sandboxed iframe;
the private renderer requires Tailscale and current Chromium. Road/Satellite/
Hybrid, Traffic, bounded selected trails, close/reopen, origin-only referrer,
parent/storage isolation and narrow layouts passed offline browser checks with
a fake SDK, including the deployed HTTPS renderer. Fourteen JavaScript
regressions and three Python build/deployment checks pass. A non-loopback HTTP
synthetic browser check passed CSPRNG startup and credentialless isolation.
Deployed parent v1.0.1 replaces secure-context-only randomUUID with a 128-bit
getRandomValues hexadecimal nonce before load reservation; failures reserve
no load and create no iframe. Only the module resource URL changed to the new
immutable parent path; exact current-resource readback and comparisons confirmed
Map configuration, dashboard metadata, trial configuration and private key/
configuration digest unchanged. Renderer v1.0.0 stays unchanged.

Live signed-in Nimo UI and screenshots verified actual Road/Satellite/Hybrid
imagery, three available markers (two people and Home), three honestly listed
unavailable trackers and visible two-hour trails. Traffic/Fit were invoked;
Close removed the renderer, and Existing map opened the original native Map
with its live map working. The correct Cloud project's Maps JavaScript API
metrics showed Requests 1 and Errors — (no errors reported).

Live Nimo Chrome verification confirmed the active dedicated **EdSys HA Maps
Trial Billing** account, existing personal payment profile and **EdSys HA Maps
Trial as its sole linked project**. Reopened persisted **HA Maps Trial - $5
monthly alerts** uses calendar Monthly, Specified amount USD 5, **All projects
(1) / All services** on that account, deliberately broader than a project filter.
Actual alerts are 50/80/100% ($2.50/$4/$5), forecast 100% ($5); billing
administrator/user role emails are on, Pub/Sub and Monitoring channels off.
Promotional credits are excluded; free-tier/other discount credits retained.
The budget list shows cap status **Not applicable**: these are **alerts only**.
Only Maps JavaScript API is enabled. The one **EdSys HA Maps Renderer** key
was saved/reopened with Websites restricted to the exact renderer HTTPS origin,
no path/wildcard, and API restriction to Maps JavaScript API only. Effective
quotas are **100 map loads/day**, **10/minute**, **0 3D map loads/day** and
**0 Grounding Widget loads/day**. These request limits are not a dollar cap.
Key entry and native save used ephemeral Nimo Windows UI Automation, with no
clipboard transfer or credential export; no private origin or key is published.
Nimo Windows app control works; no CUA browser was required. Owner preference
acceptance, physical GPS accuracy, alert-email delivery and final billed spend
were not verified.
At that trial stage, Existing map linked the unchanged native map. The primary
promotion above relocates that exact fallback to `/map-fallback`.
Setup, privacy, limits and narrow recovery:
[Google Maps trial runbook](../services/home-assistant-google-maps/README.md).

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
