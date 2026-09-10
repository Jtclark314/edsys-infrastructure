# Windows Codex Controller Setup

## Local software updater (2026-09-10)

`Update-WorkLaptopCodex.ps1` is the current update entry point for the work
laptop. It runs in ordinary **64-bit Windows PowerShell 5.1** as
`THOMPSON\jclark` on `THOMPSON-LC086`. The owner requested a local update
script after setting aside the inbound remote-access installer. That installer
is not a dependency of this updater.

Save unsent drafts and finish local Codex tasks, then run from an ordinary
PowerShell window outside ChatGPT:

```powershell
$update = Join-Path $env:TEMP 'Update-WorkLaptopCodex.ps1'
scp 9950x:/srv/edsys/edsys-infrastructure/scripts/codex/windows/Update-WorkLaptopCodex.ps1 $update
if ($LASTEXITCODE -ne 0) { throw 'Updater download failed.' }
powershell.exe -NoProfile -ExecutionPolicy Bypass -File $update
```

Alternatively, copy `Start-WorkLaptopUpdate.cmd` beside the PowerShell script
and double-click it. The launcher keeps its console open and displays the exit
code. `-PlanOnly` inventories software and downloads official packages for
inspection without installing or closing apps; `-NoRestart` leaves the app
closed after installation. There are no script-root parameter defaults or
hidden elevation child windows.

After a completed package update, redownload the script and use `-FinishOnly`
to run curated-plugin follow-up, a harmless native sandbox probe in a fresh
user folder, and CLI health checks. This mode retains
a private settings backup, leaves apps running, and does not download or rerun
CLI/Appx/editor/npm installers. Start a fresh app session after plugin changes.
`-FinishOnly -PlanOnly` describes this narrow follow-up without applying it.

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File $update -FinishOnly
```

The updater resolves candidates before installing, writes `preflight.csv`,
and attempts each independent surface even when another fails:

| Surface | Update and verification |
| --- | --- |
| Standalone CLI | Resolve the official stable release, run the vendor installer with that exact release and digest checks, verify the absolute binary and PATH command separately. |
| Unified ChatGPT desktop | Stage the official architecture-specific MSIX, inspect package identity/version/publisher, let Windows enforce signature and deployment policy, verify current-user Appx registration. Store fallback is attempted if staging was unavailable. |
| ChatGPT Classic | Upgrade the existing app with its exact Microsoft Store ID; distinguish no applicable offer from a verified latest version. |
| Codex editor extension | Detect existing `openai.chatgpt` in default profiles of VS Code/Insiders, Cursor and Windsurf found on PATH. Stage the highest stable architecture-specific OpenAI Marketplace VSIX, verify its published SHA-256, install, and verify its exact version. Editors need a reload. |
| Global npm tools | Update only existing `@openai/codex`, `@playwright/mcp`, and `chrome-devtools-mcp` packages to exact resolved stable versions; verify installed versions. |
| Curated plugins | Refresh `openai-curated` through the Git upgrade command only when its metadata identifies a configured Git source. Built-in/local catalogs are inspected through the plugin catalog. Update only enabled installed plugins with a newer numeric version and the same plugin ID. Verify installed identities and enabled flags. |
| Runtime health | Parse individual doctor checks even when the command returns nonzero; preserve failures and warnings separately. A full run reopens the app and observes its process and running embedded CLI version separately. |

The updater does not downgrade a newer version. It keeps normal app settings,
profiles, sign-ins, disabled plugins and marketplace identities. It does not
change corporate policy, add remote access, update unrelated OS applications,
request a Windows reboot, reset browser profiles or delete prior releases.
Browser extensions, hosted connectors, app-managed runtimes, custom/pinned
MCP projects and named editor profiles remain provider/owner-managed. Their
latest versions and authenticated behavior cannot be certified by this local
package updater. It does not perform the hub/Nimo guarded runtime acceptance
transaction or authorize any cleanup of retained installations.

Before closing ChatGPT, it gives a 20-second draft-saving countdown and copies
Codex TOML settings, selected plugin metadata, user PATH, and the preceding
standalone package into a private local run directory. CLI verification failure
triggers an attempt to restore the preceding version with the official
installer. A `Restore-PreviousCodex.ps1` recovery entry point is retained when a
previous standalone version was found. This is CLI recovery material, not a
full-machine or Appx rollback guarantee. For a package-manager rollback, use
the exact preceding version recorded in `preflight.csv`; availability remains
subject to that provider.

Reports, installer diagnostics, downloaded packages and recovery material stay
under `%LOCALAPPDATA%\EdSys-Private\work-laptop-updates\<run>`, outside Git/RAG.
`results.txt` opens in Notepad, and `results.json` preserves structured status.
Exit 0 means the run completed without a recorded failure, 2 means a component
failed, and 1 means the updater stopped. `Check` and `NoOffer` still need review
even on exit 0. A timeout stops subsequent mutations because a detached
installer service could still be running. No report is automatically uploaded.

The source is validated with isolated behavior tests and native Windows
PowerShell 5.1 compatibility checks. The owner's 2026-09-10 result confirms
standalone and npm Codex `0.154.0`, matching PATH, retained/restarted desktop
`26.903.9818.0`, and Classic `1.2026.190.0` with no Store offer. No supported
editor extension was detected. These are owner-provided laptop results, not
independent inbound verification.

The first follow-up exposed an updater bug: the implicit built-in curated
catalog was listed but was not a configured Git marketplace. Source-type
handling is now corrected. The supplied doctor JSON contained 22 passing
checks, one elevated Windows sandbox provisioning failure
(`helper_unknown_error`), and one endpoint-protection warning. The warning
alone does not identify why sandbox setup failed. The revised report preserves
both findings and does not add endpoint exclusions, disable protection, clear
sandbox failure records, or change sandbox mode. The subsequently supplied sandbox log contains May 1/6 failures while Codex
used `C:\Windows\System32` as its working directory: sandbox write-ACL grants
were denied with Windows error 5. These historical entries do not establish
current sandbox health. `-FinishOnly` now invokes the installed CLI's
`codex sandbox -C <fresh-user-folder> -- <PowerShell marker command>` under its
existing sandbox configuration, then reruns doctor. It does not grant System32
access, clear recorded failures, change security policy or choose a weaker
sandbox. The vendor runtime may refresh its own normal workspace setup.
Plugin follow-up execution, current sandbox health and fresh local task/tool
acceptance remain **to be confirmed**.

Official sources:

- [Windows sandbox troubleshooting](https://learn.chatgpt.com/docs/windows/windows-sandbox#troubleshooting-and-faq)
- [Marketplace and doctor commands](https://learn.chatgpt.com/docs/developer-commands)
- [Codex Windows installer](https://releases.openai.com/codex/install.ps1)
- [Deploy the Windows app](https://learn.chatgpt.com/docs/enterprise/windows-deployment)
- [Manage app updates](https://learn.chatgpt.com/docs/enterprise/manage-app-updates)
- [Classic Windows app and Store ID](https://help.openai.com/en/articles/9982051-using-the-chatgpt-windows-app)
- [VS Code extension commands](https://code.visualstudio.com/docs/configure/command-line)

## Earlier audit and configuration helpers

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

## Earlier reviewed setup (pinned August baseline)

This historical configuration helper also retunes settings; use the updater
above for current software updates. After reviewing the sanitized audit,
`Configure-WorkLaptopCodex.ps1` performs
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
as part of this audit. The owner-authorized local updater above performs its preflight and apply in
one run. The audit by itself remains read-only.
