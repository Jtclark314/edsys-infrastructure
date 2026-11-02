# Capability qualification

This is the staged 0.60.10 expansion qualified on 2026-11-01. Production and
recovery acceptance belong to the commissioning record. No personal watches,
notification destinations or browser accounts are created by installation.

| Capability | Evidence and practical status |
| --- | --- |
| Server-rendered HTML/text | Real ALPHA to BETA fetch, two saved snapshots and text diff |
| JavaScript sites | Chromium executed delayed page updates; two returned histories |
| CSS, XPath, JSONPath, XML | Real filtered JSON and XML changes; CSS-filtered form result |
| PDF monitoring | Native text extraction returned both document revisions; scanned/encrypted PDFs may need preparation in Stirling |
| Browser automation | Saved enter-text/click steps produced changed form output; native live editor also returned JPEG previews and selector maps |
| Screenshot/visual comparison | Native image_ssim_diff produced two distinct PNG history revisions with selected crop and pixel sensitivity; REST settings and native UI Save/reopen round-tripped |
| Interactive element/area selection | Visual processor is enabled in the UI; selector metadata and crop configuration are available with the dedicated browser |
| Prices and stock | Structured Product/Offer fixture changed price 10 to 20 and InStock toOutOfStock, preserving both snapshots |
| Local AI | Actual native client produced a concise synthetic change summary; one CPU-only request, bounded wire options and immediate model unload verified |
| REST/MCP | Native API key required; complete live schema includes screenshot mode/settings. The private MCP bridge separately passed text/browser/visual workflows |
| Schedules, tags, filters, triggers | Native controls retained; paused/muted disposable watches and tags used for qualification. Every possible filter/schedule combination is not separately certified |
| History, screenshots, comparisons, RSS | Native history/screenshots exercised; private authenticated/share-token behavior retained. Public shared-diff access stays off |
| Notifications | Native Apprise transports retained. No real destination/account configured; HTTP(S) delivery uses checked egress. Raw SMTP/other direct TCP transports need a separately selected relay/design |
| Login flows | Fresh isolated browser contexts support saved login/form steps and explicit request credentials. No real account login was performed |

Public HTTPS, a synthetic private LAN server and another Tailnet node's ordinary
HTTPS login page were fetched with both engines. Own-host Tailnet/Serve routes
are deliberately excluded. Private address permission does not grant access to
loopback, metadata, this stack's control endpoints or Docker management.

The native UI/API handles the monitor configuration and history. The local MCP
bridge exposes that API, including browser steps, images and visual settings;
it does not attach to a personal browser or bypass website authentication.
There is no CAPTCHA-solving service, stolen session reuse, paid proxy, cloud AI
or automatic signup. A target that demands one of those may remain unavailable.

Visual comparison follows upstream pixel/sensitivity rules, not semantic image
understanding. The red/blue fixture required sensitivity 20 because its grayscale
pixel difference was below medium sensitivity 80; choose thresholds for the page.
PDF text monitoring does not provide OCR of arbitrary scanned documents.
Screenshots/browser sessions and downloads are bounded to protect the host.

AI is optional per watch. Summary/intent/extraction controls use the local-only
native client; application call-site inspection found all built-in inference
paths converge on the guarded client, including its spawned connection tester.
The focused real test qualifies a local summary and its resource/transport
behavior, not every prompt's accuracy or every site's product extraction.
Existing raw detection remains the source evidence when AI is disabled, busy,
out of budget, timed out or mistaken.

Negative qualification covered nested images/fetch requests, WebSocket upgrade
and CONNECT attempts, HTTP/JavaScript redirects, TLS failures and raw browser
sockets. Dummy Authorization/custom/Cookie headers reached the original fixture
origin and did not reach its foreign subresources or redirected destination;
a 307 body was blocked before crossing origins. Browser/app networks remain
separate even outside the browser's HTTP proxy settings.

Private receipts include the test fixture source, raw acceptance JSON, native UI
preview images, model wire evidence, immutable image/source checks and package
inventory. These artifacts contain private addresses or synthetic session state
and are kept outside Git. Production rollback must retain the matching cold
state, definitions and image archives; candidate success alone is not a recovery
or production access claim.
