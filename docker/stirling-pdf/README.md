# Stirling PDF Core on 9950x

This folder owns the private PDF backend, its reproducible core build and synthetic
acceptance tools. The shared identity proxy, mount guard and recovery integration
live in `services/private-web-apps/`. Architecture and dashboard ownership remain
in EdSys-Master and homepage-config. Runtime state, documents, signing fixtures,
image archives and detailed receipts stay outside Git.

## Release and license boundary

The selected stable release is [v3.2.2](https://github.com/Stirling-Tools/Stirling-PDF/releases/tag/v3.2.2),
published 2026-10-24, commit `684608d143779bf3a8ffc67639f077a1202c62ff`.
The standard vendor image bundles optional proprietary components. This image uses
the official server-only core JAR and the supported frontend `--mode core` build,
with `STIRLING_FLAVOR=core` and `VITE_INCLUDE_PORTAL=false`. No vendor account,
subscription, license activation or external credential is needed.

The [release workflow](https://github.com/Stirling-Tools/Stirling-PDF/blob/v3.2.2/.github/workflows/multiOSReleases.yml)
builds `Stirling-PDF-server.jar` with security and bundled frontend disabled.
The [upstream license](https://github.com/Stirling-Tools/Stirling-PDF/blob/v3.2.2/LICENSE)
covers core with MIT and explicitly excludes proprietary source directories.
`verify-editor-build.mjs` audits every emitted source map, rejects contributions
from proprietary/desktop/SaaS/cloud/Processor trees, then removes those maps.
The core JAR archive is separately checked for proprietary package entries.
A narrow, audited URL-controller repair is applied as documented in CAPABILITIES.md;
every other original JAR entry remains byte-identical.
Third-party engines, models and fonts retain their own licenses. See
[CAPABILITIES.md](CAPABILITIES.md) for feature and license distinctions.

| Immutable input | Pin |
| --- | --- |
| Upstream source archive | SHA-256 `3f2d3f2cd960b0f12e2557630848616a396c6a3c4798f5d420959fee0180f454` |
| Official core server JAR | SHA-256 `370a4218624fa4b3cf5460c886f03de01bfcfd2d4893009202a81e53b5f3344d` |
| Vendor engine base | `stirlingtools/stirling-pdf-base:1.1.0@sha256:3bcc8e10d5319b7c0550cf41b96f6980e1552c974dc37aba462b74fa17c4b5e4` |
| UI builder | `node:24.22.2-bookworm-slim@sha256:c86ecead9e55a5210ac1727de38a4ca840bf03dcaf34dc9749addbf383b1920c` |
| Tesseract best models | [commit e12c65a915945e4c28e237a9b52bc4a8f39a0cec](https://github.com/tesseract-ocr/tessdata_best/tree/e12c65a915945e4c28e237a9b52bc4a8f39a0cec), archive SHA-256 `7584b6bdba03bb920962bd57608a0acabc1b7b6cbd2144b412ff830f6a6db4f1` |

Compose selects the qualified local image by its immutable `sha256:` image ID,
uses `pull_policy: never`, and never selects a moving production tag. Preserve
that exact image with `docker image save` and retain its archive checksum.

## Access and resources

The backend is fixed at `10.254.230.50:8080` on dedicated Docker bridge
`10.254.230.48/28`. It publishes no host port. The host identity proxy supplies
Tailscale HTTPS on 8452 and verifies membership for the user's Tailscale devices,
including tagged devices. Core has no native account/SSO/API-key subsystem.
The proxy gate and upstream isolation are required parts of this deployment.

Public and private LAN/Tailscale HTTP(S) page conversion are explicitly enabled.
The bridge therefore permits outbound networking. Upstream URL/HTML conversion
uses its configurable URL policy with private networks allowed and initial
loopback/link-local/cloud-metadata targets blocked. It retains file-URL protection
and uses WeasyPrint, without a personal browser
profile or JavaScript execution. No SMTP, Telegram, Drive, AI or paid service
credentials are configured. Analytics, PostHog, Scarf, automatic update checks,
cloud indexing and proprietary services remain disabled.

The process runs as UID/GID 1000, with a read-only root, all capabilities dropped,
no privilege escalation, and no Docker socket. Limits are 12 CPUs, 20 GiB memory,
8 GiB Java heap, 1,024 processes, 1 GiB per file, 2 GiB per multipart request and
two hours for request/engine work. Two Office workers run on container loopback;
other expensive engine pools are limited to two concurrent sessions. The proxy
allows multipart overhead above 2 GiB. These are ceilings, not a guarantee that
an arbitrary 1 GiB raster document fits in memory or the temporary workspace.

Office profiles are initialized from an immutable empty template and recreated
on startup. LibreOffice uses the vendor's required sandbox wrapper. Health checks
require the application status response plus both local Office listeners. The
supervisor stops the container if an engine process exits. `on-failure:5` is
bounded; the shared mount-guarded systemd unit owns boot recovery.

## State and retention

All durable paths are beneath `/mnt/ai-store/edsys-private-services/stirling-pdf`:

| Host subdirectory | Container target | Meaning |
| --- | --- | --- |
| `configs` | `/configs` | Settings YAML and generated service identifiers |
| `signatures` | `/customFiles/signatures` | Administrator-provided shared signature images |
| `templates` | `/customFiles/templates` | Custom templates/assets |
| `pipeline` | `/pipeline` | Saved server pipeline definitions, watched inputs and finished outputs |

Bind directories must exist as UID/GID 1000, mode 0700; Compose refuses to create
missing bind sources. `/customFiles` itself is ephemeral: its symlink to immutable
UI assets is never included in the backup tree. Keep durable state a plain,
symlink-free tree. No core user database is created.

Temporary uploads/results/native libraries use an 8 GiB `/tmp` tmpfs; native PDF
libraries require executable mappings there. Logs use 32 MiB tmpfs plus bounded
Docker logs (10 MiB x 3). Custom-file root, unused proprietary storage root and
Office home/cache also use bounded tmpfs. Server cleanup runs every 15 minutes
with a three-hour temporary-file age (longer than the two-hour job ceiling) and
startup cleanup; normal processing also
releases temporary files. Stop discards tmpfs content. Tmpfs is not a secure-erase
promise and follows host swap policy.

Browser library, saved automations, personal signature drawings and preferences
remain local to each browser. They do not sync across Tailscale devices and are
not part of server backups. Download important outputs. Server watch-folder
inputs and completed outputs are persistent and included in backup; the
production deployment starts with no configured recurring watch or pipeline.

## Build and commission

From this folder:

```sh
docker build --tag edsys/stirling-pdf-core:3.2.2-edsys6 .
docker image inspect edsys/stirling-pdf-core:3.2.2-edsys6 --format '{{.Id}}'
```

Copy `.env.example` to a private file outside Git; set the exact image ID and the
verified private HTTPS origin. Create the four state directories above. Production
must use the shared mount guard so a missing/wrong `/mnt/ai-store` volume cannot
silently redirect state to the root filesystem.

```sh
docker compose --env-file /private/path/stirling.env config --quiet
docker compose --env-file /private/path/stirling.env up -d --pull never
curl --fail http://10.254.230.50:8080/api/v1/info/status
```

For qualification, use container `edsys-stirling-pdf-candidate`, project
`edsys-stirling-candidate`, a separate private state tree, IP `10.254.230.34`,
subnet `10.254.230.32/28`, and an explicitly checked origin. Never share a writable
state directory or copy synthetic candidate documents into production.

## Acceptance and recovery

```sh
python3 acceptance.py --base-url http://10.254.230.34:8080 \
  --output /private/path/stirling-basic
python3 qualification.py --base-url http://10.254.230.34:8080 \
  --output /private/path/stirling-expanded \
  --state-root /private/path/stirling-candidate
```

The first script verifies returned merge/split/OCR documents and renders previews.
The expanded script covers security, forms, signing, editing, table extraction,
attachments, large sheet geometry, pipeline API/watch-folder processing and mobile
transfer. It uses disposable synthetic signing material outside Git. Requirements
are Python requests/reportlab/Pillow, OpenSSL and Poppler. Additional conversion
engine qualification and browser receipts belong with the private release receipt;
[CAPABILITIES.md](CAPABILITIES.md) records coverage and actual limitations.

Commissioning also checks engine versions/languages, effective privacy flags,
resource bounds, non-root state, absent host-published ports and the exact image.
Use the real HTTPS route for upload, preview, execution and returned download,
including blob workers/WebAssembly and the proxy's allowed/denied membership cases.
A status endpoint alone is not acceptance.

The shared backup helper briefly stops only this app, captures the complete state
root and private deployment metadata, and restores its prior running state even
on capture failure. It restarts before offsite transfer. All persistent pipeline
PDFs are included, with a 32 GiB capture bound and a 180-second cold-copy bound;
an oversized tree fails visibly rather than silently excluding outputs. No online
export or separate database checkpoint is required. Browser-local data and tmpfs
are outside this server backup contract.

Restore a cold checkpoint to a separate candidate directory, load/select its
matching image, compare configuration/assets/definitions, and repeat returned-file
checks before promotion. Preserve ownership and modes. Before an upgrade retain
the previous image, env, source revision and matching cold state. Rollback stops
only this app, restores that complete pair, and repeats health and functional
checks. Keep displaced state for reversal. Do not prune shared Docker resources.
