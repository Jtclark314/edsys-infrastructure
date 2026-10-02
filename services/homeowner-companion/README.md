# Homeowner Companion private access

Owning app: `/home/jeremy/code/homeowner-construction-companion`.
Deployment definitions: this directory. High-level status:
`/home/jeremy/code/EdSys-Master/docs/HOMEOWNER_COMPANION.md`.

On 2026-10-02 Jeremy requested LAN and Tailscale access for UI exploration and
explicitly selected **trusted-LAN HTTP plus Tailscale HTTPS**. No public hosting,
Funnel, router forwarding, DNS changes, model integration or email service is
authorized by this deployment. The app's owner sign-in remains required.

## Intended listeners

- App: exact `127.0.0.1:8126` and `192.168.50.50:8126`, one Python process.
- Tailnet: `https://9950x.taile832fe.ts.net:8447/`, private Tailscale Serve
  reverse proxy to `http://127.0.0.1:8126`.
- Never bind `0.0.0.0`/`::` or enable Funnel. Preserve other Serve ports/routes.
- The app admits only configured loopback, home LAN and Tailnet source networks,
  validates exact scheme/Host/Origin and CSRF, and trusts forwarding headers only
  from loopback. HTTPS cookies stay Secure even while LAN HTTP is enabled.
- LAN HTTP does not encrypt sign-in or data; use it only on the trusted home LAN.
  Prefer the Tailscale HTTPS link for portable devices and browser microphone APIs.

## Installation / operation

1. Fetch the intended app source and run its tests/typecheck/build/browser suite.
2. Make a private online SQLite/evidence backup with the app helper and verify an
   isolated restore. Save current Serve JSON privately; never publish it to Git.
3. Copy `server.env.example` to `~/.config/homeowner-companion/server.env`, mode
   `0600`, and substitute the exact Tailnet DNS name. Do not replace existing
   owner data, reset credentials or open remote first-owner registration.
4. Install `homeowner-companion.service` in `~/.config/systemd/user/`. Validate
   with `systemd-analyze --user verify` and stop only the old pilot process.
5. Run `systemctl --user daemon-reload` and
   `systemctl --user enable --now homeowner-companion.service`. Existing user
   lingering enables boot startup; verify its state instead of assuming it.
6. After checking the chosen port is free, run:

   ```bash
   tailscale serve --bg --https=8447 http://127.0.0.1:8126
   ```

   This adds one route. Do not run `tailscale serve reset` or alter other routes.
7. Check real LAN and Tailnet pages/API from another device, valid TLS, unauthenticated
   project denial, login on isolated test data, Host/Origin/CSRF guards, preservation
   of existing data and Serve entries, and restart/recovery behavior.

The user service supersedes the original tmux pilot. It starts on login/boot and
restarts only itself after failure. No timer or scheduled notification is added.
Inspect with `systemctl --user status homeowner-companion.service`; restart only
this service after a reviewed app change. Logs must not contain access links or
credentials; HTTP access logging is disabled.

## Recovery / rollback

Runtime remains `~/.local/share/homeowner-companion`, outside source. Use the
owning app's backup/restore runbook. Keep private checkpoints in
`~/.local/state/homeowner-companion`; never overwrite live data with test restores.

To withdraw this deployment, disable only the new route with
`tailscale serve --https=8447 off`, then stop/disable the user service. Return to
the prior source/build and loopback-only launch with default environment.
Do not reset all Serve routes, restore unrelated services, or roll back the
database merely to revert a listener change. This release has no schema change.
Preserve existing accounts, project records and evidence throughout rollback.

For dated acceptance and physical-device limits use the app's
`docs/VERIFICATION.md`; systemd enabled status alone is not a reboot test.

Vendor references: [Tailscale Serve command](https://tailscale.com/docs/reference/tailscale-cli/serve)
for a private HTTPS listener, and [Uvicorn proxy settings](https://www.uvicorn.org/settings/)
for the exact trusted forwarding boundary. These do not replace live route checks.
