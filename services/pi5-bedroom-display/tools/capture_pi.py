#!/usr/bin/env python3
"""Request one Kodi GUI frame, then wait for that exact PNG; never retry capture.

Kodi's DRM video plane may be absent in GUI screenshots. Do not capture account
authorization codes, credentials, private content or physical rooms for Git/RAG.
"""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess

WAIT = r'''
from pathlib import Path
import sys,time
p=Path(sys.argv[1]);deadline=time.monotonic()+8
while time.monotonic()<deadline:
    if p.is_file():
        b=p.read_bytes()
        if b.startswith(b'\x89PNG\r\n\x1a\n') and b.endswith(b'\x00\x00\x00\x00IEND\xaeB\x60\x82'):
            sys.stdout.buffer.write(b);break
    time.sleep(.1)
else:raise SystemExit('Exact requested capture not ready; inspect without replay')
'''


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--host',required=True)
    p.add_argument('--output',required=True,type=Path)
    args=p.parse_args()
    if not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_.-]*',args.host):p.error('Invalid SSH hostname/alias')
    destination=args.output.resolve()
    if destination.is_relative_to(Path(__file__).resolve().parents[3]):p.error('Capture must be outside Git source')
    ssh=['ssh','-o','BatchMode=yes','-o','ConnectTimeout=5',args.host]
    response=json.loads(subprocess.check_output(ssh+['sudo','-n','python3','/usr/share/kodi/addons/service.edsys.bedroom-display/local_control.py','screenshot'],text=True,timeout=10))
    remote=response.get('capture_path','')
    if not response.get('ok') or not re.fullmatch(r'/run/edsys-bedroom-display/capture-[a-fA-F0-9-]{36}\.png',remote):raise RuntimeError('Unexpected capture response')
    result=subprocess.run(ssh+['sudo','-n','python3','-',remote],input=WAIT.encode(),capture_output=True,timeout=15,check=True)
    fd=os.open(destination,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'wb') as f:f.write(result.stdout)
    print(json.dumps({'saved':str(destination),'bytes':len(result.stdout)}))
