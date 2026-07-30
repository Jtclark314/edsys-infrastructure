# Ask Foothills deployment template

This directory deploys the private Ask Foothills service on 9950x. The
application remains read-only and mounts the Foothills project tree with
Docker's `:ro` flag.

## Intended route

```text
named user
  -> ask.foothillsproject.com
  -> dedicated Cloudflare Access application
  -> existing EdSys ingress at 192.168.50.4
  -> 192.168.50.50:3036
  -> Ask Foothills container
```

The origin validates the Access JWT against the issuer, audience, expiry,
rotating JWKS, and the application allowlist. A
`Cf-Access-Authenticated-User-Email` header is not accepted as authentication.

## Authorization and network boundary

The production authorization gate is the dedicated Cloudflare Access
application plus the same email allowlist at the origin. Runtime secrets live
only in `/etc/edsys/ask-foothills.env` with mode `0600`.

For convenient private operations the container publishes TCP 3036 on the
9950x LAN and Tailnet addresses. It never binds `0.0.0.0`, and no router port
forward or public listener is permitted. The host firewall remains permissive
on trusted private interfaces; public admission still flows through Cloudflare
Access and the origin JWT check.

## Preflight and activation

Verify that the configured UID/GID can read the project tree and create the
audit directory for that same non-root identity:

```bash
sudo install -d -m 0700 -o 1000 -g 1000 /var/lib/ask-foothills
docker compose config
docker compose build --pull
docker compose up -d
docker compose ps
curl --fail http://192.168.50.50:3036/healthz
curl --fail http://100.87.137.47:3036/healthz
```

The health endpoint reports catalog/index readiness without admitting a user.
All application and source endpoints require a valid Access assertion.

## Recovery and backup

Docker's `restart: unless-stopped` and the container health check provide
automatic host-reboot recovery. The audit database is metadata-only and purges
records older than 90 days; it still belongs in the encrypted 9950x backup
manifest. Do not back up or persist rendered PDF pages, selected evidence,
prompts, answers, or conversation state.
