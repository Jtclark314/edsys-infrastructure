# Home Assistant Google Maps trial

Status, 2026-10-17: separate admin-only `/google-maps-trial/trial` dashboard
installed with an **empty key**. Dedicated Google Cloud billing and the
persisted $5 monthly alert budget are verified. Maps JavaScript API enablement,
the restricted key, quotas and real Google imagery acceptance remain pending.
The existing `/map` dashboard remains exactly as before this change.

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

HA serves two immutable files at
`/local/edsys-google-maps/v1.0.0/`: `core.js` and
`edsys-google-maps-trial.js`. Only the latter is a Lovelace module resource.
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
Windows app control works;
no CUA browser is required for the remaining setup.

The owner handles sign-in, payment details and key entry directly. Never paste
a key/payment detail into chat, Git, screenshots or a document. Remaining steps:

1. Enable **Maps JavaScript API only** for this pilot. Do not enable Places,
   Routes, Geocoding or Street View APIs just because a setup wizard suggests
   them. No service-account key is needed.
2. In Maps JavaScript API → Quotas & System Limits, inspect the **actual editable
   quotas**. Record any applied changes and read them back. If a daily map-load
   quota is offered, 100/day is a conservative trial target; do not assume it
   exists. Current public documentation lists 30,000 loads/min/project and
   300/min/IP and does not promise an adjustable daily quota. A rate quota
   alone does not implement a monthly dollar cap.
3. Create a dedicated **browser API key**. Set application restriction to
   **Websites**, and authorize exactly the **renderer origin**, including its
   HTTPS port. Copy that origin from this card's native visual editor, not
   HA's address bar. The loader uses `auth_referrer_policy=origin`; avoid a
   path-specific restriction and broad domain wildcards. Set API restriction
   to **Maps JavaScript API only**. Save and inspect both restrictions before
   entering the key. The renderer's private URL is not a source-file constant.
4. In HA's Google Maps Trial dashboard, Edit dashboard → Edit card, enter the
   key in the password field and save. Keep the key in HA's private dashboard
   configuration. The native frontend must use it, so it is a **restricted
   browser credential, not a server-side secret**. Dashboard administrators and
   authorized clients can access it. No key goes in static assets or helpers.
5. Press **Load Google map**. Check actual imagery and all selected markers,
   Road/Satellite/Hybrid, Traffic, Fit, two-hour trails where recorder data
   exists, Close and the Existing map link. Check the Google console for
   successful restricted-key traffic, usage attribution and quota settings.
   Remove/disable the key promptly if the trial is abandoned or unexpected
   usage appears. Never remove restrictions as an error workaround.

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

Ten projection/isolation/load-guard regressions and three configuration/build
checks pass. CLI-based browser checks (`tests/browser-check.js`) use only
synthetic locations and an intercepted fake SDK: no Google request or charge.
They verify parent access denial, no regular cookies/storage, origin-only SDK
referrer, bounded history rendering, one Map across updates, controls,
close/reopen, fallback and 390/1280-pixel layouts. The same checks also passed
with the **deployed HTTPS renderer**. Nimo's native HTTP client reached that
renderer with expected content; this does not prove its signed-in HA browser
or live Google imagery. Native HA resource/dashboard/configuration readback
matched, including the persisted empty key. The operator API masks even empty
`api_key` fields, so the installer compares persisted initial config inside
the guest and returns only a boolean; no credential is exported.

## Backup and narrow recovery

Source is sanitized/rebuildable. Back up the private checkpoint, HA's native
dashboard/resource configuration, and this single Serve route through the
existing protected backup process. No separate runtime database or volume.
Fresh offsite/export/restore acceptance is not established by this trial.

To remove an authorized trial, read `deployment.json` and the current trial
configuration first. Delete only the recorded Google Maps Trial dashboard and
the matching new module resource through native APIs/UI. Retain or remove only
the two versioned HA assets after confirming no remaining resource references.
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
