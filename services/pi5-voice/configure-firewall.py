#!/usr/bin/env python3
"""Add one HA-only voice rule to the established Pi firewall, preserving SSH."""
from pathlib import Path
import subprocess

config = Path('/etc/nftables.conf')
text = config.read_text()
ssh_rule = '    iifname "eth0" ip saddr 192.168.50.50 tcp dport 22 ct state new accept\n'
rule = '    iifname "eth0" ip saddr 192.168.50.75 tcp dport 6053 ct state new accept comment "Home Assistant voice satellite"\n'
if rule not in text:
    if text.count(ssh_rule) != 1 or 'table inet edsys_filter {' not in text:
        raise SystemExit('Unexpected firewall layout; no changes made')
    candidate = config.with_name('nftables.voice-candidate')
    candidate.write_text(text.replace(ssh_rule, ssh_rule + rule))
    try:
        subprocess.run(['nft', '-c', '-f', str(candidate)], check=True)
        candidate.chmod(config.stat().st_mode & 0o777)
        candidate.replace(config)
        subprocess.run(['nft', '-f', str(config)], check=True)
    finally:
        candidate.unlink(missing_ok=True)
