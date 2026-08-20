# Windows Codex Controller Setup

`Audit-WorkLaptopCodex.ps1` performs a bounded, read-only audit of the Dell
work-laptop controller before any Codex/ChatGPT update or retuning work.

It is intentionally restricted to `THOMPSON-LC086` and the ordinary
`THOMPSON\jclark` desktop session. It must not be run elevated. The report
contains sanitized versions, selected non-secret Codex settings, ACL summaries,
tool availability, Store update-query results, and controller-to-`9950x` SSH
parity. It does not collect credential values, raw configuration files, browser
data, email, logs, SSH keys, or business-file contents.

## Run from the work laptop

From an ordinary PowerShell window:

```powershell
$audit = Join-Path $env:TEMP 'Audit-WorkLaptopCodex.ps1'
scp 9950x:/srv/edsys/edsys-infrastructure/scripts/codex/windows/Audit-WorkLaptopCodex.ps1 $audit
powershell.exe -NoProfile -ExecutionPolicy Bypass -File $audit
```

The script writes a private local copy under
`%LOCALAPPDATA%\EdSys-Private\work-laptop-codex-audits` and uploads the sanitized
JSON report to the private `9950x` Codex checkpoint directory. The generated
reports stay outside Git and RAG.

Use `-NoUpload` only when a local-only audit is required.

## Apply the reviewed setup

After reviewing the sanitized audit, `Configure-WorkLaptopCodex.ps1` performs
the narrow approved apply step. It:

- installs exact official standalone Codex `0.148.0` through OpenAI's Windows
  installer, which validates release digests;
- keeps the Dell default at `workspace-write` plus `on-request` and retains the
  native elevated Windows sandbox;
- sets the current EdSys model/web/agent feature baseline, enables OpenAI
  Developer Docs MCP, and adds the named restricted/full-access profiles (the
  explicit `max-power` profile still retains normal on-request approvals);
- attempts only the reviewed local support packages (GitHub CLI, PowerShell 7,
  ripgrep, jq, uv, and a user-scoped VS Code upgrade); and
- keeps exact private config/profile/PATH backups and uploads only a sanitized
  result.

Run it from an ordinary PowerShell window, not elevated:

```powershell
$setup = Join-Path $env:TEMP 'Configure-WorkLaptopCodex.ps1'
scp 9950x:/srv/edsys/edsys-infrastructure/scripts/codex/windows/Configure-WorkLaptopCodex.ps1 $setup
powershell.exe -NoProfile -ExecutionPolicy Bypass -File $setup
```

The script does not delete the preceding Codex installation or old rollback
material. It does not restart the ChatGPT/Codex UI or reboot Windows; both are
separate acceptance steps after the private result is reviewed.

## Restart the unified app

After the sanitized apply result is reviewed, use the endpoint-restricted
restart helper from an ordinary PowerShell window:

```powershell
$restart = Join-Path $env:TEMP 'Restart-WorkLaptopCodex.ps1'
scp 9950x:/srv/edsys/edsys-infrastructure/scripts/codex/windows/Restart-WorkLaptopCodex.ps1 $restart
powershell.exe -NoProfile -ExecutionPolicy Bypass -File $restart
```

It stops Unified ChatGPT/Codex, ChatGPT Classic, and the local Codex child,
requires the old process set to exit, relaunches only the current unified Appx,
and verifies the exact standalone CLI. It does not reboot Windows.

## Validation

From the infrastructure repository on a host with Python and PowerShell 7:

```bash
python3 -m unittest discover -s scripts/codex/windows/tests -p 'test_*.py' -v
```

The tests parse all PowerShell files and enforce the endpoint, outbound-only,
sanitization, exact official-installer, rollback, private-ACL, bounded-package,
TOML blank-line, safe-default, and named-profile contracts. Runtime reports and
local backups are deliberately not fixtures and must not be committed.

## Operating boundary

The work laptop remains an outbound controller:

```text
work laptop -> Tailscale + key-only SSH -> 9950x
```

Do not enable inbound SSH, WinRM, RDP, or another general remote-admin service
as part of this audit. Windows-local changes require a separately reviewed
apply step after the sanitized report is inspected.
