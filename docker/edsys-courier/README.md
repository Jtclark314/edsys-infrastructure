# EdSys Courier - 9950x Deployment

This stack deploys the private Courier control and resumable-upload server. It binds only to host loopback on TCP `3045`; private HTTPS is supplied by Tailscale Serve. Do not add a LAN bind, Cloudflare route, or Funnel route.

## Runtime Paths

- Image collections: `/mnt/media/images`
- Runtime SQLite/tus state: `/srv/edsys/courier/state`
- Live environment: `/etc/edsys-courier/courier.env`
- Application source: `/home/jeremy/code/edsys-courier`

Media and live configuration do not belong in Git. Courier state is small recovery metadata; media remains outside the current offsite backup set.

## First Deployment

1. Create an unprivileged `edsys-courier` system account in the `media` group.
2. Create the runtime directories and make them writable by that account.
3. Copy `.env.example` to `.env` and set the account's numeric UID/GID.
4. Copy `courier.env.example` to `/etc/edsys-courier/courier.env`, set the real allowed Tailnet login and final Tailnet HTTPS URL, and use mode `0640`.
5. Run `./build-and-deploy.sh`.
6. Confirm `ss -ltn` shows only `127.0.0.1:3045`.
7. Enable private Tailscale Serve HTTPS for `http://127.0.0.1:3045`. Do not enable Funnel.

## Verification

```bash
curl -fsS http://127.0.0.1:3045/healthz
docker compose --env-file .env ps
docker compose --env-file .env logs --tail=100 courier-server
tailscale serve status
```

Authenticated API checks must pass through Tailscale Serve or deliberately supply the allowed identity header from local loopback during a controlled test.

## Update

Pull and test the application source, bump the pinned version in `.env`, then rerun `build-and-deploy.sh`. Incomplete transfers remain in the state/media paths and resume after restart.

## Restore

Recreate the runtime account and directories, restore `/srv/edsys/courier/state` if available, rebuild the image from the pinned source revision, and reapply the private Tailscale Serve route. If state is unavailable, existing final media remains authoritative and Courier will hash files it encounters during future planning.
