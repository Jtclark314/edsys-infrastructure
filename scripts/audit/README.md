# EdSys Read-Only Audit Tooling

Status: starting baseline.

These scripts collect non-secret operational metadata for EdSys hosts and services. They are designed for review, not blind ingestion.

## Safety Rules

- Read-only checks only.
- No service restarts.
- No installs.
- No public IP range scanning.
- No `.env` reads.
- No Docker environment dumps.
- No logs unless Jeremy explicitly requests them later.
- Raw output stays outside Git under `C:\EdSys-Codex\_local-audits\`.

## Scripts

- `edsys-audit-plan.md`: planned hosts, ports, and guardrails.
- `collect-network-baseline.ps1`: Windows LAN ping and TCP port checks for known EdSys hosts.
- `collect-live-inventory.ps1`: combined known-host inventory collector with Windows neighbor/route/DNS data, targeted ping/port checks, HTTP title probes, and optional non-interactive SSH checks.
- `collect-ssh-inventory.py`: credentialed SSH inventory collector for known EdSys hosts. It reads the password at runtime, sanitizes command output before writing, and does not store credentials.
- `collect-windows-baseline.ps1`: local Windows host baseline metadata.
- `collect-linux-baseline.sh`: Linux host baseline commands to run on a host through SSH or local shell.
- `collect-docker-baseline.sh`: Docker metadata collection without environment variables or logs.
- `sanitize-audit-output.ps1`: redacts risky strings from a local audit folder into a sanitized review folder.

## Typical Windows Use

```powershell
cd "C:\Users\jtcla\Projects\edsys-infrastructure"
.\scripts\audit\collect-network-baseline.ps1
```

For the broader live inventory pass:

```powershell
.\scripts\audit\collect-live-inventory.ps1
```

Use `-SkipSsh` if you only want local Windows neighbor/route data, ping checks, TCP port checks, and HTTP title probes.

For credentialed SSH collection:

```powershell
$env:EDSYS_AUDIT_PASSWORD = Read-Host -AsSecureString | ConvertFrom-SecureString -AsPlainText
py .\scripts\audit\collect-ssh-inventory.py
Remove-Item Env:\EDSYS_AUDIT_PASSWORD
```

The credentialed collector writes sanitized output only. Still review the generated folder before copying anything into Git.

The script prints the audit folder path and creates:

- `hosts.csv`
- `ports.csv`
- `arp-a_REVIEW_BEFORE_COMMIT.txt`
- `route-print_REVIEW_BEFORE_COMMIT.txt`
- `ipconfig-all_REVIEW_BEFORE_COMMIT.txt`
- `SANITIZED_SUMMARY.md`
- `PROPOSED_NETWORK_UPDATES.yml`
- `PROPOSED_SERVICE_UPDATES.yml`
- `PROPOSED_MAC_UPDATES.yml`

Review anything with `REVIEW_BEFORE_COMMIT` before copying a sanitized summary into Git.
