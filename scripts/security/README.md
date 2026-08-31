# 9950x SSH Guard

This capability-preserving baseline keeps SSH key-only, disables agent,
stream-local, remote TCP, and tunnel forwarding, and retains local TCP
forwarding for reviewed workflows such as the Cloudflare OAuth loopback tunnel.

The dedicated `inet edsys_ssh` nftables table accepts TCP 22 only on loopback,
the EdSys LAN, and exact approved controller addresses on `tailscale0`, then
drops TCP 22 on every other interface. It does not enable UFW, change Docker
forwarding, flush the global nftables ruleset, or replace the existing EdSys
Share firewall table. Its systemd ordering makes both `ssh.socket` and
`ssh.service` require a successful guard load first; interface-name rules are
valid before the network devices appear, so boot does not need a fail-open
listener window. Reload replaces the dedicated table in one checked nftables
transaction rather than deleting it before the replacement is ready.

The guard uses `DefaultDependencies=no`, runs after local filesystems and
before `network-pre.target`, and is required by both OpenSSH units. That early
placement is intentional: keeping the ordinary service defaults while making
`ssh.socket` depend on the guard creates a cycle through
`basic.target -> sockets.target`, causing systemd to drop the SSH socket's boot
job.

Install only after validating the configured interface names and controller
addresses:

```bash
sudo scripts/security/install-9950x-ssh-hardening.sh
sudo nft list table inet edsys_ssh
sudo sshd -T | grep -E 'pubkeyauthentication|passwordauthentication|kbdinteractiveauthentication|permitrootlogin|x11forwarding|allow(agent|tcp|streamlocal)forwarding|gatewayports|permittunnel'
```

Rollback removes only this table and the EdSys SSH drop-in, then reloads SSH.
Run it from a physical console or a separately tested maintenance session. Do
not combine `disable` with `--now`: the enabled unit installs `RequiredBy=`
links for both SSH units, so stopping the guard before systemd has forgotten
those links can stop SSH as a dependent unit.

```bash
sudo systemctl disable edsys-ssh-guard.service
sudo systemctl daemon-reload
sudo systemctl stop edsys-ssh-guard.service
sudo rm -f /etc/ssh/sshd_config.d/60-edsys-p1-hardening.conf
sudo sshd -t && sudo systemctl reload ssh.service
```

Controller public keys remain live private host state and never belong in Git.

## Historical EdCore Omarchy Workhorse

The configuration below is retained only as dated evidence for the destroyed
temporary Omarchy installation. Its hostname-specific deployment path is not
current and must not be applied to `pve-node3`; current Proxmox control and
isolation source is documented in `docs/EDCORE_CONTROL_PLANE.md`.

`90-edsys-omarchy-workhorse.conf` is the sanitized SSH policy accepted on
`edcore-workhorse`. It permits local TCP and stream-local forwarding for
reviewed loopback services while continuing to deny remote forwarding, agent
forwarding, X11, SSH tunnels, password login, and direct root login. The
dedicated `edsys-admin` account is key-only, password-locked, and source
restricted in its private `authorized_keys` entry.

Install it from a physical console or an already proven key-authenticated
session, validate before reload, and verify a fresh connection before closing
the maintenance session:

```bash
sudo install -o root -g root -m 0644 \
  scripts/security/90-edsys-omarchy-workhorse.conf \
  /etc/ssh/sshd_config.d/90-edsys-hardening.conf
sudo sshd -t
sudo systemctl reload sshd
```

The accepted LAN UFW boundary is default-deny incoming, TCP/22 from the
canonical 9950x only, and the existing Sunshine ports from Nimo only. Tailscale
is a second private transport; reviewed Tailnet policy is its primary identity
gate, with exact-peer UFW rules retained as defense in depth. Public keys,
Tailnet addresses, pairing state, and the live ruleset remain private host state
rather than Git content. Omarchy's user-persistent workhorse posture is
`omarchy-toggle-idle stay-awake`.
