# Work laptop SSH access

Status (2026-09-23): **installed and accepted for hub commands and file transfer**
in explicit SentinelManaged mode. See the live-acceptance section below for
verified controls and the remaining natural-reboot check. The original
employer-approved SSH scope and the owner's subsequent completion authorization
carry forward; no repeated approval is needed for the same operation.

Jeremy requested persistent PowerShell commands and file transfer from `9950x`
to `THOMPSON-LC086`, with graphical desktop access excluded. The installer uses
the Microsoft Windows OpenSSH Server capability and the existing Tailscale
connection. It provides:

- TCP 22 bound only to the laptop's exact Tailnet IPv4 address.
- One dedicated hub Ed25519 key, restricted in `authorized_keys` to the hub's
  exact Tailnet source, and login limited to `thompson\jclark`.
- Password authentication and SSH agent/TCP forwarding disabled; SFTP enabled.
- By default, an exact hub Windows Firewall allow rule and an explicit block
  for all other IPv4 sources. An explicitly selected Sentinel-managed mode
  preserves existing network policy and enforces hub-only authentication at SSH
  instead; see the admission-mode distinction below.
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

## Firewall admission modes

The default `WindowsFirewall` mode still requires enabled profiles, default
inbound Block, and effective local rule merging. It installs and verifies the
exact-source allow and exclusion rules described above.

The September 23 preflight found a registered `Sentinel Firewall` provider.
The operator's firewall-enablement attempt stopped before any profile, rule,
rollback task, or SSH installation change. The owner subsequently authorized
broader connectivity and all actions needed for the setup. The selected private
manifest uses `"firewallMode": "SentinelManaged"`; live SSH installation and
hub acceptance passed as recorded below.

`SentinelManaged` is an explicit alternative, not an automatic fallback when
Windows Firewall validation fails. It requires the sole registered provider to
be `Sentinel Firewall` and the documented Windows Security Center firewall-health
API to return success and GOOD. It does not enable Windows Firewall, change
Sentinel policy, stop security software, or treat inactive Windows rules as
protection. The installer disables only the default Windows SSH rule created
by its own capability installation.

Network reachability remains governed by the existing Sentinel and Tailscale
policies. Other permitted peers may connect to TCP 22; **hub-only TCP admission
is not claimed**. The listener still binds only the exact Tailnet address, and
successful authentication still requires the dedicated hub key, its exact
`from=` source, and the approved domain account. Passwords and forwarding remain
disabled. Before capability installation, managed mode writes the restricted
configuration and an empty authorization file. It installs the real key only
after stopping/disabling the new service, then validates the final configuration
before startup. On failure/revocation it empties the owned authorization file
before attempting service shutdown, so a failed stop cannot leave new key
logins authorized. Existing sessions require successful service shutdown.

The authenticated Nimo bootstrap returns the public host key using the existing
SMB administrator session, followed by trusted Nimo-to-hub SSH. No password is
stored or transferred to the hub. This authenticated return path must complete
before host-key pinning; a network scan is not a substitute.

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
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\Start-WorkLaptopAccess.ps1 -Action Install -EmployerApproved
```

For a preflight without changing the laptop, run either launcher or main script
with `-Action Plan`. `Plan` is the default for both. The launcher reuses an
already elevated console or requests normal UAC elevation. Installation requires
`-Action Install -EmployerApproved` and the private manifest. A pre-existing
OpenSSH server, configuration, listener, reserved firewall rule, or incomplete
installation is refused for operator review. This is deliberately a fresh
installation helper, not a replacement for an employer-managed SSH server.

In Windows Firewall mode, a temporary TCP 22 block covers capability installation before the broad
Windows default firewall rule is disabled. The block is removed only after
local configuration, permissions, effective firewall, service, and listener
checks pass. Any caught installation failure closes access; if service stop
fails, the block is retained. If the process is interrupted, inspect the private
receipt and use local `Revoke`; do not assume a completed installation or
delete the containment rule manually. Reinstallation after failed/revoked state
requires a reviewed recovery instead of automatic erasure of previous state.

Each launch writes a new, invocation-bound JSON result containing only status,
the installer phase, bounded error text, error type, source filename, and line.
It excludes transcripts, configuration, and key material. The launcher returns
that diagnostic through the existing trusted outbound SSH connection even
when preflight fails, and prints the actual failure in the calling console.
A missing/mismatched result or failed upload is reported without accepting a
stale success. Windows execution-policy or UAC failures that prevent the wrapper
from starting still require a visible local-console check.

After installation the launcher also returns the public host key to the private
hub bundle via that trusted connection. If this upload fails, installation
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
   forwarding. In Windows Firewall mode, check a non-hub peer cannot reach the listener. In
   Sentinel-managed mode, record reachability separately and verify unauthorized
   authentication fails; do not claim a network-layer source exclusion.
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

Twenty focused tests exercise PowerShell parsing, default-parameter binding,
invocation-bound success/failure diagnostics, input-injection rejection, exact
firewall range complements, real OpenSSH configuration parsing, failure-safe
access closure, domain-name quoting, and host-key-change rejection. Linux tests
do not establish Windows service, DISM, ACL, domain, or firewall behavior.
Managed-mode tests also cover explicit selection, unknown/unhealthy provider
rejection, unchanged native-mode requirements, empty authorization before
capability installation, and key revocation before failed service shutdown.
The corrected parameter binding, three script parsers, and failure detail
extraction also passed in a real Windows PowerShell 5.1 process without running
the installer or changing that test host's services.

- [Microsoft Windows OpenSSH installation](https://learn.microsoft.com/en-us/windows-server/administration/openssh/openssh_install_firstuse)
- [Microsoft Windows OpenSSH configuration](https://learn.microsoft.com/en-us/windows-server/administration/openssh/openssh-server-configuration)
- [Microsoft Windows OpenSSH key management](https://learn.microsoft.com/en-us/windows-server/administration/openssh/openssh_keymanagement)

- [SentinelOne firewall management FAQ](https://www.sentinelone.com/faq/)
- [Windows Security Center firewall health API](https://learn.microsoft.com/en-us/windows/win32/api/wscapi/nf-wscapi-wscgetsecurityproviderhealth)

## Live acceptance — 2026-09-23

The work laptop completed Windows OpenSSH installation in `SentinelManaged`
mode. Sentinel was the sole registered firewall provider and its documented
Security Center health check passed; Windows firewall profiles and Sentinel
policy were preserved. The capability installation took about 26 minutes.
The returned public host key was authenticated through the existing bootstrap
connection before hub pinning; a network scan was not the trust anchor.

Accepted: elevated approved-domain login from 9950x, deployed local Verify,
SFTP upload/download with identical hashes, exact Tailnet listener, wrong-key,
password-only and wrong-account rejection, and remote-forwarding rejection.
The dedicated key retains its exact hub source restriction. Other peers can
reach TCP when existing network policy permits; this mode does not promise
hub-only TCP admission. Natural reboot/sign-out persistence remains untested.
To test server forwarding denial, override the generated client's
`ClearAllForwardings` to `no`; otherwise the client suppresses the probe.

Bootstrap controller lessons: scope `RemoteSigned` to the installer process
when the default client policy is Restricted, and capture Task Scheduler COM
state/exit properties before deleting a completed task. Deleted task objects
can return null properties and falsely report failure. Accept only matching
invocation receipts plus a captured native exit of zero. Temporary bootstrap
credentials, tasks, logs and actual endpoint/key material stay outside Git/RAG.
