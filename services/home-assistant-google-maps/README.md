# Home Assistant Google Maps

Status, 2026-10-17: Google Maps is now the primary **Map** entry at `/map`,
with **Hybrid** saved and selected on fresh navigation. Native promotion and
real Nimo Chrome imagery are verified. The original native map is retained
exactly at `/map-fallback`, titled **HA Map (Fallback)** in the sidebar and
linked from the Google card. Google keeps the trial's admin-only access; the
native fallback keeps the original native map's access policy. The hidden
legacy `/google-maps-trial/trial` route remains compatible. This changes the
Map entry, not the system-wide EdSys Home landing dashboard.

Dedicated billing, the persisted $5 alert budget, restricted browser key,
quotas and native HA key save were verified in the preceding trial. Live Nimo
trial imagery was verified before this primary-map promotion. Loading Google
remains manual through **Load Google map**; Hybrid is its saved initial style.

## Marker blinking repair: 2026-10-17

The owner reported the 1/2/H markers blinking about once a second. Renderer
`draw()` was detaching and rebuilding every marker, circle, trail and list
row for every HA update, including updates unrelated to selected locations.
Deployed renderer **v1.0.3** keeps overlays and list rows keyed to
the selected locations. It changes coordinates, radius, title, list content
and trail paths only when their data changes, and removes stale/unavailable
overlays. The HTTPS content hash matches source, and only the new versioned
route was added; v1.0.0/v1.0.1/v1.0.2 and every other Serve route remain.
After refreshing Map and loading Google once, the owner watched the 1/2/H
icons for about 15 seconds and confirmed they stay steady.

Eighteen JavaScript and five Python checks pass. Expanded real Chromium
checks with an intercepted SDK confirmed stable overlays across five identical
messages: no position/layer write, detach or refit. Changed position/radius/
title/history updates, pruning, unavailable marker/circle removal, recovery
and reopen passed, with layout/isolation retained. The non-loopback HTTP suite
also passed; these synthetic checks do not replace the owner's live visual
acceptance.

The update added immutable `/v1.0.3/renderer.html` at the same origin and
changed only primary/legacy renderer URLs through the protected HA API.
Native apply/readback confirmed every other card field and the key, fallback,
resources and dashboard metadata exactly unchanged. Parent v1.0.2, owner-accepted wheel/closer zoom, saved Hybrid, height,
key, fallback, permissions, quotas and budget stay unchanged. Retain prior
renderer routes; narrow repair rollback changes only the two renderer URLs
back to v1.0.2 after current-state and unchanged-key/field checks.

## Mouse-wheel and closer zoom: 2026-10-17

Ordinary wheel zoom now works without Ctrl. Hover over the Google map and use
the wheel to zoom; HA's sidebar and the renderer's location list keep normal
scrolling. Renderer **v1.0.2** uses `gestureHandling: "greedy"` and raises the
maximum zoom ceiling from 18 to 22. The actual Nimo browser reached zoom 20
and visibly closer imagery beyond the old limit; the owner tested both
wheel directions without Ctrl and closer zoom and confirmed **both work**.
Zoom 22 was not demonstrated. Available closer detail depends on Google's
imagery; enlargement may be blurry and does not create additional source detail.
[Google's interaction guide](https://developers.google.com/maps/documentation/javascript/interaction)
describes `greedy` gesture handling.

The verified wheel release uses immutable `/v1.0.2/renderer.html` on the same dedicated
port 8450 origin. Its HTTPS content hash is verified; v1.0.0/v1.0.1 and all
other Serve routes are preserved. Parent v1.0.2 is untouched. Only
`renderer_url` changed in the primary and hidden legacy Google cards, inside
HA. Exact comparisons confirmed all other card fields and the key unchanged,
plus unchanged fallback, resources and dashboard metadata. Hybrid default,
responsive height, permissions, budget, quotas and API restrictions are
unchanged. Five Python checks, JavaScript syntax, generated build and diff
checks passed; the owner's Nimo acceptance confirms actual interaction.

For narrow interaction rollback, compare current primary/legacy configuration
first, then change only their renderer URL to the recorded prior route
through the protected native API (v1.0.1 retains ordinary wheel zoom, v1.0.0
restores the original interaction settings). Confirm all other fields and the
key stay unchanged. Leave the new file/route retained unless its removal is
separately authorized; never reset Serve or restore an entire dashboard/
resource inventory.

## Scope and architecture

The native custom card sends only six explicitly selected existing Map
locations and bounded two-hour HA history to a small transparent renderer.
It supports Road, Satellite, Hybrid, optional Traffic, and Fit locations.
Tracking, history authority and permissions remain in HA. Imagery cannot improve
phone GPS accuracy. No new integration, HACS package, household action, HA/Pi
restart, geocoder, weather lookup, Places, Routes or Street View is added.

The initially reviewed upstream community card was heavily obfuscated and
included additional coordinate lookups. It was not installed. This own-source
trial instead uses the official Google Maps JavaScript SDK, quarterly channel,
modern Advanced Markers, raster imagery and the official test `DEMO_MAP_ID`.

HA serves the two deployed immutable v1.0.2 parent assets at
`/local/edsys-google-maps/v1.0.2/`: `core.js` and
`edsys-google-maps-trial.js`. Only the latter is a Lovelace module resource.
The v1.0.1 fix retained in v1.0.2 replaces secure-context-only
`crypto.randomUUID()` with a 128-bit `crypto.getRandomValues()` nonce encoded
as hexadecimal, so the existing HTTP LAN HA route can start the trial. Nonce generation precedes
browser load reservation and fails closed with a useful status if unavailable;
no weak randomness or isolation fallback is allowed. The active renderer is
v1.0.3; earlier immutable versions remain available for rollback. Parent
v1.0.2 adds validated `default_map_type` and
`fallback_path` configuration: the promoted Map and legacy route save
`hybrid` and `/map-fallback`. A style change applies to the current instance;
a fresh card starts from the saved Hybrid default. The responsive parent uses
the available viewport height instead of leaving a large empty lower panel;
HA navigation/header and map controls remain accessible, without fullscreen.
Desktop, tall, phone and landscape synthetic browser checks passed; short
screens scroll rather than clipping controls/footer. Real Nimo Chrome
verified the promoted view in narrow and maximized windows: Hybrid imagery
fills the viewport to the footer with HA header/sidebar and toolbar visible.
Google's SDK runs on a **different HTTPS origin**, served by the existing 9950x
Tailscale daemon at dedicated port **8450**, path `/v1.0.3/renderer.html`.
Each immutable renderer version is one static file; no new daemon, container,
HA API proxy or CORS grant.

The iframe is sandboxed and credentialless. Distinct origins isolate the HA
DOM/auth storage; credentialless mode also excludes existing hostname cookies
and storage shared by other ports. Exact frame-window, origin, channel and
nonce checks apply in both directions. The native HA object, entity IDs,
credentials and service access are never passed to the renderer. Selected
labels, states, coordinates, accuracy/zone circles and trail points are passed;
Google SDK therefore can see these selected display fields. No blanket state
export or external reverse geocoding occurs.

This pilot requires **current Chromium (Chrome/Edge) and Tailscale on the
viewing device**, including Nimo. Unsupported browsers fail closed with the
native fallback available. The renderer cannot work when 9950x/Tailscale is
unreachable. The ordinary HA map does not acquire that dependency. Browser
access to HA itself may still use its existing LAN or remote route.

## Owner Google Cloud setup

Do this in the owner's signed-in [Google Cloud console](https://console.cloud.google.com/).
Live Nimo Chrome verification on 2026-10-17 confirmed the active dedicated
billing account **EdSys HA Maps Trial Billing**, using the existing personal
payment profile, with **EdSys HA Maps Trial as its sole linked project**.
The persisted budget **HA Maps Trial - $5 monthly alerts** was reopened and
verified: calendar Monthly, Specified amount **USD 5**, **All projects (1) / All
services** across this dedicated account, deliberately broader than a project
filter. Actual-spend alerts are **50/80/100%** ($2.50/$4/$5), with forecast
**100%** ($5). Billing administrator/user role emails are on; Pub/Sub and
Monitoring channels are off. Promotional credits are excluded from budget
calculations; free-tier and other discount credits are retained. These are
**alerts only**; the budget list shows cap status **Not applicable**. Nimo's
Windows app control works; no CUA browser was required for setup or acceptance.

Google Cloud configuration is now saved and reopened for verification:

- **Maps JavaScript API is the only enabled Maps service.**
- The one dedicated key, **EdSys HA Maps Renderer**, has **Websites** application
  restriction to the exact renderer HTTPS origin, including its port, with
  **no path or wildcard**; its API restriction allows **Maps JavaScript API
  only**. The private origin remains outside Git/RAG and appears in the native
  card editor. The loader retains `auth_referrer_policy=origin`.
- Effective project quotas read back as **100 map loads/day**, **10 map
  loads/minute**, **0 3D map loads/day** and **0 Grounding Widget loads/day**.
  These are observed project limits, not an assumed public default or a dollar
  spending cap.
- The key was entered securely and saved through Nimo's native HA editor using
  ephemeral Windows UI Automation, with no clipboard transfer or credential
  export. It remains in HA's private dashboard configuration, never static
  assets, helpers, source or documentation. It is a restricted browser
  credential, not a server-side secret; dashboard administrators and authorized
  clients can access it.

During the preceding trial, signed-in Nimo HA **Load Google map** displayed
actual Road,
Satellite and Hybrid imagery, **three available markers** (two people and Home),
**three unavailable trackers honestly listed**, and visible two-hour trails.
Traffic and Fit were invoked; Close removed the renderer. Existing map opened
the original native Map and its live map rendered. The correct Cloud project's
Maps JavaScript API metrics reported **Requests: 1**, with **Errors: —** (no
errors reported); this is not a billed-spend measurement.

Never remove restrictions as an error workaround. Disable/delete the key and
review usage if the trial is abandoned or unexpected usage appears. Never
paste a key/payment detail into chat, Git, screenshots or a document.

**A $5 budget is an alert, not a spending cap.** Google Maps is not listed as
eligible for Cloud's spend-cap budgets. The trial additionally requires a
manual load and limits starts to 20/day/browser in local storage. That guard
is best effort: separate browsers/devices/tabs, cleared storage, altered code
or stolen keys can bypass it. It never proves a billing hard limit. There is
no automatic retry or periodic reconstruction of the Map; state/style/traffic
updates reuse the current Map. Close destroys the frame. History refresh is
every five minutes while visible.

Current published Dynamic Maps pricing is 10,000 free monthly loads, then
$7/1,000 in the first paid tier, subject to account/region/terms. Eligible free
usage is aggregated across projects on the billing account. Check the actual
account pricing; do not infer guaranteed free use or an automatic $5 stop.

## Installation and verification

Owning control route: [HA privileged operator route](../../docs/HOME_ASSISTANT_CONTROL.md).
Review current HA/Tailscale state before any change. Deployment scripts refuse
unexpected trial metadata, an occupied port or altered immutable files; an
uncertain write is inspected rather than replayed. The owner-private checkpoint contains
the selected entity IDs, before-configurations, created IDs and renderer URL.
Never publish those full configurations or recovery files. The initial-creation
commands below refuse an existing trial; they are not an update procedure for
the promoted instance. Parent updates install a new immutable version and
change only this resource's URL after checking current metadata.

```bash
python3 build.py
npm test
python3 -m unittest discover -s tests -p 'test_*.py' -q
python3 deploy_renderer.py --private-state /PRIVATE/CHECKPOINT
python3 deploy_renderer.py --private-state /PRIVATE/CHECKPOINT --apply
python3 deploy.py --private-state /PRIVATE/CHECKPOINT
python3 deploy.py --private-state /PRIVATE/CHECKPOINT --apply
```

`build.py` regenerates `renderer.html` exactly from its template and readable
`renderer.js`. `deploy_renderer.py` serves only that static file through
Tailscale Serve, with no Funnel. Its runtime file is private outside Git under
the checkpoint's sibling `renderer/v1.0.3/` for a fresh installation. The
initial-install script refuses occupied port 8450; the wheel update adds only
the new versioned path and retains the old route. Port 8450 is a dedicated origin;
never host HA or another authenticated application on it.

Eighteen JavaScript projection/isolation/load-guard/nonce/style/layout
regressions and five Python configuration/build/promotion checks pass. The
nonce checks cover a crypto object without `randomUUID` and prove missing/throwing CSPRNGs reserve no load and
create no iframe. A real **non-loopback HTTP** synthetic browser check also
passed CSPRNG startup and credentialless parent/storage isolation; localhost
alone would not detect the secure-context regression.
CLI-based browser checks (`tests/browser-check.js`) use only
synthetic locations and an intercepted fake SDK: no Google request or charge.
They verify parent access denial, no regular cookies/storage, origin-only SDK
referrer, bounded history rendering, one Map across updates, controls,
close/reopen, saved Hybrid/fallback settings and desktop/tall/phone/landscape
layouts. The viewport-fill checks allow a 16px outer margin, retain controls,
permit short-screen scrolling and confirm resizing reuses one Map/SDK.
Earlier renderer isolation/referrer checks also passed with the **deployed
HTTPS renderer**. Nimo's native HTTP client reached that
renderer with expected content; this does not prove its signed-in HA browser
or live Google imagery. Initial native resource/dashboard/configuration readback
matched the empty-key installation baseline. Subsequent native key save and the
live Nimo browser workflow above were verified with native UI and screenshots.
The earlier activation deployed v1.0.1 to a new immutable parent path and
changed only its module resource URL; exact expected-resource readback and
comparison confirmed original Map configuration, dashboard metadata, trial configuration and private
key/configuration digest unchanged. Renderer v1.0.0 was not modified. The
operator API masks `api_key` fields; protected comparisons stay inside the
guest and export no credential.

### Primary Map promotion

Parent v1.0.2 and `promote.py` implement the owner-authorized primary-map change.
The verified deployment used a new immutable parent path and changed only the
existing module resource URL; renderer v1.0.0 and other resources were unchanged.
After installing the new immutable parent files and updating only the existing
module resource URL, run a fresh native preflight and inspect its private
receipt before applying:

The private checkpoint must already contain current `native-before.json`
(the original `/map` configuration) and `dashboards-before.json` (native
dashboard metadata), obtained through the protected operator route. Do not
include the Google configuration or key in those recovery snapshots.

```bash
python3 promote.py --private-state /PRIVATE/PROMOTION_CHECKPOINT
python3 promote.py --private-state /PRIVATE/PROMOTION_CHECKPOINT --apply
```

The promotion requires the expected native/Google card types, matching selected
locations (six in this deployment) and two-hour history, a configured key and the new parent
resource. It requires the private native/dashboard recovery snapshots to
hash-match the preflight, and refuses an existing fallback or changed
preflight. It creates and verifies the exact native fallback before changing `/map`; the key stays
inside HA. The expected metadata changes are confined to the primary Map,
the hidden legacy trial and the new native fallback. An uncertain attempt is
inspected without replay. Dry-run/apply succeeded. Native readback confirmed
exact primary/legacy configurations, exact native fallback, intended dashboard
metadata and unchanged resources. Protected persisted checks confirmed the
same valid key, saved Hybrid default and `/map-fallback` link without exporting
the key.

Nimo full navigation to `/map/0` showed Hybrid selected before loading. Real
Hybrid imagery filled the viewport while HA navigation/header and the toolbar
remained visible in narrow and maximized windows. The in-card fallback opened
the original native map at `/map-fallback/0`; sidebar Map returned to the new
Google card with Hybrid still the saved default. A fresh real Hybrid load
worked and was left open. No Cloud/key restriction change, HA/Pi restart,
household-device action or voice change was needed.

Physical GPS accuracy, alert-email delivery and final billed spend remain
unverified.

## Backup and narrow recovery

Source is sanitized/rebuildable. Back up the private checkpoint, HA's native
dashboard/resource configuration, and the versioned renderer Serve routes
through the
existing protected backup process. No separate runtime database or volume.
Fresh offsite/export/restore acceptance is not established by this trial.

To reverse the primary-map promotion, first inspect the current `/map`,
`/map-fallback`, hidden legacy configuration, module resource and the private
promotion receipt. Restore the original native configuration from the verified
current fallback to `/map` through the native API, together with only the saved
original Map metadata. Use the native API path `map-fallback`; do not infer
its storage filename from the path (the observed storage ID is `map_fallback`).
Confirm exact readback before removing anything. Remove
only the fallback created by this promotion if that removal is authorized and
no link still needs it. Restore only this card's earlier module URL if a
parent-version rollback is needed; do not restore the entire resource list.

If authorized to retire Google entirely, remove only the recorded Google
dashboard/resource references after the native `/map` is restored. Retain or
remove the versioned HA files only after checking references. If the port 8450
Serve handlers still exactly match the recorded renderer routes, run
`tailscale serve --https=8450 off`; verify every other route matches its
before-state. **Never run `tailscale serve reset` or restore entire HA storage
or old dashboard inventories.** Retaining static files is harmless.
Disable/delete the dedicated Google key and review usage/billing separately;
removing a dashboard does not revoke a Google key or cancel an account.

## Primary references

- [Google API key security and website restrictions](https://developers.google.com/maps/api-security-best-practices)
- [Map gesture handling and wheel zoom](https://developers.google.com/maps/documentation/javascript/interaction)
- [Maps JavaScript loader](https://developers.google.com/maps/documentation/javascript/load-maps-js-api)
- [Usage and billing](https://developers.google.com/maps/documentation/javascript/usage-and-billing)
- [Current prices](https://developers.google.com/maps/billing-and-pricing/pricing)
- [Cost management](https://developers.google.com/maps/billing-and-pricing/manage-costs)
- [Cloud spend-cap eligible services](https://docs.cloud.google.com/billing/docs/how-to/budgets-spend-caps#services-that-are-eligible-for-spend-cap-budgets)
- [Credentialless frame behavior](https://developer.chrome.com/blog/iframe-credentialless)
