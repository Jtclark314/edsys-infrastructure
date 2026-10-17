# Home Assistant Google Maps trial

Status, 2026-10-17: separate admin-only `/google-maps-trial/trial` dashboard
installed; dedicated billing, the persisted $5 alert budget, restricted browser
key, quotas and native HA key save are verified. Parent assets **v1.0.1 are
deployed**, and the actual Google Maps trial is **browser-verified on Nimo**.
The original `/map` configuration is unchanged and its live map still works.

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

HA serves the two immutable parent assets at
`/local/edsys-google-maps/v1.0.1/`: `core.js` and
`edsys-google-maps-trial.js`. Only the latter is a Lovelace module resource.
The v1.0.1 fix replaces secure-context-only `crypto.randomUUID()` with a
128-bit `crypto.getRandomValues()` nonce encoded as hexadecimal, so the
existing HTTP LAN HA route can start the trial. Nonce generation precedes
browser load reservation and fails closed with a useful status if unavailable;
no weak randomness or isolation fallback is allowed. The renderer stays at
v1.0.0 unchanged.
Google's SDK runs on a **different HTTPS origin**, served by the existing 9950x
Tailscale daemon at dedicated port **8450**, path `/v1.0.0/renderer.html`.
It is one static file; no new daemon, container, HA API proxy or CORS grant.

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

In the signed-in Nimo HA trial, **Load Google map** displayed actual Road,
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
existing trial metadata, occupied port or altered immutable files; an uncertain
write is inspected rather than replayed. The owner-private checkpoint contains
the selected entity IDs, before-configurations, created IDs and renderer URL.
Never publish those full configurations or recovery files.

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
the checkpoint's sibling `renderer/v1.0.0/`. Port 8450 is a dedicated origin;
never host HA or another authenticated application on it.

Fourteen JavaScript projection/isolation/load-guard/nonce regressions and three
Python configuration/build checks pass. The nonce checks cover a crypto object
without `randomUUID` and prove missing/throwing CSPRNGs reserve no load and
create no iframe. A real **non-loopback HTTP** synthetic browser check also
passed CSPRNG startup and credentialless parent/storage isolation; localhost
alone would not detect the secure-context regression.
CLI-based browser checks (`tests/browser-check.js`) use only
synthetic locations and an intercepted fake SDK: no Google request or charge.
They verify parent access denial, no regular cookies/storage, origin-only SDK
referrer, bounded history rendering, one Map across updates, controls,
close/reopen, fallback and 390/1280-pixel layouts. The same checks also passed
with the **deployed HTTPS renderer**. Nimo's native HTTP client reached that
renderer with expected content; this does not prove its signed-in HA browser
or live Google imagery. Initial native resource/dashboard/configuration readback
matched the empty-key installation baseline. Subsequent native key save and the
live Nimo browser workflow above were verified with native UI and screenshots.
Activation deployed v1.0.1 to a new immutable parent path and changed only its
module resource URL; exact expected-resource readback and comparison confirmed
original Map configuration, dashboard metadata, trial configuration and private
key/configuration digest unchanged. Renderer v1.0.0 was not modified. The
operator API masks `api_key` fields; protected comparisons stay inside the
guest and export no credential.

Owner preference acceptance, physical GPS accuracy, alert-email delivery and
final billed spend were not verified.

## Backup and narrow recovery

Source is sanitized/rebuildable. Back up the private checkpoint, HA's native
dashboard/resource configuration, and this single Serve route through the
existing protected backup process. No separate runtime database or volume.
Fresh offsite/export/restore acceptance is not established by this trial.

To remove an authorized trial, read `deployment.json` and the current trial
configuration first. Delete only the recorded Google Maps Trial dashboard and
the matching trial module resource through native APIs/UI. Retain or remove
only the active version's two HA assets after confirming no remaining resource
references; keep older immutable assets available for narrow rollback.
If the port 8450 Serve handler still exactly matches the recorded one-file
route, run `tailscale serve --https=8450 off`; verify every other route matches
its before-state. **Never run `tailscale serve reset` or restore entire HA
storage/old dashboard inventories.** Retaining the static file is harmless.
The original map requires no restoration. Disable/delete the dedicated Google
key and review usage/billing in the owner's console separately; removing a
dashboard does not revoke a Google key or cancel an account.

## Primary references

- [Google API key security and website restrictions](https://developers.google.com/maps/api-security-best-practices)
- [Maps JavaScript loader](https://developers.google.com/maps/documentation/javascript/load-maps-js-api)
- [Usage and billing](https://developers.google.com/maps/documentation/javascript/usage-and-billing)
- [Current prices](https://developers.google.com/maps/billing-and-pricing/pricing)
- [Cost management](https://developers.google.com/maps/billing-and-pricing/manage-costs)
- [Cloud spend-cap eligible services](https://docs.cloud.google.com/billing/docs/how-to/budgets-spend-caps#services-that-are-eligible-for-spend-cap-budgets)
- [Credentialless frame behavior](https://developer.chrome.com/blog/iframe-credentialless)
