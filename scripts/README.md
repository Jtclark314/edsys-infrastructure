# Scripts

Status: starting baseline.

Store deployment and admin helper scripts here.

Scripts should:

- Be clear about target host and assumptions.
- Avoid hard-coded secrets.
- Prefer environment variables for private values.
- Include comments only where they clarify risk or intent.
- Be safe to read and review before running.

## Subfolders

- `audit/` - read-only host/network/service audit collectors.
- `backup/` - Restic/rclone backup tooling for 9950x, including the unified
  Basecamp Foothills application/service stage, off-host verification, Drive
  parity monitoring, and isolated recovery tests.
- `deploy/` - deployment preparation helpers such as the 9950x workhorse bootstrap.
- `network/` - exact-address exposure helpers, including the FreeBind AI Tailnet socket proxy.
- `ops/` - operational helpers and report-only scripts.
- `kindle-drop/` - private Basecamp SMB-to-Kindle PDF dispatcher, authenticated
  Scribe-return capture, health repair, and verified 9950x backup pull.

Always-on Codex operations (Morning Brief, weekly maintenance, and grounded-RAG
quality evaluation) are deployed from `ops/install-codex-operations.sh`; their
runtime reports and credentials remain outside Git.
