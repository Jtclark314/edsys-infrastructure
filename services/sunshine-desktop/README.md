# 9950x Sunshine console desktop

Owner: Jeremy. Qualified host: Ubuntu 24.04, GNOME X11 console `:0`, RTX 5060.
This is a single-client Desktop-only deployment for Nimo, not a generic server
installer. XRDP and its separate `:10` desktop remain unchanged recovery paths.
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
minute. It manages only an ActiveStore host `/32` route on an up, Preferred,
explicitly allowed EdSys LAN interface. This avoids the advertised Tailscale
subnet route taking local traffic. It supports the qualified wired dock and
Wi-Fi addresses, removes its own stale route off LAN, and refuses conflicting
unmanaged host routes. It does not disable Tailscale or alter default routing.
Address changes require requalification; the helper is not a DHCP reservation.
Its source/config directory grants only administrators/SYSTEM write access.
Existing EdCore/RDP shortcuts are not modified.

## Security, data and rollback

- Mandatory LAN and WAN encryption; UPnP disabled; no public forward, tunnel,
  Serve or Funnel. Only exact Nimo identities on expected interfaces may reach
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

Use Windows PowerShell's parser on both scripts before deployment. Test the
route task as SYSTEM, not just from an administrator SSH session. Never use
Moonlight `--help` through a noninteractive Windows SSH session: its blocking
dialog can capture subsequent single-instance CLI requests.
