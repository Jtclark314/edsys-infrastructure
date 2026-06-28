# Homepage Workhorse Console

Status: deployed on `9950x` at `http://192.168.50.50:3019`.

This stack runs a second Homepage instance for Workhorse, AI, programming, and Codex tooling. The visual/config source lives in the `homepage-config` repo under `workhorse/`; the live config path is `/srv/homepage-workhorse/config`.

The container uses Docker host networking with `PORT=3019`. Its config uses LAN links by default and a small custom.js rewrite changes those links to `100.87.137.47` when the dashboard is opened over Tailnet.

## Commands

```bash
cd /srv/edsys/edsys-infrastructure
docker compose -f docker/homepage-workhorse/compose.yaml config --quiet
docker compose -f docker/homepage-workhorse/compose.yaml up -d
```

## Safety

- Do not commit `/srv/homepage-workhorse/config/logs/` or other runtime logs.
- Keep workhorse admin UIs limited to LAN/Tailnet bindings unless a separate exposure/auth review approves otherwise.
- This stack exposes only the dashboard on port `3019`.
