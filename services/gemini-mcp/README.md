# Gemini advisory MCP on 9950x

A local stdio MCP exposes `gemini_status` and `ask_gemini` to Codex. It uses
Google's official Antigravity CLI and an authenticated Google account. It is a
custom EdSys bridge, not an official Google MCP or a replacement Codex model.
Source of truth: this directory. Hub operations: `EdSys-Master/docs/CODEX_HUB_OPERATIONS.md`.

## Accepted on 2026-10-01

- Official Antigravity CLI `1.2.14`, verified archive and executable hashes.
- Google-account sign-in, available-model listing and live MCP response from
  `gemini-3.1-pro-high` passed. `/usage` displayed available account quota.
- A direct provider test attempted a canary file read and shell write; both were
  denied by configured rules. Canary stayed unchanged, its undisclosed contents
  were not returned, and the write marker was absent.
- Five Node tests cover input encoding, environment filtering, configuration
  drift, empty optional MCP configuration and provider-result validation.
- Exact subscription-tier label remains to be confirmed; account access and
  quota are verified. The legacy Gemini CLI rejected access with
  `UNSUPPORTED_CLIENT` and is not used by this bridge.

## Operation and limits

`ask_gemini` sends only explicitly supplied question/context text to Google.
This consumes Google account allowance. No API-key route or extra-credit
fallback is configured. Returned advice is untrusted and must be checked.
The native CLI's global file, shell, web and nested MCP permissions are denied;
this also affects other Antigravity CLI sessions using the same settings.
The bridge refuses active nested MCPs, an altered binary or paid-credit policy
drift. An absent/empty native MCP configuration means no additional servers.

Each process accepts one request at a time, at most 96,000 input characters,
120 seconds provider time and 135 seconds overall. Multiple Codex sessions can
still issue concurrent requests. Output is limited to 64,000 characters and
2 MiB of process output. There are no automatic retries. Instructions disable
tools and delegation; permission rules deny the listed actions. This is not
an operating-system sandbox for the entire provider binary.

No listener, port, daemon, scheduled task or public endpoint is created.
Native CLI authentication, logs and conversation state stay private on the
host; they must not enter Git, RAG or prompts. The bridge strips inherited
API credentials and provider overrides from the child environment.

## Install and sign in

Prerequisites: Linux x86_64, Node 24+, npm, Python 3.12+, curl and sha512sum.
From this directory run `./install.sh`. It installs locked MCP dependencies,
downloads the pinned official binary, preserves existing native settings,
and backs up replaced source/lock files privately. It does not register Codex
or complete Google sign-in. If policy checks reject existing native settings,
review them deliberately; do not overwrite unrelated settings blindly.

Run `~/.local/bin/edsys-gemini-login` in a visible terminal. Complete Google's
account sign-in. Paste any one-time browser code into that same terminal.
Check `/usage`, then `/quit`. Never put authorization codes in source or docs.
The default model must appear in `antigravity models`; adjust private
`runtime/provider.json` to an available Gemini model if needed.

After a live MCP request passes, register:

```sh
codex mcp add edsys-gemini -- /home/jeremy/.local/bin/edsys-gemini-mcp
```

Set `tool_timeout_sec = 150` in that server's Codex configuration section.
A new Codex chat may be required to load its tools. Registration does not
connect ChatGPT's web interface to the local stdio server. Do not restart the
managed hub simply to refresh a chat's tool inventory.

## Runtime, verification and recovery

- Private installation: `~/.local/share/edsys-gemini-mcp/`.
- Native settings/state: `~/.gemini/antigravity-cli/` and Google's native keyring.
- Launchers: `~/.local/bin/edsys-gemini-{mcp,login}`.
- Codex registration: the `edsys-gemini` section of `~/.codex/config.toml`.
- Local checks: `npm test`; `bash -n install.sh`; `node --check server.mjs`.
- Provider acceptance: MCP discovery, `gemini_status`, then a harmless
  `ask_gemini` request. Local status alone does not prove account access.

Retain private backups of replaced configuration and lockfiles. For rollback,
remove only this registration with `codex mcp remove edsys-gemini`, restore
the prior bridge source/package files, and run `npm ci` in its runtime.
Reauthenticate through Google if credential recovery is needed; never copy
credentials into Git. Native settings are shared, so review them separately
before restoring. Revoke access using Google's account controls if retiring
the integration. Existing hub services do not require a restart.

Official references:
- https://antigravity.google/docs/cli/install/
- https://antigravity.google/docs/cli/headless/
- https://antigravity.google/docs/permissions?tab=cli
- https://antigravity.google/docs/settings?tab=cli
- https://antigravity.google/docs/cli/gcli-migration/
