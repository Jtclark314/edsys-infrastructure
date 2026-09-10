#!/usr/bin/env python3
"""Prepare private SSH material and a downloadable Windows bundle; no deployment."""
from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import shutil
import subprocess

SOURCE = Path(__file__).resolve().parent


def tailnet(value: str) -> str:
    ip = ipaddress.ip_address(value)
    if ip.version != 4 or ip not in ipaddress.ip_network('100.64.0.0/10') or str(ip) != value:
        raise ValueError('Expected a canonical Tailnet IPv4 address')
    return value


def quote(value: str) -> str:
    if any(c in value for c in '\r\n\x00'):
        raise ValueError('Unsafe SSH configuration value')
    return '"' + value.replace('\\', '\\\\').replace('"', '\\"') + '"'


def write_private(path: Path, data: str) -> None:
    # Refuse existing paths: preparation never overwrites a prior bundle/key.
    with path.open('x', encoding='utf-8') as f:
        f.write(data)
    path.chmod(0o600)


def prepare(bundle: Path, identity: Path, hub: str, laptop: str) -> dict:
    tailnet(hub)
    tailnet(laptop)
    if hub == laptop:
        raise ValueError('Hub and laptop must be distinct')
    bundle = bundle.absolute()
    identity = identity.absolute()
    if not re.fullmatch(r'/home/jeremy/\.codex/operator-checkpoints/[a-zA-Z0-9/_-]+', str(bundle)):
        raise ValueError('Bundle must use the private operator-checkpoints directory')
    if bundle.exists() or identity.exists() or Path(str(identity) + '.pub').exists():
        raise ValueError('Refusing to overwrite an existing bundle or identity')
    for parent in (bundle.parent, identity.parent):
        if parent.resolve() != parent or not parent.is_dir():
            raise ValueError('Parents must be existing real directories')
    os.umask(0o077)
    bundle.mkdir(mode=0o700)
    windows = bundle / 'windows'
    windows.mkdir(mode=0o700)
    subprocess.run(['ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-C',
                    'edsys-9950x-to-work-laptop', '-f', str(identity)], check=True)
    identity.chmod(0o600)
    public = ' '.join(Path(str(identity) + '.pub').read_text().split()[:2])
    manifest = dict(schemaVersion=1, computer='THOMPSON-LC086', user='thompson\\jclark',
                    hubAddress=hub, laptopAddress=laptop, publicKey=public)
    write_private(windows / 'access.json', json.dumps(manifest, indent=2) + '\n')
    for name in ['Manage-WorkLaptopAccess.ps1', 'Start-WorkLaptopAccess.ps1', 'Invoke-WorkLaptopAccess.ps1']:
        shutil.copyfile(SOURCE / name, windows / name)
        (windows / name).chmod(0o600)
    hashes = {name: hashlib.sha256((windows / name).read_bytes()).hexdigest() for name in
              ['Manage-WorkLaptopAccess.ps1', 'Start-WorkLaptopAccess.ps1', 'Invoke-WorkLaptopAccess.ps1', 'access.json']}
    write_private(windows / 'bundle.json', json.dumps(dict(schemaVersion=1,
                  hubUploadDirectory=str(bundle), hashes=hashes), indent=2) + '\n')
    # This file is usable only after the returned public host key is pinned.
    # No global SSH configuration or known_hosts file is changed.
    config = '\n'.join([
        'Host work-laptop-admin', f'    HostName {laptop}', '    Port 22',
        '    User ' + quote('thompson\\jclark'), '    IdentityFile ' + quote(str(identity)),
        '    IdentitiesOnly yes', '    BatchMode yes', '    PreferredAuthentications publickey',
        '    PasswordAuthentication no', '    KbdInteractiveAuthentication no',
        '    StrictHostKeyChecking yes', '    UserKnownHostsFile ' + quote(str(bundle / 'known_hosts')),
        '    GlobalKnownHostsFile /dev/null', '    ForwardAgent no', '    ClearAllForwardings yes',
        '    ConnectTimeout 10', '    ServerAliveInterval 30', '    ServerAliveCountMax 3', '',
    ])
    write_private(bundle / 'hub-ssh.conf', config)
    write_private(bundle / 'known_hosts', '')
    return {'bundle': str(bundle), 'identity': str(identity), 'status': 'prepared-not-installed'}


def pin(bundle: Path) -> dict:
    """Pin only a key returned through the laptop's already trusted outbound SSH."""
    manifest = json.loads((bundle / 'windows' / 'access.json').read_text())
    laptop = tailnet(manifest['laptopAddress'])
    key_path = bundle / 'host-key.pub'
    parts = key_path.read_text().strip().split()
    if len(parts) not in (2, 3) or parts[0] != 'ssh-ed25519':
        raise ValueError('Expected the returned Ed25519 host public key')
    subprocess.run(['ssh-keygen', '-lf', str(key_path)], check=True, capture_output=True)
    line = laptop + ' ' + ' '.join(parts[:2]) + '\n'
    known = bundle / 'known_hosts'
    old = known.read_text()
    if old and old != line:
        raise ValueError('Host key changed; manual verification required')
    known.write_text(line)
    known.chmod(0o600)
    return {'status': 'host-key-pinned', 'client_config': str(bundle / 'hub-ssh.conf')}


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--bundle', required=True, type=Path)
    p.add_argument('--identity', type=Path)
    p.add_argument('--hub-address')
    p.add_argument('--laptop-address')
    p.add_argument('--pin-returned-host-key', action='store_true')
    args = p.parse_args()
    if args.pin_returned_host_key:
        result = pin(args.bundle)
    else:
        if not all([args.identity, args.hub_address, args.laptop_address]):
            p.error('Preparation requires --identity, --hub-address, and --laptop-address')
        result = prepare(args.bundle, args.identity, args.hub_address, args.laptop_address)
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
