#!/usr/bin/env python3
"""Install the narrowly scoped Sunshine input guard before starting the host."""
import ipaddress
import json
import os
from pathlib import Path
import re
import subprocess


def render(config, exists=False):
    interfaces = [config["lan_interface"], config["tailnet_interface"]]
    if not all(re.fullmatch(r"[A-Za-z0-9_.:-]+", i) for i in interfaces):
        raise ValueError("Invalid interface")
    addresses = {}
    for key in ("lan_clients", "tailnet_clients"):
        values = config[key]
        if not isinstance(values, list) or not values:
            raise ValueError("An exact client list is required")
        addresses[key] = [str(ipaddress.IPv4Address(v)) for v in values]
    lines = ["delete table inet edsys_sunshine"] if exists else []
    lines += ["table inet edsys_sunshine {", "chain input {",
              "type filter hook input priority -20; policy accept;",
              'iifname "lo" tcp dport { 47984, 47989, 47990, 48010 } counter accept',
              'iifname "lo" udp dport { 47998, 47999, 48000, 48002, 48010 } counter accept',
              "tcp dport 47990 counter drop"]
    for iface, key in zip(interfaces, ("lan_clients", "tailnet_clients")):
        peers = ", ".join(addresses[key])
        for proto, ports in [("tcp", "47984, 47989, 48010"), ("udp", "47998, 47999, 48000, 48002, 48010")]:
            lines += [f'iifname "{iface}" ip saddr {{ {peers} }} {proto} dport {{ {ports} }} counter accept']
    lines += ["tcp dport { 47984, 47989, 48010 } counter drop",
              "udp dport { 47998, 47999, 48000, 48002, 48010 } counter drop", "}", "}"]
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    if os.geteuid() != 0:
        raise SystemExit("Root required")
    path = Path("/etc/edsys-sunshine/clients.json")
    st = path.stat()
    if path.is_symlink() or st.st_uid != 0 or st.st_mode & 0o077:
        raise SystemExit("Private root-owned 0600 client configuration required")
    config = json.loads(path.read_text())
    exists = subprocess.run(["/usr/sbin/nft", "list", "table", "inet", "edsys_sunshine"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0
    rules = render(config, exists)
    subprocess.run(["/usr/sbin/nft", "-c", "-f", "-"], input=rules, text=True, check=True)
    subprocess.run(["/usr/sbin/nft", "-f", "-"], input=rules, text=True, check=True)
    print("Exact-peer Sunshine guard installed; web administration is loopback-only")
