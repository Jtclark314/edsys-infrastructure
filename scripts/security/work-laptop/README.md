# Work laptop SSH access

Status (2026-09-10): installer and private hub bundle prepared; **not installed**
on the work laptop. Live Windows acceptance and employer/IT authorization for
persistent inbound administration remain to be confirmed. The existing outbound
laptop-to-hub connection continues to be the bootstrap and public-host-key
return path. This source does not grant access by itself.

Jeremy requested persistent PowerShell commands and file transfer from `9950x`
to `THOMPSON-LC086`, with graphical desktop access excluded. The installer uses
the Microsoft Windows OpenSSH Server capability and the existing Tailscale
connection. It provides:

- TCP 22 bound only to the laptop's exact Tailnet IPv4 address.
- One dedicated hub Ed25519 key, restricted in `authorized_keys` to the hub's
  exact Tailnet source, and login limited to `thompson\jclark`.
- Password authentication and SSH agent/TCP forwarding disabled; SFTP enabled.
- An exact hub firewall allow rule and an explicit block for all other IPv4
  sources on the SSH listener, including when unrelated allow rules exist.
- Automatic delayed SSH service startup, an existing Tailscale service
  dependency, and restart recovery at 15/30/60-second delays.
- Windows OpenSSH event logging, protected system/administrator-only files,
  drift checks, and local access revocation.

It preserves the existing account's administrator rights; it does not create
an administrator, change domain or security policy, enroll Fleet, add graphical
access, change Codex permissions, or bypass UAC. It requires a local elevated
`THOMPSON\jclark` PowerShell session. If that account cannot elevate, employer
IT must resolve the privilege decision; another account is not silently used.
Windows servicing policy must allow the OpenSSH capability. The installer does
not redirect WSUS or override execution/Group Policy settings.

The laptop must be powered on, awake, connected to Tailscale, and allowed by
Tailnet policy. No sleep, reboot, power, or Tailscale key-expiry policy changes
are made. Configured startup is not proof of successful reboot persistence.

## Prepare on 9950x

Inspect live peer addresses first. All real addresses, keys, bundle manifests,
receipts, and host pins remain in protected runtime directories outside Git,
Obsidian, RAG, and the shared-drive publication lane.

```bash
python3 scripts/security/work-laptop/prepare_bundle.py \
  --bundle /home/jeremy/.codex/operator-checkpoints/WORK_LAPTOP_RUN \
  --identity /home/jeremy/.ssh/DEDICATED_WORK_LAPTOP_KEY \
  --hub-address HUB_TAILNET_IPV4 --laptop-address LAPTOP_TAILNET_IPV4
```

The helper refuses to overwrite a bundle or key. The private key stays on the
hub with mode `0600`; only its public counterpart enters `access.json`. The
client uses an isolated `hub-ssh.conf` and `known_hosts` file, strict host-key
checking, and no password fallback. Global SSH configuration is unchanged.

## Run locally on the laptop

Copy the `windows/` bundle folder over the existing authenticated outbound
SSH connection into a local folder. The launcher checks payload hashes and
requests normal UAC elevation. `-EmployerApproved` is an explicit attestation
that employer/IT approval covers this persistent inbound administration; the
previous approval for using AI with work information does not establish it.

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\Start-WorkLaptopAccess.ps1 -EmployerApproved
```

For a preflight without changing the laptop, run the main script in elevated
PowerShell with `-Action Plan`. `Plan` is also the default. Installation requires
`-Action Install -EmployerApproved` and the private manifest. A pre-existing
OpenSSH server, configuration, listener, reserved firewall rule, or incomplete
installation is refused for operator review. This is deliberately a fresh
installation helper, not a replacement for an employer-managed SSH server.

A temporary TCP 22 block covers capability installation before the broad
Windows default firewall rule is disabled. The block is removed only after
local configuration, permissions, effective firewall, service, and listener
checks pass. Any caught installation failure closes access; if service stop
fails, the block is retained. If the process is interrupted, inspect the private
receipt and use local `Revoke`; do not assume a completed installation or
delete the containment rule manually. Reinstallation after failed/revoked state
requires a reviewed recovery instead of automatic erasure of previous state.

The launcher returns only the public host key to the private hub bundle via the
already trusted outbound SSH connection. If this upload fails, installation
may still have passed locally; retain `host-key.pub` and return it through that
trusted connection. A network key scan alone is not the trust anchor.

## Verify from the hub

After the public host key arrives through the existing trusted connection:

```bash
python3 scripts/security/work-laptop/prepare_bundle.py \
  --bundle /home/jeremy/.codex/operator-checkpoints/WORK_LAPTOP_RUN \
  --pin-returned-host-key
ssh -F /home/jeremy/.codex/operator-checkpoints/WORK_LAPTOP_RUN/hub-ssh.conf \
  work-laptop-admin 'powershell.exe -NoProfile -Command "hostname; whoami"'
```

Use `sftp -F ... work-laptop-admin` or modern `scp -F ...` for file transfers.
The stock Windows default shell remains unchanged; explicitly invoke
`powershell.exe` for commands or interactive PowerShell. Remote logons do not
inherit Explorer's mapped drives or guarantee interactive-only application
updates. Authenticated network-resource access from that session is a separate
acceptance item; credentials and agent forwarding are not supplied to solve it.

Before accepting remote administration, verify:

1. Exact computer/account, expected administrator token, and local `Verify`.
2. A bounded PowerShell command and a temporary file upload/download with equal
   SHA-256 hashes, followed by deletion of only that test file.
3. Rejection of a wrong key, password-only login, a different account, and
   forwarding. Check a non-hub peer cannot reach the listener when available.
4. Post-sign-out and a separately authorized natural/controlled reboot,
   automatic service/Tailscale startup, and a fresh authenticated connection.

Do not report installation, remote acceptance, or reboot acceptance solely
from source tests. Pending Windows/package updates are a later operation
through the accepted connection; this installer does not apply those updates.

## Revoke from the laptop

Run locally in elevated PowerShell:

```powershell
powershell.exe -NoProfile -File "$env:ProgramData\EdSys-WorkLaptopAccess\Manage-WorkLaptopAccess.ps1" -Action Revoke
```

For an accepted installation, revocation refuses configuration/key/service
drift instead of overwriting subsequent IT changes. It stops/disables the
owned SSH service, removes its authorization and access rules, and leaves the
default broad SSH firewall rule disabled. The installed Windows capability,
protected host keys, configuration, backups, and receipt remain for recovery.
This revokes access; it is not an OS-feature uninstall. No scheduled task or
separate always-running agent is installed.

The hub key can separately be retired after laptop-side revocation. Do not
delete recovery material or global SSH entries as incidental cleanup.

## Validation and vendor references

```bash
python3 -m unittest discover -s scripts/security/work-laptop/tests -v
```

Focused tests exercise PowerShell parsing, input-injection rejection, exact
firewall range complements, real OpenSSH configuration parsing, failure-safe
access closure, domain-name quoting, and host-key-change rejection. Linux tests
do not establish Windows service, DISM, ACL, domain, or firewall behavior.

- [Microsoft Windows OpenSSH installation](https://learn.microsoft.com/en-us/windows-server/administration/openssh/openssh_install_firstuse)
- [Microsoft Windows OpenSSH configuration](https://learn.microsoft.com/en-us/windows-server/administration/openssh/openssh-server-configuration)
- [Microsoft Windows OpenSSH key management](https://learn.microsoft.com/en-us/windows-server/administration/openssh/openssh_keymanagement)
