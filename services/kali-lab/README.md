# EdCore Kali Security Lab

This service definition builds one deliberately isolated Kali Linux virtual
machine on `edcore-workhorse`. It does not bridge the VM to the EdSys LAN,
Tailnet, Docker networks, or the public Internet after bootstrap.

## Design

- Hypervisor: KVM/QEMU through the system libvirt connection.
- Management: Cockpit Machines through the existing loopback-only Cockpit
  tunnel, plus the `edcore-control lab` commands on `9950x`.
- Guest: stable Kali point release, installed from the official signed
  installer image as a headless `kali-linux-core` system.
- Authentication: a dedicated Ed25519 key whose private half stays on `9950x`.
  The guest account has no usable password and direct root login is disabled.
- Final network: `security-lab`, `192.168.77.0/24`, no libvirt forwarding and
  no host firewall route to the LAN, Tailnet, or Internet.
- Bootstrap network: a temporary libvirt NAT network used only for the install
  and first controlled update. It is destroyed and undefined before
  acceptance.
- Storage: a dynamically allocated 160 GiB qcow2 disk on EdCore's encrypted
  Btrfs filesystem.
- Recovery: a shut-down libvirt snapshot named with the
  `clean-baseline-YYYYMMDD` convention.

The isolated network is intentionally reusable for future vulnerable target
VMs. Target definitions must be separately reviewed and must never bridge this
network to production EdSys systems.

## Source files

- `security-lab-network.xml` - persistent isolated final network.
- `security-lab-bootstrap-network.xml` - temporary NAT bootstrap network.
- `kali-lab-preseed.cfg.in` - secret-free automated installer template. The
  deployment script substitutes only the dedicated public key.
- `../../scripts/ops/install-edcore-kali-lab.sh` - guarded installer.
- `../../scripts/ops/verify-edcore-kali-lab.sh` - read-only acceptance.

## Accepted deployment

The first live deployment passed on 2026-08-30 with Kali 2026.2. The official
archive signature and exact installer checksum passed before QEMU saw the
media. The bootstrap updater reported zero pending packages. The final guest
has no default route or resolver, cannot reach the EdSys LAN or Internet, and
accepts key-only SSH only from EdCore's isolated bridge address. The VM is
shut down by default and has the internal snapshot
`clean-baseline-20260830`.

## Deployment

Create a dedicated key on `9950x`; keep the private file outside Git. Copy only
the public half and this reviewed source to private staging on EdCore, then run:

```bash
sudo ./install-edcore-kali-lab.sh \
  --public-key-file /private/staging/edsys-kali-lab.pub \
  --source-dir /private/staging/kali-lab
```

The script refuses the wrong hostname, rejects malformed keys, verifies the
official Kali archive-key fingerprint, authenticates the signed SHA256SUMS,
verifies the installer image, applies narrowly scoped temporary UFW rules,
blocks bootstrap access to RFC1918/link-local destinations, and refuses to
overwrite an existing VM. If a first staging attempt is safely interrupted
before a domain exists, `--resume-staged` revalidates the signed media,
networks, and disk instead of repeating the package transaction.

After the installer finishes, use the dedicated key through the EdCore jump
host to perform the first update. Replace the guest's temporary DHCP stanza
with the final static `192.168.77.10/24` configuration and no gateway or DNS,
shut it down cleanly, replace its NIC with `security-lab`, detach the installer
media, and remove the bootstrap network and every bootstrap-labeled UFW rule.
Boot once on the isolated network, prove the negative LAN/Internet tests, shut
down again, create the internal `clean-baseline-YYYYMMDD` snapshot, and run the
verifier.

The 9950x helper keeps ordinary use short and auditable:

```bash
edcore-control lab status
edcore-control lab start
edcore-control lab run -- uname -a
edcore-control lab shell
edcore-control lab shutdown
edcore-control lab snapshots
```

`lab shell` and `lab run` use the dedicated 9950x-only guest key through the
`edcore-admin` jump host; the private key is never staged on EdCore or stored
in Git.

## Boundaries

- Do not store captured credentials, packet captures, forensic images, private
  assessment evidence, or guest disk images in Git.
- Do not enable VM autostart by default.
- Do not add a physical bridge or macvtap interface.
- Do not add libvirt forwarding to `security-lab`.
- Do not install `kali-linux-everything`; add tools only for an explicitly
  scoped and authorized assessment.
- Security testing is limited to systems Jeremy owns or has explicit
  authorization to assess.
