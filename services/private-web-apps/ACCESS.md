# Private application access

Tailscale Serve sends each private HTTPS route to its application access bridge:

| Application | Private HTTPS | Bridge | Application backend | Access |
| --- | --- | --- | --- | --- |
| Stirling PDF | `9950x.taile832fe.ts.net:8452` | `127.0.0.1:8142` | `10.254.230.50:8080` (dedicated Docker network) | Current devices in the configured tailnet, including tagged devices |
| changedetection.io | `9950x.taile832fe.ts.net:8453` | `127.0.0.1:8143` | `127.0.0.1:8141` | Exact configured owner identity |

Both bridges require the exact configured Host and HTTPS indication from a
loopback peer. changedetection retains the local-coder exact-owner boundary. Foreign
Origins and cross-site subresources or mutations are denied. User-initiated
top-level GET/HEAD navigation is allowed, including dashboard links. Browser
mutations and WebSocket upgrades require the exact HTTPS Origin. Duplicate gate headers, absolute-form
URLs, unknown methods, wrong routes and incomplete configuration fail closed.
Requests rejected by these gates never reach the application.

[Tailscale Serve](https://tailscale.com/kb/1312/serve#identity-headers) replaces
incoming identity headers with the authenticated Tailnet user's identity.
Tagged devices have no user identity and therefore remain denied at
changedetection. Stirling uses device membership instead of user identity:

- [Serve 1.104.4](https://github.com/tailscale/tailscale/blob/v1.104.4/ipn/ipnlocal/serve.go#L1144)
  replaces `X-Forwarded-For` with one authenticated source IP and replaces the
  forwarded Host/protocol. The proxy requires that single IP and exact forwarded
  Host; it rejects chains, ports, malformed addresses and duplicate gate fields.
- The IP must be an assigned address of Self or a current `InNetworkMap` peer
  returned by the local Tailscale daemon. The pinned tailnet StableID and DNS
  suffix must match. Shared-to nodes, alternate sharers, foreign DNS suffixes,
  expired nodes and routed subnet addresses are denied. Tags and user IDs do not
  restrict otherwise valid members.
- Status is read through `/var/run/tailscale/tailscaled.sock`, with a one-second
  deadline, 4 MiB response cap and two-second in-memory cache. A failed refresh
  denies access; it never reuses an expired grant. No peer inventory is persisted.
- Any `Tailscale-Funnel-Request` marker is denied at both bridges. Funnel is not
  enabled or otherwise changed by this deployment.

The installed binary's replacement behavior was verified from Nimo and 9950x
using forged identity/forwarded/Funnel headers on a temporary private fixture.
That fixture was removed and every prior Serve value remained identical.
Synthetic tests cover tagged and external-shared peers; no live tagged/shared
peer was available during qualification. Recheck forwarding semantics when
changing the Serve implementation.

The Stirling backend has no published host port and stays on its dedicated Docker
network. Authorized public and private URL fetching uses that network's normal
egress; the application deployment owns this policy. Its fixed address is the only non-loopback upstream
allowed by the bridge. changedetection's published backend and both proxy
listeners remain loopback-only. Configuration rejects arbitrary IP/DNS targets
and any different endpoint pair. This boundary trusts processes on
9950x: a local process can directly reach a no-password backend or forge Serve
headers. The bridge does not claim process isolation or native application
authentication. No credentials are put in browser URLs. Application cookies
and Authorization headers are preserved, including changedetection's API guard;
incoming Tailscale, forwarded and proxy-authorization headers are stripped.
The verified public Host is forwarded; only trusted X-Forwarded-Host and
X-Forwarded-Proto are regenerated from the validated route. Configure each application's external
base URL as its HTTPS address above.

Upload bodies stream with backpressure; responses and WebSockets are not
buffered. Per-route limits bound request bodies and total request/WebSocket
duration. An oversized Content-Length is rejected before contacting the
backend. A chunked upload is stopped at the first chunk exceeding its bound;
the backend may already have received an incomplete prefix. Limits in the
example are 2 GiB plus 16 MiB multipart overhead and two hours for Stirling;
changedetection remains 32 MiB and 30 minutes. Configuration ceilings are 3 GiB
and two hours for Stirling, 32 MiB and 30 minutes for changedetection. Match these
with each application's own limits. Response framing,
cookie values and app auth are preserved; hop-by-hop headers are rebuilt. Responses
deny framing, suppress referrers and disable storage by shared caches.
Each response cookie, including WebSocket upgrade cookies, receives `Secure`
and `SameSite=Lax` only when that attribute is absent. Existing attributes,
cookie bytes and separate Set-Cookie entries remain intact. No HSTS policy is
added because other services use HTTP ports on this same hostname.

Stirling responses additionally apply a browser CSP for same-origin connections,
images, fonts and workers. Local `blob:` connections/images/workers and `data:`
images/fonts remain available for PDF preview. No remote origin is permitted by
these directives; script execution and WASM receive no additional restriction.
Any upstream CSP is retained as a separate policy. changedetection receives no
additional CSP. Candidate upload, thumbnails, merge and output preview passed
with the equivalent meta policy; repeat this workflow through the final HTTPS
route with the real response header before accepting browser compatibility.

Native Stirling REST clients can make `/api/` writes without an Origin only when
they send no browser `Sec-Fetch-*` headers. They still require verified current
tailnet membership. Foreign/null Origins always fail. Browsers retain the exact
same-origin write requirement; normal scanner/mobile requests use this HTTPS
origin. A client that supplies browser metadata can explicitly send
`Origin: https://9950x.taile832fe.ts.net:8452`. changedetection's API still requires
the owner boundary, its native API authorization and its exact Origin on writes.
No proxy password, proprietary authentication feature or invented MCP credential
is introduced.

The private configuration is
`/mnt/ai-store/edsys-private-services/proxy.json`. Copy the example, fill the
owner from the current Tailscale owner identity, pin `tailnet.stableID` and
`tailnet.dnsSuffix` from the current local-daemon status, and set owner `jeremy` and mode
`0600`. The loader rejects symlinks, other owners, other modes, oversized config
files, unexpected keys, unapproved port mappings and invalid limits. Do not
store the real owner configuration or raw request logs in Git/RAG. The user
service checks the AI Store mount before starting and uses the existing pinned
Node executable; it installs no runtime dependencies.

For deployment, preserve the current Serve configuration privately, verify the
two ports are unowned, install the user unit and start only this unit. Configure
only the two individual HTTPS routes with Serve. Do not reset Serve or change
other routes. Roll back access by turning off only HTTPS 8452 and 8453, then
stopping/disabling this user unit. Preserve application data and private config.

Verification before acceptance:

- Run `node --test tests/test_web_proxy.mjs` and validate the user unit.
- Confirm both bridge listeners and changedetection's backend listen only on
  `127.0.0.1`; confirm Stirling has no published host port and only its fixed
  dedicated Docker endpoint. Check the
  final Serve JSON retains all preceding routes and no Funnel activation.
- changedetection must deny missing/wrong owner identity. Stirling must admit
  configured-tailnet members without identity headers and deny missing/forged
  forwarded IP, external shares, wrong tailnet, expired nodes and daemon failures.
  Both must deny Funnel, wrong Host/protocol, foreign Origin, Origin-free browser
  mutations and Origin-free WebSocket. Verify a native Stirling API write too.
- Test HTTPS from Nimo and load both real browser interfaces. Exercise a
  small PDF upload and changedetection state change through their normal UIs;
  a health-page response alone is insufficient.
- Verify forged identity, forwarded address/Host/protocol and Funnel markers
  cannot replace Serve's authenticated values. Live tagged/shared-peer acceptance
  or denial is separate evidence from synthetic membership fixtures.
- Restart only the new proxy/app services and repeat a real owner request.
  A controlled service restart does not establish full-host reboot acceptance.
