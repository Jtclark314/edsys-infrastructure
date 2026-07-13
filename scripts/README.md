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
- `backup/` - restic/rclone backup tooling for 9950x.
- `deploy/` - deployment preparation helpers such as the 9950x workhorse bootstrap.
- `network/` - exact-address exposure helpers, including the FreeBind AI Tailnet socket proxy.
- `ops/` - operational helpers and report-only scripts.
