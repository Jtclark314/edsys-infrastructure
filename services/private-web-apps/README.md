# Private document and change tools on 9950x

This folder owns the shared access, mount-guarded startup and scoped recovery
for [Stirling PDF](../../docker/stirling-pdf/README.md) and
[changedetection.io](../../docker/changedetection/README.md). Deployable app
definitions live in those Docker folders. Private state never belongs in Git.

| Application | Private URL | Backend |
| --- | --- | --- |
| Stirling PDF core | `https://9950x.taile832fe.ts.net:8452/` | Dedicated Docker IP `10.254.230.50:8080`, no published port |
| changedetection.io | `https://9950x.taile832fe.ts.net:8453/` | Host loopback `127.0.0.1:8141` |

See [ACCESS.md](ACCESS.md) for the exact Tailscale identity/origin boundary and
[BACKUP.md](BACKUP.md) for consistent backup, exact-snapshot restore and limits.
Local host processes are trusted. No Funnel, public tunnel or LAN app listener
is part of this deployment. Stirling may fetch public and private LAN/Tailnet
HTTP(S) pages as authorized. changedetection retains its public-target guard and starts without real watches.

## Startup and data

The private runtime root is `/mnt/ai-store/edsys-private-services`, owned by
Jeremy with mode `0700`. Each app has a separate data directory. Live env files
are `deploy/stirling-pdf.env` and `deploy/changedetection.env`; the access proxy
configuration is `proxy.json` (all mode `0600`). Immutable qualified image
archives live in `images/` for offline rollback/rebuild.

Install `control.py` and `mount-check` under `/usr/local/libexec` with the names
`edsys-private-web-apps-control` and `edsys-private-web-apps-mount-check`. Install
the actual `/mnt/ai-store` filesystem UUID in root-owned
`/etc/edsys-private-web-apps/mount-uuid`. The guard refuses a missing mount or
different filesystem before Compose can touch the app state. Long bind syntax
also refuses to silently create an absent data directory.

Install `edsys-private-web-apps.service` as a system unit and the proxy unit as
Jeremy's user unit. The app unit requires Docker and the data mount, binds to
that mount's lifecycle, and runs Compose with explicit private env files, no
image pull, no build, and a health wait. Docker `on-failure:5` gives bounded
process-crash recovery without bypassing the mount guard at daemon startup.
The existing global container-recovery manifest is intentionally unchanged;
this independent unit follows the existing mount-guarded service pattern.

```bash
sudo systemctl start edsys-private-web-apps.service
systemctl --user start edsys-private-web-apps-proxy.service
sudo tailscale serve --bg --https=8452 http://127.0.0.1:8142
sudo tailscale serve --bg --https=8453 http://127.0.0.1:8143
```

Enable the two startup units only after real function/private-access checks.
Install and enable the scoped backup timer only after its local/offsite and
isolated application restore acceptance. Its daily cold capture can briefly
interrupt these two apps; it has no Basecamp dependency and no catch-up run.
No unrelated service or global Docker/Tailscale configuration is reset.

## Upgrade or rollback

For an upgrade, retain the exact old images, source, private env, proxy config
and a consistent pre-upgrade data snapshot. Qualify a separate candidate first;
never run two containers against one live datastore. Restore the matching old
image **and** old data if a migration needs rollback. Local image IDs must
match the retained archive, not merely a mutable tag.

To undo this initial installation without discarding data:

1. Stop/disable the new backup timer; let any active capture finish.
2. Remove only the two new Serve routes using `tailscale serve --https=8452 off`
   and `tailscale serve --https=8453 off`. Do not reset Serve or touch other ports.
3. Stop/disable the user proxy and the root app unit. Remove only these two
   app cards through Homepage source build/check/deploy; preserve other cards.
4. Retain the runtime directory, images and accepted encrypted backups. Container
   or network removal is optional cleanup and must target these projects only.

An acceptance checkpoint must record exact versions/images, PDF output checks,
watch snapshot/diff checks, owner and denial paths, cold startup and restoration,
backup snapshot IDs privately, dashboard parity and any unverified limits.
An app-only restart does not establish a full-host reboot test.
