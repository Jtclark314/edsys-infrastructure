# Kali Internet and approved home-LAN access

Owner-authorized on 2026-09-16 after the initial offline desktop/streaming
setup. Kali now has two virtual adapters:

| Adapter | Purpose | Configuration |
| --- | --- | --- |
| net0 / eth0 | Existing training lab and Nimo stream relay | vmbr77, 192.168.77.10/24, unchanged |
| net1 / kali-wan | Public Internet and approved 192.168.50.0/24 connections | QEMU user-mode NAT, 10.0.2.15/24, gateway 10.0.2.2 |

Metasploitable VM 331 remains stopped with only its vmbr77 adapter. Neither
pve-node3 nor Kali forwards IPv4 or IPv6 packets. The lab bridge still has
no physical uplink, gateway/DNS DHCP options, or kernel NAT. QEMU translates
only VM 330's dedicated adapter traffic in userspace; no inbound port
mappings are configured. Moonlight and key-only SSH still use net0.

The owner subsequently authorized all destinations in 192.168.50.0/24. An
explicit output accept for that subnet precedes the private-address reject.
This permits connections to home devices, including management services;
access alone performs no scan, but intrusive tests or compromised Kali
software can affect those devices. Other private subnets remain blocked.

The guest firewall otherwise blocks private, loopback, link-local, shared-address,
documentation, multicast, and reserved IPv4 destinations on kali-wan, plus
all IPv6 there. This includes the QEMU host service endpoint 10.0.2.2.
Using it as an IP gateway does not require opening IP connections to that
address. DNS goes directly to public resolvers 1.1.1.1 and 9.9.9.9.
Unsolicited inbound traffic and all forwarding through the new adapter are
blocked. The existing guest forward chain also has default-drop policy.
These guest controls assume the trusted Kali administrator does not disable
them; Kali is deliberately the privileged learning workstation.

## Deploy or recover

Use the pre-Internet snapshot `pre-internet-20260916` for full rollback.
Backups of the original guest resolver and nftables file are private under
`/var/lib/edsys-kali-internet-before-20260916`. Templates here contain no
credentials or private Tailnet identity.

1. Verify VM 330 is the intended guest, its current streaming session is
   healthy, VM 331 is stopped, and host forwarding/bridge containment pass.
   Retain a guest-agent filesystem-frozen snapshot before changes.
2. Install `egress.nft` at `/etc/edsys-kali-internet/egress.nft`. Validate it
   with `nft -c -f`, then apply the new table **before adding the adapter**.
   Include that file at top level in `/etc/nftables.conf` for persistence.
   Do not flush the live ruleset: that would remove the separate Sunshine
   guard. Validate the complete persistent file with `nft -c -f`.
3. Install `10-kali-wan.link` and `20-kali-wan.network` under
   `/etc/systemd/network`; install `90-kali-no-forwarding.conf` under
   `/etc/sysctl.d` and apply it with `sysctl -p`. Reload udev rules and
   networkd. Install the provided static `resolv.conf` at `/etc/resolv.conf`.
   The current guest does not use systemd-resolved. Leave its lab interface
   and NetworkManager ownership policy unchanged.
4. On pve-node3 run `qm set 330 --net1 virtio=52:54:00:ED:78:10`.
   **Omit bridge entirely**: a bridge would change the security model.
   The current Proxmox installation hotplugs this device. Confirm the live
   name is kali-wan and there are no pending VM changes. If hotplug is not
   available in a future version, perform a planned guest restart.
5. Run `verify-host.sh` on pve-node3. In Kali check `ip route`, both forwarding
   sysctls, `nft list table inet edsys_kali_internet`, and active nftables.
   Verify public DNS, HTTPS to example.com and http.kali.org, and a real
   browser page. Bounded connection checks to known live services on
   192.168.50.54 and 192.168.50.51 must succeed. Negative checks to
   192.168.51.1, 10.0.2.2 and a shared-address destination must fail.
   `networkctl reload` / `networkctl reconfigure kali-wan` must preserve
   connectivity and streaming. Retain the accepted snapshot
   `internet-enabled-20260916`.

For a narrow rollback, remove net1 from VM 330 first, remove only the new
link/network/sysctl/include files, restore the saved resolver, and delete
only `inet edsys_kali_internet`. Do not replace the current full nftables file
if unrelated firewall changes have been made since the backup. Restore the
full pre-change VM snapshot only when its rollback scope is intended. Keep
all host forwarding guards and the existing streaming relay in place.

For only the LAN exception rollback, remove the 192.168.50.0/24 accept rule
and atomically replace just `inet edsys_kali_internet`. The private
`egress-before-lan.nft` backup and `internet-enabled-20260916` snapshot
retain the prior Internet-only policy. No network adapter changes are needed.

## Verification and limits

Live acceptance on 2026-09-16 covered DNS, successful HTTPS document retrieval,
Firefox rendering, rejected private destinations, unchanged host and guest
forwarding controls, VM 331's sole isolated NIC and stopped state, live
networkd reconfiguration, and active streaming. No guest reboot was needed;
persistent files and QEMU configuration were validated, while a later cold
boot remains an additional check.

The subsequent LAN exception passed two TCP connection checks to known
Proxmox management endpoints, continued Internet HTTPS, and rejected
out-of-scope private destinations. No network scan or exploit was run.
Forwarding remains disabled and the target remains stopped and isolated.

QEMU user-mode networking is suitable for browsing and TCP/UDP connections.
It is not a transparent Ethernet connection: some ICMP/raw-packet exercises
need the separate training interface. Do not interpret a failed Internet
ping alone as proof that browsing is broken. Reference:
[official QEMU network emulation documentation](https://www.qemu.org/docs/master/system/devices/net.html#using-the-user-mode-network-stack).
