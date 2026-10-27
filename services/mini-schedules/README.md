# Mini Schedules private prototype

Source: `/home/jeremy/code/mini-schedules`.
Manual tasks/dates/predecessors only, synthetic sample, no external project integrations.
Owning product/runbook: source `README.md`, `docs/RUNBOOK.md`, `docs/VERIFICATION.md`.

## Access and startup

One user service binds only `127.0.0.1:8138`. A dedicated private Tailscale Serve
HTTPS listener uses port 8451. The backend checks exact Host/Origin and the
owner's Serve-provided Tailscale login; non-loopback direct connections are
denied. Tagged clients without user identity are denied. Local 9950x loopback
is trusted for operator access. Uvicorn proxy-header interpretation is disabled.
No Funnel, LAN wildcard listener, public domain or Cloudflare change.

Build the pinned frontend and install the locked Python dependencies in the app's
isolated `.venv`. Keep runtime in `~/.local/share/mini-schedules` (0700), env in
`~/.config/mini-schedules/server.env` (0600), and private checkpoints in
`~/.local/state/mini-schedules`. Install this directory's unit in
`~/.config/systemd/user/mini-schedules.service`, validate it, then enable/start.
Existing user lingering provides startup; it is not a tested full-host reboot.

After verifying port availability and saving existing Serve config privately:

```sh
systemd-analyze --user verify ~/.config/systemd/user/mini-schedules.service
systemctl --user daemon-reload
systemctl --user enable --now mini-schedules.service
tailscale serve --bg --https=8451 http://127.0.0.1:8138
```

Verify `/api/health`, real browser save/reload, identity and Origin denial,
dependency arrows, source/build versions and unchanged existing Serve routes.
Use a separate temporary runtime for automated browser tests.

## Recovery and withdrawal

Each explicit save creates a consistent SQLite backup, integrity checked, with
the latest 30 retained. Every saved revision is also retained in the operational
database. Backups contain private task data and must never enter Git/RAG.
This prototype does not commission a new backup timer or off-host backup job.
For isolated restore instructions use the source runbook; never test over live data.

To withdraw only this prototype:

```sh
tailscale serve --https=8451 off
systemctl --user disable --now mini-schedules.service
```

Preserve the runtime. Never reset all Serve routes. Code rollback uses the prior
source/build and the existing database unless a separate schema migration needs
its verified pre-change backup. No unrelated services are restarted.
