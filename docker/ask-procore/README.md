# Ask Procore protected path

The single-owner test UI is at **https://ask.foothillsproject.com/procore/**.
It reuses the existing Ask Foothills Cloudflare Access application, owner
restriction, DNS and tunnel. Only its Caddy site block changes; all other
paths still reach Ask Foothills on port 3036.

`ask-foothills.caddy` is the sanitized replacement **site block**, not a full
ingress configuration. The live Caddyfile is
`/opt/stacks/edsys-ingress/Caddyfile` on SSH host `edsys-ingress`. Preserve all
other blocks and validate the full candidate before reloading. Do not deploy
this fragment as the complete Caddyfile. The `/procore` redirect adds the
trailing slash; `handle_path` removes the prefix and immediate flushing keeps
progress events flowing. The UI uses relative asset/API paths.

## Origin and ownership

The enabled `ask-procore-ui.service` user unit on 9950x binds exactly
`192.168.50.50:8765`, with no wildcard/public listener. Source, unit, tests and
operations are owned by the local Foothills repository:

`/home/jeremy/projects/foothills/.agents/skills/foothills-procore-browser/scripts/ask_ui/`

Private `~/.config/ask-procore-ui/runtime.env` (0600) selects the existing task
and reuses only the existing Ask Foothills Access issuer, audience and single
owner email. No Codex credential is copied. Every origin page/API request
requires a signed Access assertion; identity headers alone are rejected.
The same-user Codex Unix socket is used internally. Never proxy it directly.

The unit has automatic process recovery and starts with the existing lingering
user manager. `/run/user/1000/ask-procore-ui/status.json` is a 0600 operational
status file with time/connected/busy only, not a transcript or identity store.
The unit, example settings and full recovery commands remain with the source.

## Rollback and backup

Private pre-change/candidate copies are on ingress under
`~/.local/share/ask-procore-deploy/20260914/`. Restore only the original Ask
Foothills site block, validate/reload Caddy, and stop/disable the Ask Procore
unit. Do not overwrite unrelated later routing changes. Preserve private
configuration through approved encrypted host backup or reconstruct it from
the existing Access settings and exact task identity; do not put it in Git.
Conversation backup/recovery belongs to the existing Codex service. There is
no new database, upload store, transcript archive, container or volume.

## September 14 verification

- Caddy full-config validation/reload passed; only one site block changed.
- The adapter service is enabled, connected, and survived a controlled restart.
- Seventeen authentication/HTTP/stream/routing tests, JS syntax, and systemd
  validation passed. Access public signing keys are reachable.
- Direct and ingress unauthenticated/spoofed requests returned 401. Existing
  Ask health remained 200. The `/procore` slash redirect returned 308.
- Browser-agent public page/API requests returned 302 to Access. A default
  Python user-agent was denied with 403; no access was granted.
- Fresh real owner sign-in and authenticated public browser/stream/submission
  acceptance remain **to be confirmed**. No test conversation was requeued.

See EdSys-Master `docs/ASK_PROCORE.md` for the current service overview.
