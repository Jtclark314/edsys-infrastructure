# pve-node3 Isolated-Lab Host Definitions

These files reproduce the host-side containment boundary for VMIDs 330 and 331.
They contain no credentials, guest keys, disk images, serial numbers, or live
runtime data.

`install.sh --apply` is deliberately restricted to root on `pve-node3`. It
backs up replaced host files under a root-private directory, installs the
bridge/DHCP/nftables/sysctl/systemd definitions, reloads networking and units,
and finishes with `verify.sh`.

`verify.sh` proves the bridge address, global forwarding state, nftables guard,
DHCP no-router/no-DNS policy, VM NIC placement, autostart/protection settings,
snapshot names, and the accepted stopped resting state.
