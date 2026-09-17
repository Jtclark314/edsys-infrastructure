# EdCore Proxmox Security Lab

Status: current deployment source for the isolated Kali and Metasploitable lab
on `pve-node3` (EdCore v3).

## Current design

- Proxmox host: `pve-node3` at the reviewed LAN management address.
- Lab bridge: `vmbr77`, `192.168.77.1/24`, no physical bridge ports and no
  gateway.
- DHCP-only service: dedicated `dnsmasq`; DNS is disabled (`port=0`) and the
  router and DNS DHCP options are deliberately empty.
- Kali: VMID 330, reservation `192.168.77.10`, off by default, deletion
  protection enabled.
- Metasploitable 2: VMID 331, reservation `192.168.77.20`, off by default,
  deletion protection enabled.
- Host forwarding: IPv4 and IPv6 forwarding are disabled. A persistent nftables
  forward-hook table drops traffic entering or leaving `vmbr77` even if a
  future host change enables forwarding accidentally.
- Guest access: the locked-password `kali` automation account uses key-only
  SSH through the `pve-node3` jump host. A separate personal `jeremy` console
  account has ordinary password-backed sudo; login material remains private
  in local credential storage and must never be stored in Git or RAG.
  Metasploitable is intentionally vulnerable and has no production-network
  attachment.

The lab is never to be attached to `vmbr0`, NAT, a physical network, the EdSys
LAN, the Tailnet, or the Internet. Both guests remain shut off except during a
bounded training session.

## Deployable source

The current Proxmox host definitions live under [`proxmox/`](proxmox/):

- `edsys-security-lab.interfaces`
- `security-lab-dnsmasq.conf`
- `security-lab.nft`
- `99-edsys-security-lab.conf`
- `edsys-security-lab-guard` and its systemd unit
- `edsys-security-lab-dhcp.service`
- guarded `install.sh`
- read-only `verify.sh`

Deployment is explicit and host-guarded:

```bash
sudo services/kali-lab/proxmox/install.sh --apply
```

Verification is safe and read-only:

```bash
ssh pve-node3 sudo /path/to/verify.sh
edcore-control isolation
edcore-control lab status
edcore-control target status
```

## Accepted recovery points

- Kali `clean-baseline-20260830`
- Kali `starter-tools-baseline-20260830`
- Kali personal console login `personal-login-baseline-20260830`
- Kali before desktop installation `pre-desktop-20260916`
- Kali verified Xfce desktop and new personal login `xfce-desktop-baseline-20260916`
- Metasploitable `clean-vulnerable-baseline-20260830`

The corresponding verified recovery images and manifests remain private on AI
Store outside Git, Obsidian, chat, and RAG. Their hashes are validated in the
private recovery area before use.

## Graphical desktop and offline servicing

Use Kali's Xfce desktop with LightDM through VM 330's Proxmox **Console
(noVNC)**. The existing 6 vCPU and 16 GiB allocation is sufficient. The desktop
uses the existing virtio display; it does not require a separate remote-desktop
port, guest Internet access, or another NIC. The personal console account is
`jeremy`; automation stays key-only under `kali`. Keep login material in private
local credential storage, never in this repository or the knowledge corpus.

The guarded [`install-desktop-offline.sh`](install-desktop-offline.sh) runs
inside the guest against `/var/lib/edsys-desktop-install`. Before running it:

1. Save a Proxmox snapshot and verify the host forwarding guard, isolated bridge,
   guest routes, and free disk capacity. Preserve all older baselines.
2. Copy the guest's `/var/lib/dpkg/status`, Kali archive keyring, and Kali APT
   source definition into a private staging directory on 9950x.
3. In the official `kalilinux/kali-rolling` container, use that source/keyring,
   run authenticated `apt-get update`, and resolve against the copied guest
   status with `-o Dir::State::status=/work/guest-status`. Download only, with
   `--no-remove`, the packages `kali-desktop-xfce x11-apps xdotool`; retain the
   normal recommended desktop dependencies. Do not install guest packages on
   the hub or attach the guest to an online network.
4. Bundle the downloaded `archives/*.deb`, `packages.sha256` (generated from
   authenticated downloads), and `apt-lists.tar` containing the container's
   `lists/` directory. Transfer through the existing key-only SSH jump path.
5. Run the installer with `--apply`. It verifies transfer hashes, replaces
   stale package indexes, installs with `--no-download --no-remove`, preserves
   existing configuration files, and keeps systemd-networkd as network owner.
   NetworkManager is masked and marks all interfaces unmanaged. Additional
   printing, discovery, Bluetooth, and modem services are disabled. Temporary
   package service-start suppression is removed even if installation fails.
6. Verify LightDM, a real personal Xfce login, reboot persistence, clean dpkg
   state, and unchanged lab containment; retain a post-install snapshot.

Raw package lists, downloads, logs, VM screenshots, and login material remain
private outside Git/RAG. `apt-lists.tar` must come from successful signed APT
metadata verification; the transfer hash file alone does not prove provenance.
Do not reuse the old Omarchy online bootstrap installer for this procedure.

The legacy `edcore-control isolation` bridge-name check accepts direct TAP
ports but rejects Proxmox firewall ports such as `fwpr330p0` while a guest is
running. In that case inspect the entire bridge path and host guard directly;
do not remove the firewall to make the checker pass. The full Proxmox verifier
also intentionally requires both lab guests stopped.

## Moonlight streaming from Nimo

The owner-authorized [streaming setup](streaming/README.md) adds Sunshine
inside Kali and an exact-Nimo Tailnet socket relay on pve-node3. Double-click
**Kali Desktop** on Nimo after starting VM 330. The existing noVNC console
remains available. No guest NIC, gateway, NAT, or forwarding was added; only
the documented streaming sockets are proxied from the private client.

## Legacy Omarchy/libvirt files

The XML, preseed, systemd-networkd, and Omarchy/libvirt installer files still
present in this folder are retained only as dated rebuild history. The
`install-edcore-kali-lab.sh`, `install-kali-lab-starter-tools.sh`, and
`install-edcore-metasploitable2-target.sh` flows are host-guarded for the
retired `edcore-workhorse` identity and are **not** the current pve-node3
procedure.

## Backups and recovery

Host configuration is selected by
`scripts/backup/edsys-collect-remotes.sh`. Runtime VM storage and raw recovery
images remain private outside Git. Before changing the bridge or VM NICs:

1. confirm both VMs are stopped;
2. verify off-host recovery-image hashes;
3. retain the current Proxmox snapshots;
4. change one layer at a time;
5. rerun `verify.sh` and `edcore-control isolation`.
