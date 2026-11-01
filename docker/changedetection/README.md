# changedetection.io on 9950x

Private website change monitoring with the upstream HTTP/text fetcher. This
folder owns the deployable service definition; watch URLs, snapshots, tokens,
settings, and logs remain outside Git.

## Version and scope

- Official stable release: **0.60.10**, published 2026-10-26 and checked against
  GitHub `releases/latest` on 2026-11-01.
- Image: `ghcr.io/dgtlmoon/changedetection.io:0.60.10`, pinned in Compose to
  `sha256:72f97681f390e4d6ae3e6585e1e0036449910f347144b766fc23910458566a1a`
  (linux/amd64).
- Upstream revision: `16974bf351cd68b1978af9494082b75efadaa8e0`.
- No headless browser, browser profile, SMTP account, external notification
  destination, LLM provider, or real watch is configured by installation.
- The HTTP fetcher handles server-rendered content. JavaScript-dependent pages,
  browser steps, screenshot comparisons, and authenticated browser workflows
  are outside this initial qualification. Add a dedicated, isolated browser
  only when a selected target needs one; never attach a personal browser.

Primary references:
[release](https://github.com/dgtlmoon/changedetection.io/releases/tag/0.60.10),
[upstream Compose settings](https://github.com/dgtlmoon/changedetection.io/blob/0.60.10/docker-compose.yml),
[API specification](https://github.com/dgtlmoon/changedetection.io/blob/0.60.10/docs/api-spec.yaml).

## Access and resource bounds

| Purpose | Binding |
| --- | --- |
| Backend | `127.0.0.1:8141` -> container `5000` |
| Owner-facing route | `https://9950x.taile832fe.ts.net:8453` |
| Production bridge | `10.254.230.16/28` |
| Isolated qualification bridge | `10.254.230.0/28` |

The HTTPS route is owned by
[`services/private-web-apps`](../../services/private-web-apps/ACCESS.md). It must
verify the Tailnet owner before passing any request to the backend. The UI has
no additional app password. Its backend must stay on loopback and the private
Docker bridge. The API still requires the app-generated `x-api-key`; keep
`api_access_token_enabled=true`. Shared unauthenticated diff access is disabled.
The proxy must replace forwarded host/protocol headers with its verified values.

The container runs as UID/GID 1000, with a read-only root filesystem, all Linux
capabilities dropped, `no-new-privileges`, 1 CPU, 1 GiB memory, 128 PIDs, a 128 MiB
temporary directory, two fetch workers, and rotating container logs. There is
no host-network mode or Docker socket mount. Recheck subnet overlap before
deploying elsewhere; the explicit subnet avoids the 9950x's exhausted automatic
Docker address pools.

Version/anonymous-stat checks, remote LLM price fetching, and LLM features are
disabled. File URLs and IANA-restricted/private target addresses are blocked.
The latter is an outbound target guard; it does not prevent using the private
UI. A future private target needs a deliberate configuration decision.

## Runtime and startup

Use an env file outside Git, based on `.env.example`. The production datastore is
`/mnt/ai-store/edsys-private-services/changedetection`, mounted at `/datastore`.
It must exist and be writable only by the service owner. Compose refuses to
create a missing host bind directory.

`initialize.py` uses the pinned upstream datastore model only when that directory
is empty. It creates fresh API/RSS tokens without creating the upstream's sample
external watches. It leaves an existing datastore untouched and fails closed if
a nonempty directory lacks `changedetection.json`.

The root-managed startup unit must require the correct `/mnt/ai-store` mount and
validate its filesystem identity before Compose starts. `restart: on-failure:5`
permits bounded process-crash recovery without Docker boot auto-start bypassing
that mount check. Use the owning private-services unit for normal lifecycle
operations. The following are operator commands after its mount checks pass:

```bash
docker compose --env-file /absolute/private/changedetection.env \
  -f /srv/edsys/edsys-infrastructure/docker/changedetection/compose.yaml config --quiet
docker compose --env-file /absolute/private/changedetection.env \
  -f /srv/edsys/edsys-infrastructure/docker/changedetection/compose.yaml up -d
```

For an isolated candidate, use distinct project/container names, loopback port
18141, the qualification subnet, and a separate datastore. Only that candidate
may temporarily set `CHANGEDETECTION_ALLOW_PRIVATE_TARGETS=true` to fetch a local
synthetic fixture. Return it to `false` before production qualification.

## Verification

```bash
docker inspect edsys-changedetection --format '{{.State.Health.Status}}'
docker exec edsys-changedetection python /opt/edsys/healthcheck.py
curl --fail --silent --show-error http://127.0.0.1:8141/worker-health
curl --silent --output /dev/null --write-out '%{http_code}\n' \
  http://127.0.0.1:8141/api/v1/watch
```

Expect healthy, two healthy workers, and **403** for the API request without a
key. `healthcheck.py` reads the private datastore's token in process, verifies
that key enforcement is enabled, and queries `/api/v1/systeminfo`; it does not
print tokens or watch data. Health proves app/API availability, not that a
particular watched website is reachable or unchanged.

Before commissioning or upgrading, use a controlled synthetic page to prove an
initial snapshot, a changed snapshot, and an API/UI diff containing the old and
new content. Pause the fixture before a restart/restore check, compare both
snapshots again, then delete the test watch and stop its fixture. Verify the
owner HTTPS route in a real browser, and reject non-owner/no-identity requests.
Do not configure notifications or start real recurring watches as an acceptance
shortcut. Keep private receipts outside Git.

The isolated 2026-11-01 qualification verified an empty first start, rejected
missing/invalid API keys, two healthy workers, an HTTP fixture changing from
ALPHA/value 10 to BETA/value 20, two snapshots, and the corresponding diff. A
restart preserved both snapshots and the diff. A stopped 10-file datastore
capture had exact SHA-256 parity; an offline restored container reproduced both
snapshots and the diff exactly. With production target restrictions enabled,
loopback HTTP was blocked before a snapshot and a file URL was rejected with
HTTP 400. The temporary watch and fixture containers were removed afterward.
This backend qualification is separate from production owner-route/browser and
offsite-backup acceptance, which belong to the commissioning workflow.

## Backup, restore, and rollback

Back up the **entire datastore** together with the private runtime env and exact
Compose/script version. `changedetection.json` contains application settings and
secret API/RSS tokens. Per-watch UUID directories contain `watch.json`, history
indexes, snapshots, and related state; session-secret files also belong to the
datastore. Watch pages and cookies can be sensitive. A single settings JSON or
URL-list export is not a complete backup.

For a consistent capture, stop only `edsys-changedetection` with the 45-second
grace period, confirm it stopped, and copy the whole directory preserving modes
and ownership. Restart it only if it was running before the capture, including
on failure paths. Upload the completed private capture through the owning
encrypted backup job. Never copy runtime state into this source directory.

Restore into a **new** directory and a separate candidate container first. Use
the exact pinned image and scripts, keep test watches paused, and verify JSON
readability, API-key enforcement, watch counts, snapshot contents, and a real
diff. After acceptance, stop the production container, retain the current
datastore, select the restored path, and start through the mount guard. Only
one app instance may write a datastore.

For an upgrade, retain a stopped, complete pre-upgrade datastore copy and the
old image digest. Upstream schema migrations can change multiple files. A
rollback must restore the matching old datastore with the old image; changing
only the image tag is not a safe rollback. To undo this new installation, stop
its mount-guarded unit and owner proxy route, remove only its container/network,
and keep its data and backup until separately authorized to delete them.
