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
- Guest access: Kali uses key-only SSH through the `pve-node3` jump host.
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
- Metasploitable `clean-vulnerable-baseline-20260830`

The corresponding verified recovery images and manifests remain private on AI
Store outside Git, Obsidian, chat, and RAG. Their hashes are validated in the
private recovery area before use.

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
