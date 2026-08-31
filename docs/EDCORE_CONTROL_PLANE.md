# EdCore Proxmox Control Plane

Status: current deployment and operator source for EdCore v3 (`pve-node3`).

## Boundary

- Canonical controller: `9950x`.
- Current EdCore identity: `pve-node3`; key-only OpenSSH lands as root on the
  Proxmox host.
- LAN management: the reviewed `pve-node3` SSH alias and Proxmox HTTPS endpoint.
- Tailnet management: Tailscale is authorized and online as a client-only
  fallback. Tailnet ping and ordinary key-only OpenSSH through the private
  `pve-node3-tailnet` alias pass; Tailscale DNS, accepted routes, advertised
  routes, Tailscale SSH, exit-node, Serve, and Funnel roles are disabled.
- Home Assistant: VMID 300.
- Kali lab: VMID 330, `vmbr77` only, off by default.
- Metasploitable target: VMID 331, `vmbr77` only, off by default.

No password, private key, Tailnet address, auth URL, VM image, or runtime log
belongs in Git, Obsidian, chat, or RAG.

## 9950x helper

The current helper is `scripts/ops/edcore-control.py`; the installed user entry
point is `~/.local/bin/edcore-control`.

```bash
edcore-control status
edcore-control isolation
edcore-control web
edcore-control shell
edcore-control root -- pveversion

edcore-control ha status
edcore-control ha snapshots

edcore-control lab status
edcore-control lab start
edcore-control lab wait
edcore-control lab shell
edcore-control lab snapshots
edcore-control lab shutdown
edcore-control lab restore-starter

edcore-control target status
edcore-control target start
edcore-control target wait
edcore-control target snapshots
edcore-control target shutdown
edcore-control target restore-clean
```

`web` opens an SSH local forward rather than publishing the Proxmox UI through a
new listener. Kali SSH uses the dedicated private guest key through the
`pve-node3` jump host. The target has no general-purpose SSH helper because it
is deliberately vulnerable.

## Isolation contract

The current host-side source is `services/kali-lab/proxmox/`:

- `vmbr77` has address `192.168.77.1/24`, no physical ports, and no gateway;
- the dedicated dnsmasq instance provides DHCP only, with DNS disabled and no
  router or DNS option;
- IPv4 and IPv6 forwarding remain disabled;
- an nftables forward-hook guard drops traffic entering or leaving `vmbr77`;
- both lab VMs have only `vmbr77`, autostart disabled, and deletion protection;
- both VMs are stopped except during a bounded training exercise.

Verify after any host-network or VM-NIC change:

```bash
ssh pve-node3 /usr/local/sbin/verify-edsys-security-lab
edcore-control isolation
```

Never attach VMID 330 or 331 to `vmbr0`, NAT, a physical network, the EdSys LAN,
the Tailnet, or the Internet.

## Recovery points

- Home Assistant: `clean-haos-18-2-baseline-20260830`
- Kali: `clean-baseline-20260830`
- Kali with starter tools: `starter-tools-baseline-20260830`
- Metasploitable: `clean-vulnerable-baseline-20260830`

The off-host recovery images and their hash manifest are private AI Store
material. VM restore commands remain explicit and snapshot-specific; they do
not delete other guests or bypass Proxmox protection.

## Monitoring and backup

- Netdata on `pve-node3` streams to the 9950x Parent as part of the exact
  six-node/five-stream topology.
- Both Homepage dashboards link to pve-node3 and Home Assistant and monitor the
  current endpoints.
- `scripts/backup/edsys-collect-remotes.sh` selects Proxmox, network, isolated
  lab, sysctl, nftables, DHCP, verifier, and systemd host configuration into the
  private backup staging area.
- NetBox is the authoritative structured inventory; the plan-gated sync records
  pve-node3, VMIDs 300/330/331, current IP assignments, and retired prior
  identities without automatic deletion.

## Legacy Omarchy control files

`install-edcore-control-plane.sh`, `edcore-session`, the old Ansible inventory,
and the older libvirt installers remain only as dated Omarchy history. Their
hostname guards reject `pve-node3`. They are not current deployment or recovery
instructions.

## Source validation

```bash
python3 -m py_compile scripts/ops/edcore-control.py
pytest -q scripts/ops/tests/test_edcore_control.py
bash -n \
  services/kali-lab/proxmox/install.sh \
  services/kali-lab/proxmox/verify.sh \
  services/kali-lab/proxmox/edsys-security-lab-guard
```
