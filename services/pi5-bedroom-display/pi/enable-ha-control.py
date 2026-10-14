#!/usr/bin/env python3
"""Enable authenticated Kodi HTTP for one HA IPv4 address; display must be stopped.

Creates credentials only in a root-private Pi file, never in stdout or source.
Existing configuration refuses overwrite. Preserve the private checkpoint.
"""
import argparse
from datetime import datetime, timezone
import ipaddress
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import xml.etree.ElementTree as ET

PORT = 8080
CREDENTIALS = Path('/etc/edsys-bedroom-kodi-control.json')


def firewall_policy(text, address):
    ip = ipaddress.IPv4Address(address)
    if ip.is_unspecified or ip.is_multicast or ip.is_loopback:
        raise ValueError('A specific HA LAN IPv4 address is required')
    anchor = '    type filter hook input priority filter; policy drop;\n'
    if text.count(anchor) != 1 or 'EdSys HA Kodi HTTP' in text:
        raise ValueError('Unexpected or already configured firewall')
    rule = f'    iifname "eth0" ip saddr {ip} tcp dport {PORT} ct state new accept comment "EdSys HA Kodi HTTP"\n'
    return text.replace(anchor, anchor + rule)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--ha-address', required=True)
    p.add_argument('--apply', action='store_true')
    a = p.parse_args()
    if os.geteuid() != 0 or subprocess.check_output(['hostname'], text=True).strip() != 'edsys-edge-livingroom':
        raise RuntimeError('Reviewed Pi/root only')
    if subprocess.run(['systemctl','is-active','--quiet','edsys-bedroom-display']).returncode == 0:
        raise RuntimeError('Stop only the display unit before changing its profile')
    firewall = Path('/etc/nftables.conf')
    original = firewall.read_text()
    candidate = firewall_policy(original, a.ha_address)
    settings = Path('/var/lib/edsys-voice/.kodi/userdata/guisettings.xml')
    tree = ET.parse(settings)
    nodes = {n.get('id'):n for n in tree.getroot().iter('setting')}
    if CREDENTIALS.exists() or nodes['services.webserver'].text != 'false':
        raise RuntimeError('Existing control configuration; inspect without replay')
    if not a.apply:
        print(json.dumps({'candidate':True,'port':PORT,'allowed_sources':1,'display_stopped':True}))
        return
    checkpoint = Path('/var/backups/edsys-bedroom-display')/('ha-control-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
    checkpoint.mkdir(mode=0o700)
    shutil.copy2(firewall, checkpoint/'nftables.conf')
    shutil.copy2(settings, checkpoint/'guisettings.xml')
    candidate_file = checkpoint/'nftables-candidate.conf'
    candidate_file.write_text(candidate)
    subprocess.run(['nft','-c','-f',str(candidate_file)],check=True)
    credential = {'username':'ha-bedroom-pi','password':secrets.token_urlsafe(36),'port':PORT}
    fd = os.open(CREDENTIALS,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'w') as f:json.dump(credential,f)
    values = {'services.webserver':'true','services.webserverport':str(PORT),
              'services.webserverauthentication':'true','services.webserverssl':'false',
              'services.webserverusername':credential['username'],
              'services.webserverpassword':credential['password']}
    for key,value in values.items():
        node = nodes[key];node.text = value;node.attrib.pop('default',None)
    tree.write(settings,encoding='utf-8',xml_declaration=True)
    if firewall.read_text() != original:
        raise RuntimeError('Concurrent firewall change; profile saved but firewall not applied')
    # Append only our live rule; do not flush other live rules while applying.
    subprocess.run(['nft','add','rule','inet','edsys_filter','input','iifname','eth0',
                    'ip','saddr',a.ha_address,'tcp','dport',str(PORT),'ct','state','new',
                    'accept','comment','"EdSys HA Kodi HTTP"'],check=True)
    firewall.write_text(candidate)
    print(json.dumps({'configured':True,'port':PORT,'ha_only_firewall':True,'private_checkpoint':str(checkpoint)}))


if __name__ == '__main__':main()
