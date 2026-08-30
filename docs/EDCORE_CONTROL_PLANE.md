# EdCore Omarchy Control Plane

Status: deployment source for the dedicated EdCore workhorse. Live addresses,
keys, Tailnet identity, pairing records, rollback archives, and host state are
private runtime material and are not stored here.

## Control model

- The canonical 9950x host owns a unique Ed25519 key for the locked
  `edsys-admin` account on EdCore.
- The key is accepted only from 9950x's LAN and Tailnet addresses.
- `edsys-admin` has passwordless sudo so reviewed automation does not depend on
  a shared human password.
- OpenSSH remains key-only, denies root login, agent forwarding, X11, remote
  forwarding, and SSH tunnels. Local forwarding is retained for loopback-only
  tools such as Cockpit.
- `/usr/local/bin/edcore-session` crosses from the root control path into
  Jeremy's existing Hyprland session without storing session secrets.
- `hyprctl`, `grim`, `wl-copy`, `wtype`, and the private `ydotoold` socket
  provide inspection, screenshots, clipboard access, keyboard input, and mouse
  input for the active Omarchy desktop.
- Cockpit listens only on `127.0.0.1:9090`; it is reached through an SSH local
  tunnel and is never opened directly on the LAN or Tailnet.
- Tailscale is a secondary private transport for key-only OpenSSH and the
  already-paired Sunshine/Moonlight session. It does not advertise routes, act
  as an exit node, enable Tailscale SSH, or use Serve/Funnel.

## Source files

- `scripts/ops/install-edcore-control-plane.sh` - root-only, host-guarded,
  rollback-backed bootstrap.
- `scripts/ops/edcore-session` - root-to-live-session execution bridge.
- `scripts/ops/edcore-control.py` - 9950x CLI for shell, sudo, GUI, screenshots,
  clipboard, pointer input, and a Cockpit tunnel.
- `scripts/security/90-edsys-omarchy-workhorse.conf` - reviewed OpenSSH policy.
- `ansible/playbooks/edcore-control-acceptance.yml` - read-only live acceptance.

## Private prerequisites

The operator supplies at deployment time:

1. a dedicated public key whose private half remains on 9950x;
2. 9950x's current Tailnet IPv4 address;
3. Nimo's current Tailnet IPv4 address; and
4. an interactive Tailscale authorization completed on EdCore.

Do not copy these values into this repository. Tailnet access policy remains the
primary identity boundary for traffic accepted through Tailscale's managed
netfilter rules. The installer also adds exact-peer UFW rules as defense in
depth; do not treat those rules as a substitute for reviewed Tailnet grants.

## Deployment

Review and stage the installer, session helper, SSH policy, and public key on
EdCore. Then run the installer from EdCore's physical or graphical session:

```bash
sudo ./install-edcore-control-plane.sh \
  --public-key-file /private/staging/edcore-admin.pub \
  --source-dir /private/staging/source \
  --hub-tailnet-ip TAILNET_IPV4_OF_9950X \
  --nimo-tailnet-ip TAILNET_IPV4_OF_NIMO
```

The installer validates the host and inputs, creates a private rollback folder
under `/var/backups/edsys-edcore-control/`, installs the reviewed packages,
configures services, and pauses for interactive Tailnet enrollment when needed.
Keep the local terminal open until new LAN and Tailnet admin sessions pass.

## 9950x use

Install the helper privately on 9950x, or invoke it from this checkout:

```bash
scripts/ops/edcore-control.py status
scripts/ops/edcore-control.py shell
scripts/ops/edcore-control.py root -- systemctl status tailscaled
scripts/ops/edcore-control.py gui windows
scripts/ops/edcore-control.py gui screenshot /tmp/edcore.png
scripts/ops/edcore-control.py cockpit
```

The default SSH alias is `edcore-admin`. To exercise the fallback route without
changing source, use:

```bash
EDCORE_SSH_HOST=edcore-admin-tailnet scripts/ops/edcore-control.py status
```

## Acceptance

From the infrastructure repository on 9950x:

```bash
python3 -m py_compile scripts/ops/edcore-control.py
bash -n scripts/ops/install-edcore-control-plane.sh scripts/ops/edcore-session
ansible-playbook \
  -i ansible/inventory/edcore-workhorse.yml \
  ansible/playbooks/edcore-control-acceptance.yml
```

Also verify a fresh key-only login on both routes, a Cockpit tunnel bound only
to loopback, one disposable screenshot, Sunshine reachability from Nimo, no
failed units, and the effective SSH configuration. Remove disposable images and
do not commit live output.

## Vendor references

- [Tailscale Linux installation](https://tailscale.com/docs/install/linux)
- [`tailscale up` reference](https://tailscale.com/docs/reference/tailscale-cli/up)
- [Tailscale client preferences](https://tailscale.com/docs/features/client/manage-preferences)
- [Tailscale netfilter modes](https://tailscale.com/docs/reference/netfilter-modes)
- [Tailscale firewall ports](https://tailscale.com/docs/reference/faq/firewall-ports)
