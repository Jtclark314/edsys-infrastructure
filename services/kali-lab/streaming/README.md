# Kali desktop streaming from Nimo

Sunshine runs inside Kali VM 330; the existing Moonlight client on Nimo opens
its Xfce console through a narrowly scoped TCP/UDP relay on pve-node3. This is
an application proxy, not IP forwarding. Streaming stays on the isolated
vmbr77 NIC. The owner subsequently authorized a separate Kali-only
[Internet adapter](../internet/README.md); it does not change this relay
or attach the vulnerable target to an online network.

## Owner use

Start VM 330 in Proxmox when needed, then double-click **Kali Desktop** on
Nimo. Sign in as `jeremy` with the private lab password. Moonlight pairs once;
subsequent launches require no pairing PIN. The shortcut uses 1920x1080,
60 fps, H.264, 20 Mbps, hardware client decoding, absolute mouse, and a
borderless window. Ctrl+Alt+Shift+Q disconnects. The VM keeps running until
explicitly shut down; its Proxmox autostart remains disabled. After a guest
restart, reopen the shortcut. Proxmox noVNC remains the recovery console.

Sunshine software-encodes on the existing 6 vCPU VM. There is no GPU
passthrough requirement. A 60 fps setting is a target, not a measured promise
under every workload. If interaction is sluggish on a distant network, first
reduce the client to 1080p30 and 10 Mbps. Do not expose stream ports publicly.

## Pinned software and provenance

- Official [Sunshine release v2026.914.233613](https://github.com/LizardByte/Sunshine/releases/tag/v2026.914.233613),
  x86_64 AppImage, SHA256
  `6f80297a853bcf8cda114ff131b5c0a8c9a4d96a2a9c3dee1f04c39c54206846`.
- Verify the official release asset digest before extraction on 9950x.
  Transfer the extracted tree offline through the existing SSH jump path into
  `/opt/edsys-sunshine/2026.914.233613/squashfs-root`, root-owned and readable.
- Do not run the AppImage's privileged installer. The Debian trixie package
  did not match this Kali rolling library set; no forced dependency downgrade
  or online guest package source was used.
- Nimo's existing signed Moonlight 6.1.0.0 was retained.

## Files and deployment order

These definitions target the existing `jeremy` UID 1001 Xfce/LightDM guest.
Verify identity, display `:0` / `Virtual-1`, installed socat on the host,
private Tailnet reachability, and available disk space before reusing them.
Keep real addresses, pairing state, credentials, and logs outside Git/RAG.

1. Retain a clean guest snapshot and the original guest nftables configuration.
   Copy Python helpers to `/usr/local/libexec/edsys-kali-stream` as root-owned
   executable files. Copy guest systemd units into `/etc/systemd/system`.
2. Guest `/etc/edsys-kali-stream/guard.env` contains `ROLE=guest`. Install the
   guard unit and include `guest-input.nft` **inside the existing inet filter
   input chain**. Preserve every existing rule. The early guard rejects other
   peers; the existing default-drop chain also needs these explicit accepts.
   Validate with `nft -c -f /etc/nftables.conf` before applying changes.
3. Load `uinput` persistently. Create group `edsys-stream-input`, add `jeremy`,
   and install a udev rule granting only `KERNEL=="uinput"` mode `0660` to
   that group. Do not grant broad access to physical input devices. Sunshine
   has no file capabilities and runs as `jeremy` with NoNewPrivileges.
4. Copy `sunshine.conf.example` and `apps.json.example` into the user's
   `.config/sunshine` as `sunshine.conf` and `apps.json`, mode 0600, directory
   0700. Enable user lingering for the existing PipeWire/Pulse services.
   Enable `kali-stream-guard.service`, `kali-sunshine.service`, and
   `kali-sunshine-console.path`. The root preparation helper copies the active
   LightDM Xauthority into a private runtime directory; the streamer itself
   is unprivileged. The path unit refreshes it when console authority changes.
5. Initialize Sunshine's administrator through loopback HTTPS `/api/password`
   with a separately generated random credential. Save the matching
   `{"username":"...","password":"..."}` privately in the user's
   `web-admin.json`, mode 0600, for `pair-local.py`. Never reuse or print the
   personal console password. TCP 47990 is blocked from all non-loopback
   interfaces and is not relayed.
6. On pve-node3 create the dedicated system account `edsys-kali-relay`.
   Install `guard.py`, the guard unit, and both relay template units. Private
   root-owned mode-0600 files under `/etc/edsys-kali-stream` contain:
   - `guard.env`: `ROLE=relay`
   - `relay.json`: `{"peer_address":"NIMO_PRIVATE_TAILNET_IPV4"}`
   - `relay.env`: `BIND_ADDRESS=PVE_PRIVATE_TAILNET_IPV4` and
     `NIMO_ADDRESS=NIMO_PRIVATE_TAILNET_IPV4`, each on its own line.
   Replace placeholders only in private runtime files. Enable the guard,
   TCP instances 47984/47989/48010, and UDP instances
   47998/47999/48000/48002/48010. Both socket ranges and nftables restrict the
   peer; listeners bind only the host's Tailnet IPv4. Outbound proxy sockets
   bind the existing lab address 192.168.77.1. No new routing or NAT rules.
7. On Nimo create `%LOCALAPPDATA%\EdSys\KaliStream` restricted to the current
   user, Administrators, and SYSTEM. Run `install-nimo.ps1 -RelayAddress
   PRIVATE_RELAY_IPV4` in the logged-in `NIMO-LAPTOP\jtcla` session. It
   preserves an existing Moonlight session, verifies the executable signature,
   pairs through Nimo's existing key-only `ssh 9950x`, confirms the Desktop
   app, and creates the shortcut. Pairing reads a private PIN through stdin;
   the local helper accepts only a single pending request from the relay.
   Current Sunshine requires `pairing_id`; Moonlight's CLI pairing displays a
   success dialog that the installer dismisses only after that exact success.

The guest guard and the host guard use their own `inet edsys_kali_stream`
input table. They never flush unrelated rules or alter forwarding. The
original `edsys_security_lab` forward drop guard remains authoritative.
When reloading a full nftables ruleset, restart the streaming guard afterward.

## Validation and recovery

Run `python3 -m unittest discover -s services/kali-lab/streaming/tests -v` and
validate systemd units on their target hosts. Confirm real Moonlight video,
audio packets, keyboard and mouse input; reconnect after a clean guest start
and verify the login screen. Confirm host IPv4/IPv6 forwarding remains zero,
vmbr77 has no physical uplink, the lab route remains on eth0 and only the
approved Internet adapter has a default route, non-Nimo clients cannot reach
the relay, and guest admin 47990 is blocked even from the host.
Use the full lab verifier with **both guests stopped**; its running-VM check
has the preexisting Proxmox firewall-bridge naming limitation documented in
the parent README.

Recovery snapshots: `pre-sunshine-20260916` and
`sunshine-verified-20260916`. The streaming baseline includes the
owner's subsequently saved password; older desktop/login snapshots can
restore an older password. Snapshot state is private. Back up the guest
Sunshine directory (contains private keys/pairings), host private relay
configuration, deployed units/helpers, and Nimo pairing state privately.
The host-config collector covers the host relay configuration and units.

To roll back, disconnect Moonlight, disable/stop only the eight relay
instances and the guest Sunshine/path units, then restore the pre-Sunshine
VM snapshot. Remove only the `edsys_kali_stream` table after its listeners are
stopped. Preserve the original lab forwarding guard and noVNC/SSH access.
Remove the new Nimo shortcut or restore its saved predecessor. An older
snapshot does not restore host relay files or Nimo pairing state; retain the
private backups until end-to-end recovery is verified.

Accepted on 2026-09-16: H.264 1080p stream, Nimo D3D11 decoding, stereo audio
packet delivery, actual keyboard-created guest marker and mouse movement,
clean shutdown/start, corrected path-watch startup ordering, isolation verifier,
and a guest-agent filesystem-frozen snapshot. Subjective
latency and audio listening quality still require the owner's experience.
