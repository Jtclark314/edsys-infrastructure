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
