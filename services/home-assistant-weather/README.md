# Home Assistant weather

HA owns the weather entities, provider polling and Weather dashboard. The
existing AcuRite station supplies observations through Weather Underground;
NWS supplies regional forecasts and alerts, and NOAA supplies observed radar.
Private station identity, coordinates, credentials, entity bindings, recovery
copies and API responses stay outside this repository.

## Pinned components

| Component | Version | Role |
| --- | --- | --- |
| Wunderground PWS | 2.3.0, with reviewed local patch | Station measurements and Weather Underground daily forecast |
| NWS | Native HA integration | Hourly forecast for the station location |
| NWS Alerts | 6.7.3, with reviewed local patch | NWS-reported alerts for the selected location/area |
| Weather Radar Card | 3.13.0 stable | NOAA radar loop; native HA map tiles |

`pins.json`, `nws-pins.json` and `radar-pins.json` record immutable public
revisions and hashes. The integration patches fix failure handling and missing
observations; the radar assets are unchanged official release files.

- WU errors report fixed messages/status/type without credential-bearing URLs,
  provider error bodies or exception chains. HTTP 401 initiates native reauth.
- Missing solar, UV, humidity and direction remain unknown; a measured zero
  remains zero. Sensor availability follows coordinator failure.
- A timestamp sensor exposes the provider's `obsTimeUtc`, independent of HA's
  poll time. The dashboard warns when that observation is stale or unavailable.
- NWS transport, malformed JSON and partially malformed alert lists fail as
  unavailable. A valid empty feature list remains zero reported alerts.

WU requests both observations and daily forecast every five minutes, even
with individual forecast sensors disabled: roughly 576 HTTPS requests/day,
plus setup/refreshes. Provider entitlements and quotas remain account-specific.
Its two requests are coupled: forecast failure can make the WU entities
unavailable as well. NWS hourly forecast is an independent source; an automatic
merged forecast/failover service is not configured.

## Prepare and qualify public packages

Use a new private output directory. The tools verify upstream archives/trees,
reject unsafe members and drift, apply zero-fuzz patches, and emit file manifests.
They do not contact HA or handle credentials.

```sh
python3 prepare_wunderground.py --output /PRIVATE/NEW-WU-PACKAGE
python3 prepare_nws.py --output /PRIVATE/NEW-NWS-PACKAGE
python3 test_wunderground_patch.py --source /PRIVATE/VERIFIED-WU-CHECKOUT
python3 test_nws_patch.py --source /PRIVATE/VERIFIED-NWS-CHECKOUT
```

An offline `--source` can name a verified upstream checkout. Keep generated
packages, caches and receipts private. The focused checks exercise safe auth/
transport/JSON failure paths, missing values, observation timestamps and source
integrity. Actual HA startup, entities and forecast readback are separate checks.

## Install and configure

1. Preserve a fresh protected native HA backup, database and all installed apps.
   Capture existing configuration/voice hashes and relevant integration policies.
2. Install the exact upstream versions through HACS. WU may need custom repository
   `cytech/Home-Assistant-wundergroundpws`, category Integration. NWS Alerts is
   `finity69x2/nws_alerts`; the frontend is `jpettitt/weather-radar-card`.
3. Compare the installed packages against the pinned upstream manifests before
   any writes. Preserve narrow originals privately, then apply only the reviewed
   package changes. Verify every resulting file against the prepared manifests.
   Verify radar release assets against their pinned digests.
4. Run HA configuration validation and perform a normal Core restart during an
   authorized window. Preserve unrelated staged changes and native disabled
   integration policies.
5. Add Wunderground PWS through HA's native configuration flow, supplying the
   station ID and **PWS API key**, not the station-upload credential. Leave the
   experimental calendar-day option off. Use decimal precision; keep unnecessary
   individual forecast sensors off.
6. Add native NWS for the same location. Its API identifier is a descriptive
   User-Agent string; do not reuse the WU secret for that field.
7. Add NWS Alerts: `user` → `gps` → `gps_loc`, using a private `LAT,LON` string,
   one-minute interval and 120-second timeout. This uses NWS's point service;
   the integration performs no local polygon containment. Do not claim each
   returned warning polygon necessarily contains the house. A Z-only zone
   selection can omit county-based warnings.
8. Configure one radar resource (avoid duplicate `/hacsfiles` and `/local`
   registrations). `radar-pins.json` contains the reviewed NOAA configuration.
   HA MapTiles requires HA 2026.10+. Keep frame timestamps visible; radar is
   an observation loop, and fallback frames may be older.

HACS upgrades/reinstalls can overwrite local patches. Review upstream changes,
regenerate packages, repeat focused checks and verify installed hashes before
activating an update. Do not silently replace the maintained local packages.

## Dashboard and verification

`dashboard.py` builds native Lovelace JSON from private bindings. It uses native
weather, entities, headings and Markdown cards, plus the pinned radar card.

```sh
python3 dashboard.py /PRIVATE/bindings.json /PRIVATE/dashboard.json
```

Create/save the dashboard through HA's native Lovelace APIs, then read it back
exactly. Keep runtime entity IDs and map coordinates out of tracked fixtures.
Bindings distinguish WU current/daily weather, NWS hourly weather, the UTC
observation sensor, selected station measurements, alerts and optional radar.

Verify native integration state and plausible measurements; compare station
observation time with the direct provider response. Confirm unsupported UV/solar
remain unknown, actual daily/hourly forecasts are returned, and the alert entity
agrees with a successful provider query. Validate templates through HA, not only
offline Jinja. Check the real dashboard and radar in an authenticated browser.
Expose only selected read-only weather entities to Assist. Spoken voice and TV
presentation acceptance remain separate from data/dashboard verification.

## Manual Pi TV weather — October 17, 2026

**Installed; source/configuration verification passed.** The Pi dashboard extension
adds forecast (90 seconds) and radar (180 seconds) buttons in Player → On the TV, TV
screen and Household. These call only the existing
`edsys_voice_knowledge.bedroom_screen` service with `weather_forecast` or
`weather_radar`. The existing permission/identity/HDMI 1 gate applies; no implicit
TV power/input or playback change. Back/Dismiss or fixed expiry closes the view.
Live screen/voice/playback tests remain held.

Forecast uses the native WU daily forecast (up to five days), actual station
temperature/humidity/source observation time and NWS-reported alert count.
Observation age above 15 minutes is stale; unavailable values/counts stay unknown.
It uses the existing `briefing` card. Radar is fetched only on explicit request from
the fixed public [NOAA KGSP
loop](https://radar.weather.gov/ridge/standard/KGSP_loop.gif). The whole 600×550
source image includes legend, attribution and scan timestamps. `Last-Modified` must
be at most 20 minutes old; this is source-file freshness, not proof that every
historical radar scan is recent or a forecast.

HTTP is limited to 4 MiB/10 seconds, without redirects. Decoding runs in HA's
executor and limits images to 1024 pixels per dimension, 20 frames/8M total pixels,
and 32,000-byte JPEGs. Existing encrypted `camera_frame`/`large` messages advance
every two seconds with a fixed expiry. Identity, user read permissions and TV/Pi
selection are rechecked. Stale input or uncertain send fails without retry; same-key
clear plus the Pi SequenceGuard cannot clear a replacement view. There is no Pi
protocol/add-on change, listener, credential or cloud AI task. Preparation is
serialized with screen mutations; a slow failed replacement can let an existing
radar preview hit the Pi's six-second stale-frame guard and hide.

Private `bedroom_screen.json` gains an optional, separate `weather` object:
`version: 1`, fixed `radar_site: KGSP`, and exactly five source keys: `daily`,
`observed`, `temperature`, `humidity`, `alerts`. Bindings pin native entity/device/
entry/unique identity, platform and effective area; each caller needs read access.
The original four household sources remain intact. No URL, coordinates or key is
accepted as a weather setting.

For this selective rollout, preserve private originals/hashes and overlay only
`bedroom_screen.py`, `weather_tv.py`, `weather_display.py`; validate HA then use one
normal Core restart. Retain active voice 0.6.0, its original two-method RPC
allowlist and the other fourteen component files, including the manifest. The full
`install_knowledge.py` path includes staged 0.6.1 priority changes and is reserved
for a separate activation; its future full-package module list is not the selective
deployment procedure. Build a future priority activation from current main so it
carries this weather extension; do not replay the older staged bundle. Pre-change
protected native backup includes the database/four apps; post-change protected
backup `0cbf09b2` also completed with the database/all four apps.

Use [the Pi dashboard transformer/recovery
procedure](../home-assistant-pi-plex/README.md#manual-tv-weather-controls--october-17-2026)
against the latest live dashboard. Recovery restores the original
`bedroom_screen.py`, removes the two added helpers and reverts only the optional
weather configuration and owned dashboard additions after comparing current
hashes/readback; preserve later edits and Pi media state. Twelve helper, thirteen
dashboard and twenty-seven isolated native checks passed, with candidate device
networking denied. Native HA check/restart, exact source/dashboard readback and
authenticated controller layout passed; Pi PID, add-on/profile/media/pause/position
were unchanged. New archive export/offsite/ restore and physical TV acceptance
remain unverified.

## State, networking and recovery

- Private HA configuration lives in native `.storage` entries/registries.
  Polling uses outbound HTTPS 443; dashboard access uses the existing HA route.
  Radar/map tiles are fetched by the viewer's browser.
- The existing protected native backup includes HA configuration/database and
  all installed apps. Take a post-configuration backup to cover the new key and
  entries. Confirm actual backup metadata; offsite export/restore is a separate
  evidence requirement.
- Narrow recovery: remove only the newly added weather entries/dashboard and
  its exclusive radar resource through native UI/APIs; remove the corresponding
  HACS components when no remaining views reference them. Restore reviewed
  package originals only after comparing current hashes. A whole-VM rollback
  would also revert unrelated household state and is not the default repair.
- The station's existing upload configuration is independent and stays with
  the console. Never substitute its upload password for the PWS read API key.

Canonical current status: `EdSys-Master/docs/HOME_ASSISTANT_WEATHER.md`.

Primary references: [WU integration](https://github.com/cytech/Home-Assistant-wundergroundpws),
[native NWS](https://www.home-assistant.io/integrations/nws/),
[NWS Alerts](https://github.com/finity69x2/nws_alerts),
[radar card](https://github.com/jpettitt/weather-radar-card),
[NWS geography](https://www.weather.gov/media/documentation/docs/NWS_Geolocation.pdf).
