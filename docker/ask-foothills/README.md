# Ask Foothills deployment template

This directory prepares, but does not activate, the private Ask Foothills
service on 9950x. The application remains read-only and mounts the Foothills
project tree with Docker's `:ro` flag.

## Intended route

```text
named user
  -> ask.foothillsproject.com
  -> dedicated Cloudflare Access application
  -> existing EdSys ingress at 192.168.50.4
  -> 192.168.50.50:3036
  -> Ask Foothills container
```

The origin validates the Access JWT against the issuer, audience, expiry, and
rotating JWKS. A `Cf-Access-Authenticated-User-Email` header is not accepted as
authentication.

## Authorization gate

Do not activate this stack until all of the following are separately approved
and available:

1. the exact named-email allowlist;
2. the Cloudflare Access application audience and team domain;
3. a service-scoped LiteLLM virtual key and approved `foothills-query-best`
   alias;
4. Cloudflare hostname and ingress routing;
5. a host firewall rule that allows TCP 3036 from the ingress host
   `192.168.50.4` only;
6. encrypted backup coverage for `/var/lib/ask-foothills`.

No production Cloudflare, firewall, model, or runtime state was changed when
this template was created.

## Preflight and activation

After authorization, place secrets in `/etc/edsys/ask-foothills.env` with mode
`0600`, verify that the configured UID/GID can read the project tree, and
create the audit directory for that same non-root identity:

```bash
sudo install -d -m 0700 -o 1000 -g 1000 /var/lib/ask-foothills
docker compose config
docker compose build --pull
docker compose up -d
docker compose ps
curl --fail http://192.168.50.50:3036/healthz
```

The health endpoint reports catalog/index readiness without admitting a user.
All application and source endpoints require a valid Access assertion.

## Recovery and backup

Docker's `restart: unless-stopped` and the container health check provide
automatic host-reboot recovery. The audit database is metadata-only and purges
records older than 90 days; it still belongs in the encrypted 9950x backup
manifest. Do not back up or persist rendered PDF pages, selected evidence,
prompts, answers, or conversation state.
