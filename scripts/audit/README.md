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
- `collect-windows-baseline.ps1`: local Windows host baseline metadata.
- `collect-linux-baseline.sh`: Linux host baseline commands to run on a host through SSH or local shell.
- `collect-docker-baseline.sh`: Docker metadata collection without environment variables or logs.
- `sanitize-audit-output.ps1`: redacts risky strings from a local audit folder into a sanitized review folder.

## Typical Windows Use

```powershell
cd "C:\Users\jtcla\Projects\edsys-infrastructure"
.\scripts\audit\collect-network-baseline.ps1
```

The script prints the audit folder path and creates:

- `hosts.csv`
- `ports.csv`
- `arp-a_REVIEW_BEFORE_COMMIT.txt`
- `route-print_REVIEW_BEFORE_COMMIT.txt`
- `ipconfig-all_REVIEW_BEFORE_COMMIT.txt`
- `SANITIZED_SUMMARY.md`
- `PROPOSED_NETWORK_UPDATES.yml`
- `PROPOSED_SERVICE_UPDATES.yml`

Review anything with `REVIEW_BEFORE_COMMIT` before copying a sanitized summary into Git.
