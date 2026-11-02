# changedetection.io on 9950x

Private website, document, price and visual monitoring based on upstream
changedetection.io 0.60.10. The dedicated Chromium service enables JavaScript,
browser steps, interactive selection and screenshot comparison. Optional AI
uses the already installed local Ollama model. The UI remains owner-only;
its native REST API still requires an API key.

See [CAPABILITIES.md](CAPABILITIES.md) for tested workflows and limits, and
[images.lock.json](images.lock.json) for immutable runtime pins and source hashes.
This directory owns app/browser/gateway deployment. The owner access proxy,
backup controller and MCP bridge remain in their respective `services/` folders.
No watch URLs, credentials, history, runtime env files or private test receipts
belong in Git.

## Architecture and boundaries

| Component | Network and purpose | Limits |
| --- | --- | --- |
| App | Internal `10.254.230.16/28`, fixed backend `10.254.230.18:5000` | 2 CPU, 4 GiB, 256 PIDs; two workers |
| Browser | Separate internal `10.254.230.112/28`; no host port | 4 CPU, 4 GiB, 512 PIDs; three Chromium sessions |
| Gateway | App `.19`, browser `.115`, outward `.98` on `10.254.230.96/28` | 0.5 CPU, 256 MiB, 128 PIDs |
| Owner proxy | Existing private HTTPS port 8453 | Owner identity checked before UI requests |

Docker 29 suppresses published ports on these internal networks. The owner
proxy and local MCP therefore use the fixed private app IP; no app, CDP or
proxy port is published. Only the gateway has an outward network. Its namespace
IP forwarding is disabled. App and browser cannot directly address each other.
All services drop Linux capabilities, run as UID/GID 1000 with read-only roots,
and use bounded temporary filesystems. The browser has no host mounts, personal
profile, account state, Docker socket or host network.

The browser retains upstream `--no-sandbox`: this deployment relies on Docker
containment and split networks, not Chromium's user-namespace sandbox. Web
security and browser hang protections remain enabled. QUIC and unproxied WebRTC
UDP are disabled. Sessions have a five-minute gateway connection deadline and
a ten-minute browser cleanup backstop; the live editor advertises a four-minute
lease. Closed sessions destroy their temporary browser profiles.

Public websites and private LAN/other-node Tailnet pages are authorized.
Every new HTTP/CONNECT destination, including redirects, nested resources and
WebSockets, is resolved once, checked, then connected using the approved numeric
address. Mixed public/forbidden DNS answers fail closed. Bounds cover DNS,
connection setup, idle time, total lifetime and transferred bytes.

Denied destinations include loopback, link-local/metadata, multicast, address
translation/tunnelling prefixes, this stack's networks, Docker/Kubernetes
management ports and the local model API from browser/watch fetches. Operator
runtime settings additionally deny this machine's Tailnet addresses entirely
and protect its LAN management ports. Denying its own Tailnet identity avoids
inheriting access to privileged Tailscale Serve applications. Other hosts'
ordinary HTTPS/private pages remain usable. Keep these host-address settings
current when the machine or management services change.

Both fetchers verify TLS certificates. A private site with a self-signed
certificate needs an explicitly approved CA trust addition. File URLs and
arbitrary per-watch proxy/browser endpoints are refused. Custom HTTP headers,
including explicit cookies, stay on the original origin. Credentialed
cross-origin redirects are rejected, including redirects that could forward a
POST body. Normal website cookie/session behavior remains Chromium's behavior.

## Versions, source and licensing

The app derives from the pinned official Apache-2.0 image at revision
`16974bf351cd68b1978af9494082b75efadaa8e0`. Narrow fail-closed patches restore
TLS verification, scope credentials, enforce local AI bounds and complete the
REST schema/readback for the installed open-source screenshot processor. A
scalar-settings hydration fix preserves its crop/sensitivity fields when the
native editor is reopened and saved. The
UI's upstream disabled-processor default is explicitly cleared.

The browser uses Apache-2.0 Sockpuppet source pinned by commit and archive hash,
with Debian's open-source Chromium 156.0.8078.73. It does not install Google
Chrome or Widevine. Python browser dependencies are pinned with wheel hashes;
Chromium and the base image are pinned. Other signed Debian packages are
recorded in the build inventory, so rollback uses the retained image archive
rather than assuming a future APT rebuild is byte-identical. Upstream license
texts are retained under `licenses/` and in the images; Chromium and Debian
package notices remain under `/usr/share/doc`.

Primary sources: [app release](https://github.com/dgtlmoon/changedetection.io/releases/tag/0.60.10),
[API source](https://github.com/dgtlmoon/changedetection.io/blob/0.60.10/docs/api-spec.yaml),
[Sockpuppet](https://github.com/dgtlmoon/sockpuppetbrowser/tree/d6c16815a231d0050eee22a8efc5c4afbbc987d9),
[Chromium source](https://chromium.googlesource.com/chromium/src/).

## Deployment and optional local AI

Use a private env file based on `.env.example`; replace all three image values
with the `image_id` values in `images.lock.json`. Set the actual host address
exclusions and private owner URL. The datastore remains
`/mnt/ai-store/edsys-private-services/changedetection`, mounted only into the app.
It must exist with private ownership/permissions. The active settings file is
`changedetection.json`; individual watch directories also contain their history
and processor settings.

Before changing an existing deployment, stop the exact app and retain its full
cold datastore, previous env/source/access configuration and immutable image.
While that original app container still exists in the stopped state, the
following explicit helper seeds the approved existing local provider:

```bash
python3 docker/changedetection/configure_local_ai.py \
  --container edsys-changedetection \
  --datastore /mnt/ai-store/edsys-private-services/changedetection \
  --base-url http://EXISTING-LOCAL-OLLAMA:11434
```

The helper verifies the selected container's datastore mount and API-key
protection, preserves unrelated settings and ownership, and writes atomically.
It is not an every-start reset. Local AI uses `ollama_chat/llama3.2:3b`, the same
already installed model with the serializer that correctly emits `keep_alive=0`.
The model and endpoint are allowlisted; no keys, cloud calls or model downloads
are configured. Generation forces CPU execution, four threads, context 8,192,
maximum 512 output tokens and a 120-second deadline. One cross-process lock bounds
all inference paths; one summary worker and one native request attempt avoid
parallel/repeated work. Global/per-watch monthly token budgets also apply.
New watches default AI off. Raw diffs remain available and global restock AI
fallback is off. The local Llama model has its own Meta Community License;
its weights are not described as OSI-licensed software.

Recreate only the owned old app network during cutover: its previous network
was not internal. Do not modify unrelated networks. The mount-guarded private
services controller owns startup and recovery. After its mount checks:

```bash
docker compose --env-file /absolute/private/changedetection.env \
  -f docker/changedetection/compose.yaml config --quiet
docker compose --env-file /absolute/private/changedetection.env \
  -f docker/changedetection/compose.yaml up -d --wait
```

An isolated candidate uses app/browser/outward networks `.0/28`, `.64/28`,
`.32/28`, separate names and its own datastore. Never share writable state.
Keep `.80/28` available for recovery qualification.

## Verification and rollback

`test_guards.py` covers destination pinning and exclusions, exact host/port
rules, credential-origin scope, transport restrictions, CPU limits and the
cross-process inference lock. Qualification also uses real synthetic HTTP,
JavaScript, form, JSON, XML, PDF, product and visual changes; valid/invalid TLS;
private peer access; credentialed redirects; nested requests; native UI selector
previews; and actual local-model wire/CPU/unload evidence. Final image files must
match the lock's source hashes. Live monitoring should check all three container
health states and the owner UI/MCP route without publishing API tokens.

Back up the complete stopped datastore, source/env/access definitions and all
required immutable images. The browser/gateway have no durable user state.
Retain a matching pre-change set separately from the new-state recovery set.
To roll back, stop this stack, restore its matching old datastore/env/source and
old network/binding, then restore the owner proxy/MCP configuration for that
version. Verify authenticated API access and history after a cold start.
Restoring only the JSON settings file is insufficient. The shared controller's
exact-restored-state rehearsal is required before calling recovery qualified.
