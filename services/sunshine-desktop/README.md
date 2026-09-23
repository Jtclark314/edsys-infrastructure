# 9950x Sunshine console desktop

Owner: Jeremy. Qualified host: Ubuntu 24.04, GNOME X11 console `:0`, RTX 5060.
This is a Desktop-only deployment with one active stream at a time. Nimo is
qualified; the work-laptop client setup is prepared, with laptop acceptance pending. XRDP and its separate `:10` desktop remain unchanged recovery paths.
Current acceptance and limitations live in EdSys-Master's
`docs/9950X_REMOTE_DESKTOP.md`.

## Pinned software

- Sunshine `v2026.516.143833`, official Ubuntu 24.04 amd64 DEB.
- SHA256: `6df8900f23c9c056252eea51639507b8239a1d1241308ab8923cb402b0ca653b`.
- Existing signed Moonlight 6.1.0 on Nimo; no client or NVIDIA driver upgrade.
- Qualified NVIDIA driver: 580.173.02; live H.264/HEVC NVENC probe passes.

Upstream references: [release](https://github.com/LizardByte/Sunshine/releases/tag/v2026.516.143833),
[Sunshine configuration](https://docs.lizardbyte.dev/projects/sunshine/latest/md_docs_2configuration.html),
[Moonlight setup](https://github.com/moonlight-stream/moonlight-docs/wiki/Setup-Guide).
Latest documentation may describe a newer build; qualify the pinned binary.

## Deployment order

1. Verify the console X11 authorization, all monitor modes/transforms, exact
   Nimo LAN/Tailnet identities, GPU encoding and `/dev/uinput` user access.
   Do not infer the console from the shared user-systemd DISPLAY variable:
   XRDP can overwrite it. Do not create autologin, change drivers, or add broad
   input-device permissions as part of this deployment.
2. Verify the official DEB digest, install it, then remove its file capabilities
   with `sudo setcap -r /usr/bin/sunshine`. The packaged user service is not used.
   Package upgrades may reapply capabilities and require a separate review.
3. Install `desktop.py` and `firewall.py` root-owned, mode 0755, under
   `/usr/local/libexec/edsys-sunshine/`. Install the two system units under
   `/etc/systemd/system/`.
4. Create `/etc/edsys-sunshine/clients.json` root-owned mode 0600 from the
   example with only independently verified exact peer IPv4 addresses.
   Install/enable the firewall unit **before starting Sunshine**. Its atomic
   nft table is scoped to Sunshine ports and runs before Tailscale's accepts.
   It never flushes the global ruleset; stopping it leaves the guard intact.
5. Create the user's `.config/sunshine` and `.local/state/edsys-sunshine`
   directories mode 0700. Copy the configuration/app examples mode 0600.
   Re-qualify `output_name = 1` against Sunshine's display enumeration: this
   deployment selects the physical primary DP-0, not HDMI-0.
6. Validate units, reload systemd, enable/start `edsys-sunshine.service`.
   Initialize the admin account over loopback and store recovery material
   privately; pair Nimo without publishing pairing codes or device material.
7. On Nimo, run `install-nimo.ps1` elevated with the verified private hub
   Tailnet address and exact allowed Nimo LAN addresses. The helper validates
   the existing Moonlight signature and creates two desktop shortcuts.
   Never commit the generated `client.json`, pairing state, or logs.
8. Perform the streaming and recovery acceptance in the owning runbook.

## Display lease and recovery

The Desktop prep hook snapshots all monitor modes, rates, positions and the
primary output in private state. It dry-runs the change, then enables only the
primary monitor at 1920x1080/60 Hz. Unsupported scaling/rotation is refused.
Undo restores and verifies the saved layout before removing the snapshot.
This restores monitor geometry, not individual window positions.

The supervisor is pinned to physical `:0`, runs without capabilities, and
restores a stale snapshot before admitting new streams. Normal client exit
uses Moonlight `--quit-after`. Any Sunshine client-disconnect event recycles
the host after two seconds so the next connection reruns prep; this does not
close the physical desktop's independent applications. A launch that never
connects is recovered after 45 seconds. Systemd restarts crashed services and
also runs restoration after service exit. The protocol timeout is 10 seconds;
abrupt-disconnect recovery is not instantaneous.

Never manually run `acquire` concurrently with supervisor startup. For recovery:

```sh
sudo systemctl stop edsys-sunshine.service
sudo -u jeremy env DISPLAY=:0 XAUTHORITY=/run/user/1000/gdm/Xauthority \
  /usr/local/libexec/edsys-sunshine/desktop.py restore
```

If a saved monitor is unplugged or X11 is unavailable, recovery deliberately
retains the snapshot and refuses a new stream. Reconnect the original monitors
and restore; do not delete the only snapshot to force a new session. Do not
connect multiple clients simultaneously. Physical display power-off/headless
operation, other monitor topologies, and full reboot require their own tests.

## Nimo route and shortcuts

`9950x Desktop` uses direct LAN, 1080p60 HEVC hardware decoding, 25 Mbps,
absolute mouse, system-key capture and borderless display.
`9950x Desktop (Tailscale)` uses the exact private host identity and 15 Mbps.

The SYSTEM task `EdSys Nimo 9950x LAN Route` runs at startup/logon and every
minute. An event trigger also runs five seconds after system wake or network
connection, with minute retries for three minutes while the adapter settles.
`nimo-route-events.ps1` adds this trigger to the existing SYSTEM task, preserves
its action and other triggers, and backs up the prior task XML in the protected
runtime directory. It can be reapplied without duplicate event triggers. This
uses Windows [event subscriptions](https://learn.microsoft.com/en-us/windows/win32/taskschd/eventtrigger-subscription).
It manages only an ActiveStore host `/32` route on an up, Preferred,
explicitly allowed EdSys LAN interface. This avoids the advertised Tailscale
subnet route taking local traffic. It supports the qualified wired dock and
Wi-Fi addresses, removes its own stale route off LAN, and refuses conflicting
unmanaged host routes. It does not disable Tailscale or alter default routing.
Address changes require requalification; the helper is not a DHCP reservation.
When changing a client reservation, update both private allowlists: host
`/etc/edsys-sunshine/clients.json` and Nimo `client.json`. Preserve other verified
peers, back up both files, atomically reapply only the Sunshine firewall table,
and invoke the existing SYSTEM route task. Verify the actual source/interface,
all three stream TCP ports, admin denial, and a real Moonlight session. A zero
route-task result alone is insufficient because its unqualified/off-LAN path
also exits successfully. This dependency was missed and corrected on 2026-09-11.
Its source/config directory grants only administrators/SYSTEM write access.
Existing EdCore/RDP shortcuts are not modified.

## Security, data and rollback

- Mandatory LAN and WAN encryption; UPnP disabled; no public forward, tunnel,
  Serve or Funnel. Only exact approved Nimo/work-laptop identities on expected interfaces may reach
  stream ports. IPv6 stream traffic is denied. Web administration is loopback
  only through both Sunshine origin policy and nft filtering.
- Streaming TCP: 47984, 47989, 48010. Guarded UDP: 47998, 47999, 48000, 48002,
  48010. TCP 47990 is dropped for every non-loopback peer, including Nimo.
- Runtime configuration, credentials, paired-device material, logs and layout
  snapshots are **not source material**. The existing encrypted backup include
  roots cover `/home/jeremy/.config` and `/etc`; a fresh Sunshine-specific
  backup/restore remains to be confirmed. Re-pairing is a recovery alternative.
- Rollback: stop/disable Sunshine (restores displays), retain the firewall,
  return to XRDP. Disable the dedicated Nimo route task, remove only its owned
  exact route, and remove the two new shortcuts. No host reboot is necessary.

## Source validation

```sh
python3 -m unittest discover -s services/sunshine-desktop/tests -v
systemd-analyze verify services/sunshine-desktop/*.service
```

Use Windows PowerShell's parser on all scripts before deployment. Test the
route task as SYSTEM, not just from an administrator SSH session. Never use
Moonlight `--help` through a noninteractive Windows SSH session: its blocking
dialog can capture subsequent single-instance CLI requests.

## 2026-09-12 wake recovery

After Nimo resumed from sleep, its direct host route was absent while the
existing minute task still showed its previous-night run and missed executions.
Both peer allowlists and the wired reservation were correct. The existing route
helper immediately restored direct LAN streaming, confirmed by Jeremy. The wake
and network-connect event trigger was then deployed and its event query matched
real local wake/network events. SYSTEM invocation, idempotent registration, and
positive streaming/negative administration probes passed. A subsequent natural
sleep/wake recovery remains to be confirmed; the working session was retained.

## 2026-09-15 work laptop preparation

The host guard now admits the independently verified work-laptop Tailnet peer,
with all Nimo LAN/Tailnet entries preserved. Sunshine administration remains
loopback-only. The previous private client configuration is retained for rollback.
Sunshine, its guard, and XRDP remain active; no Sunshine restart is required.

`install-work-laptop.ps1` runs locally as the ordinary `THOMPSON\jclark`
user on `THOMPSON-LC086`. Inbound administration was refused during preflight,
so the existing trusted outbound SSH connection supplies a private local-run
bundle and returns a bounded result. The script checks existing signed clients,
streaming reachability and administration denial before installation. If absent,
it installs official Moonlight 6.1.0 portable under LocalAppData after a pinned
archive SHA-256 and Windows Authenticode publisher check. It retains an existing
valid client and does not request elevation or change Windows services/routes.

The existing trusted SSH path carries a generated pairing PIN only on stdin to
`pair-local.py`; the helper uses local private recovery credentials solely against
Sunshine's loopback API. Credentials, PINs, client state, and raw output never
enter source or shared-drive publication. The helper requires no exposed admin
port or extra listener. The client verifies the paired Desktop application before
creating and reading back two shortcuts. Both work-laptop shortcuts use Tailscale
at 1080p60 HEVC/hardware decoding, 15 Mbps, borderless display, absolute mouse,
system-key capture, and quit-after cleanup. The ordinary shortcut deliberately
works away from the EdSys LAN. It does not install Nimo's LAN route task.

Rollback: restore any backed-up shortcuts and remove only this owned portable
installation; retain pre-existing Moonlight and its state. Remove only the new
work-laptop host allowlist entry and atomically reapply the existing guard if
revoking access. Revoke its Sunshine pairing separately if pairing completed.

Verified before handoff: official archive download/hash, Valid Windows signature
from Cameron Gutman, real Windows PowerShell 5.1 parsing, ten existing host tests,
and authenticated loopback administration. Work-laptop installed-client inventory,
local script execution, pairing, GPU decoding, video/audio, normal disconnect,
display restoration, and reconnect remain **to be confirmed** until local setup
returns a result and the owner opens a stream. One stream at a time remains required.

## Work laptop as a Sunshine host — prepared 2026-09-23

`install-work-laptop-host.ps1` is a separate local, elevated installer for
streaming **the work laptop's desktop to Nimo**. It refuses another computer,
existing Sunshine, previous setup rules, a missing Tailnet interface, disabled
firewall profiles, and invalid/equal Tailnet peer addresses. Supply the verified
host/client addresses privately; they are not hardcoded in source.

Place official `Sunshine-Windows-AMD64-installer.msi` from release
`v2026.914.233613` beside the script (or pass `-BundleDirectory`). The installer
requires SHA-256 `1d7fed8beecd5889dc7ff14cf9f42d6d38f37c3066c13c6c2a5f4e91847e0ccf`
and valid Authenticode from the release publisher David Lane. The enclosed
executable is unsigned; the package signature and pinned digest establish its
provenance. `-Plan` runs preflight without installation.

Setup applies a temporary program-wide inbound block before MSI execution,
disables MSI-added broad allow rules, denies all other peer addresses and
remote administration, and admits only the supplied Nimo address on the
Tailscale interface and streaming ports. Only after service/listener/Desktop
checks does it remove the temporary block. Errors retain the guard and attempt
to stop/disable Sunshine; if MSI times out, inspect the continuing installer
before retrying. No policy, driver, display, sleep, lid, or SSH changes are made.
The service starts automatically; UPnP is off and encrypted streaming required.

Local administration is `https://localhost:47990`; the owner must create its
account locally and enter Moonlight's pairing PIN there. Reports and the prior
fresh-install configuration stay in an administrator/SYSTEM-only ProgramData
setup directory. Runtime credentials, pairing state, logs, MSI and reports must
remain outside Git/RAG. Keep the laptop powered, awake, and its display present.

Rollback: stop/disable `SunshineService`, uninstall Sunshine using Windows
Installed Apps, then remove only firewall rules in `EdSys Work Laptop Sunshine`.
Retain private configuration if recovery/re-pairing is desired. A partial setup
is intentionally refused on rerun and needs inspection, not blind overwrite.

Observed: work laptop online but SSH/WinRM and Sunshine ports refused; Nimo
offline. Official MSI checksum/signature and real Windows PowerShell 5.1 parsing,
Tailnet validation and exact-peer exclusion calculations passed. Local host
installation, effective corporate firewall policy, encoder/display readiness,
account creation, Nimo pairing, picture/audio/input, reconnect and reboot
acceptance remain **to be confirmed**. No laptop installation is claimed.
