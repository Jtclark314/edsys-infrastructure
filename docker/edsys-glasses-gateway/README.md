# EdSys Glasses Gateway Deployment

Sanitized deployment wrapper for the existing private gateway source under `/srv/edsys-glasses-assistant/gateway`.

- Docker publishes host port `8015` only on loopback and the `9950x` LAN address. The exact Tailnet listener is the FreeBind proxy in `../../scripts/network/README.md`.
- Runtime source, `.env`, and captures remain outside Git. This repository tracks the deployment boundary, not private captures or credentials.
- The build context must exist before recovery.

```bash
docker compose --project-name gateway \
  -f docker/edsys-glasses-gateway/compose.yaml config --quiet
docker compose --project-name gateway \
  -f docker/edsys-glasses-gateway/compose.yaml up -d --pull never --no-build
```

Build or replace the local gateway image only in a separate reviewed
maintenance window, then rerun the same health and client checks. Routine
recovery must not build or pull implicitly.

Do not expose the gateway publicly without a separate authentication and data-retention review.
