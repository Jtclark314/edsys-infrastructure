#!/usr/bin/env python3
"""Apply only the Kali streaming input table; never enable forwarding."""
import ipaddress
import json
from pathlib import Path
import subprocess
import sys

TCP = '47984, 47989, 48010'
UDP = '47998, 47999, 48000, 48002, 48010'

def render(role, peer, exists=False):
    if role not in ('guest', 'relay'):
        raise ValueError('Unknown guard role')
    peer = str(ipaddress.IPv4Address(peer))
    table = 'edsys_kali_stream'
    iface = 'eth0' if role == 'guest' else 'tailscale0'
    lines = [f'delete table inet {table}'] if exists else []
    lines += [f'table inet {table} {{', 'chain input {',
              'type filter hook input priority -25; policy accept;',
              f'iifname "lo" tcp dport {{ {TCP}, 47990 }} accept',
              f'iifname "lo" udp dport {{ {UDP} }} accept',
              'tcp dport 47990 counter drop',
              f'iifname "{iface}" ip saddr {peer} tcp dport {{ {TCP} }} counter accept',
              f'iifname "{iface}" ip saddr {peer} udp dport {{ {UDP} }} counter accept',
              f'tcp dport {{ {TCP} }} counter drop',
              f'udp dport {{ {UDP} }} counter drop', '}', '}']
    return '\n'.join(lines) + '\n'

def main():
    role = sys.argv[1]
    path = Path('/etc/edsys-kali-stream/relay.json')
    if role == 'guest':
        peer = '192.168.77.1'
    else:
        st = path.stat()
        if path.is_symlink() or st.st_uid != 0 or st.st_mode & 0o077:
            raise SystemExit('Private root-owned relay configuration required')
        peer = json.loads(path.read_text())['peer_address']
    exists = subprocess.run(['nft', 'list', 'table', 'inet', 'edsys_kali_stream'],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0
    rules = render(role, peer, exists)
    for args in (['nft', '-c', '-f', '-'], ['nft', '-f', '-']):
        subprocess.run(args, input=rules, text=True, check=True)

if __name__ == '__main__':
    main()
