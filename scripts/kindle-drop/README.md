# Basecamp Kindle Drop

Status: deployed foundation; private Gmail/Amazon commissioning and real Scribe
acceptance remain required.

This directory is the sanitized deployment source for the private
`\\basecamp.taile832fe.ts.net\Kindle-Drop` workflow. It never contains mailbox
addresses, Kindle destinations, OAuth material, Amazon message samples, Amazon
download URLs, SMB passwords, PDFs, runtime databases, or logs.

## Safety model

- The only scanned paths are `00-Drop-PDF-Here` and `01-Resend`.
- The share requires SMB encryption, server signing, authenticated
  per-device users, no guest access, and no offline caching.
- Each file must stabilize before an atomic move to a private processing path.
- Reparse points, deceptive extensions, zero-byte, corrupt, encrypted, and
  unsupported PDFs fail closed.
- The original PDF is sent without conversion or modification. The automatic
  limit is 18 MiB; larger files through 200 MiB move unchanged to the manual
  web-upload queue.
- A deterministic RFC 822 message ID makes Gmail retries recoverable after a
  dispatcher crash. Duplicate hashes are suppressed unless submitted through
  `01-Resend`.
- A successful Gmail API message ID produces a `submitted` receipt and deletes
  the processing copy. It is never called `delivered`.
- Return capture requires a proven Amazon sender domain, passing SPF or DKIM,
  HTTPS, and a proven exact/wildcard download-host allowlist. Redirects are
  checked against the same list.
- The `/healthz` response contains only aggregate state and binds to Basecamp's
  exact Tailnet IPv4 on port 8094.
- Backups contain retained annotated returns, receipt JSON, and an online
  SQLite snapshot. They exclude intake/processing PDFs, private configuration,
  OAuth data, and logs.

## Tracked files

- `kindle_drop.py` — queue, validation, Gmail submission, return capture,
  health, and safe backup implementation.
- `commission_gmail.py` — interactive OAuth commissioning and DPAPI
  LocalMachine encryption.
- `Install-KindleDrop.ps1` — idempotent Basecamp share/runtime/task/firewall
  installer.
- `New-KindleDropClient.ps1` — creates or rotates a separately revocable
  SMB-only identity, denies non-SMB logons, and blocks other ordinary shares.
- `Health-KindleDrop.ps1` — SYSTEM repair task restricted to the expected
  dispatcher.
- `Backup-KindleDrop.ps1` and `9950x/` — Basecamp snapshot and independently
  hash-verified 9950x pull.
- `tests/` — queue, duplicate, fidelity, validation, allowlist, and backup
  tests.

## Basecamp deployment

Stage this directory on Basecamp, then run in elevated Windows PowerShell:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\Install-KindleDrop.ps1 `
  -ApprovedTailnetClients @(
    "100.87.137.47",  # 9950x monitoring/backup
    "<approved-device-tailnet-ip>"
  )
```

The installer:

1. creates a dated release under `C:\EdSys\KindleDrop\releases`;
2. creates a Python 3.12 virtual environment and runs the full test suite;
3. creates an encrypted, access-based, no-cache SMB share;
4. creates `LOCAL SERVICE` dispatcher, SYSTEM health, and SYSTEM backup tasks;
5. limits health access to 9950x and the SMB Tailnet firewall rule to the
   explicitly supplied client IPs; and
6. starts the listener in fail-closed `commissioning` state.

It deliberately does not change router forwarding, configure Tailscale Serve
or Funnel, publish a web intake, create a Gmail account, invent Amazon
allowlists, or create device passwords.

## Private commissioning

Before authorizing the runtime:

1. Create a dedicated Gmail account with recovery methods and 2FA.
2. Create a personal-use Google OAuth desktop application with publishing
   status **In Production** and only Gmail `gmail.modify`.
3. Approve that Gmail sender in Amazon Send to Kindle.
4. Manually send a benign PDF, annotate it on the first-generation Scribe, and
   share it back to the dedicated mailbox.
5. Inspect that real message to prove the Amazon From domain, SPF/DKIM result,
   redirect host, final download host, and error-message pattern. Never copy
   the message or URL into Git, RAG, or a runbook.
6. On Basecamp, run:

   ```powershell
   C:\EdSys\KindleDrop\releases\<release>\.venv\Scripts\python.exe `
     C:\EdSys\KindleDrop\releases\<release>\commission_gmail.py `
     --client-json C:\private-temporary-location\oauth-desktop-client.json `
     --private-output C:\ProgramData\EdSys\KindleDrop\private\gmail-private.dpapi
   ```

7. Delete the source OAuth client JSON after the encrypted blob exists and
   restart `EdSys Kindle Drop`.
8. Confirm `GET http://100.120.155.81:8094/healthz` changes from HTTP 503
   `commissioning` to HTTP 200 `ready`.

The encrypted blob is machine-bound and ACL-limited to SYSTEM,
Administrators, and LOCAL SERVICE. Treat a Basecamp compromise as requiring
Google authorization revocation and Kindle sender review.

## Device onboarding

Run once per approved device from elevated PowerShell:

```powershell
C:\EdSys\KindleDrop\operations\New-KindleDropClient.ps1 -DeviceName nimo
```

Enter a unique password directly into the secure prompt. Do not paste it into
Git, a ticket, a transcript, or a shared note. Verify the new identity:

- can create/read/rename/delete a disposable file only in `Kindle-Drop`;
- negotiates SMB 3 with encryption and signing;
- cannot access any other ordinary Basecamp share;
- cannot log on locally, through Remote Desktop, as a batch job, or as a
  service; and
- stops working when the account is disabled.

Tailscale grants remain a separate tailnet-admin control. Restrict TCP 445 to
the same approved devices/users and validate with `tailscale configure
validate` before applying. Share ACLs and the Windows firewall are
defense-in-depth, not substitutes for tailnet policy.

## 9950x backup

Install the user timer:

```bash
/srv/edsys/edsys-infrastructure/scripts/kindle-drop/9950x/install-kindle-drop-backup-pull.sh
systemctl --user start kindle-drop-basecamp-backup-pull.service
```

The destination is `/mnt/ai-store/kindle-drop-basecamp-backups`, which is part
of the encrypted EdSys restic/off-site backup selection. The pull refuses an
invalid filename, remote/local SHA-256 mismatch, unsafe ZIP path, missing
SQLite snapshot, or failed SQLite integrity check.

## Acceptance gate

Do not describe this service as accepted until all steps in the EdSys-Master
runbook pass, including real device mappings, representative plan and quote
delivery, visible Scribe handwriting in returned PDFs, a Basecamp cold reboot,
forced dispatcher repair, and an off-host restore. Until then, current status
is `commissioning` and all unproven live facts are `to be confirmed`.
