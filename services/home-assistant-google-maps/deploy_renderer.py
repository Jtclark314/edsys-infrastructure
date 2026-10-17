#!/usr/bin/env python3
"""Add one static tailnet-only renderer route; preserve all existing Serve routes."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
from build import build

PORT='8450'
ROUTE='/v1.0.0/renderer.html'

def command(*args):
    return subprocess.run(['tailscale',*args],capture_output=True,text=True,check=True,timeout=45).stdout

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--private-state',type=Path,required=True)
    parser.add_argument('--apply',action='store_true')
    args=parser.parse_args();private=args.private_state
    before=json.loads(command('serve','status','--json'))
    if PORT in before.get('TCP',{}):raise RuntimeError('Renderer port already in use; inspect rather than replay')
    host=json.loads(command('status','--json'))['Self']['DNSName'].rstrip('.')
    url=f'https://{host}:{PORT}{ROUTE}'
    target=private.parent/'renderer'/'v1.0.0'/'renderer.html'
    content=build();digest=hashlib.sha256(content.encode()).hexdigest()
    if target.exists() and target.read_text()!=content:raise RuntimeError('Immutable renderer differs; use a new version')
    print(json.dumps({'port':PORT,'static_files':1,'sha256':digest,'tailnet_only':True,'google_requests':0}),flush=True)
    if not args.apply:return
    checkpoint=private/'serve-before.json'
    if checkpoint.exists():raise RuntimeError('Prior checkpoint exists; inspect before retry')
    checkpoint.write_text(json.dumps(before));checkpoint.chmod(0o600)
    target.parent.mkdir(parents=True,exist_ok=True);target.write_text(content);target.chmod(0o644)
    if hashlib.sha256(target.read_bytes()).hexdigest()!=digest:raise RuntimeError('Renderer readback differs')
    command('serve','--bg',f'--https={PORT}',f'--set-path={ROUTE}',str(target))
    after=json.loads(command('serve','status','--json'))
    expected=json.loads(json.dumps(before));expected.setdefault('TCP',{})[PORT]={'HTTPS':True}
    expected.setdefault('Web',{})[f'{host}:{PORT}']={'Handlers':{ROUTE:{'Path':str(target)}}}
    if after!=expected:raise RuntimeError('Serve readback differs; inspect without restoring stale state')
    endpoint=private/'renderer-endpoint.json';endpoint.write_text(json.dumps({'renderer_url':url,'sha256':digest,'port':PORT,'route':ROUTE,'target':str(target)}));endpoint.chmod(0o600)
    print(json.dumps({'renderer_added_and_verified':True,'other_routes_exactly_preserved':True,'key_configured':False}))

if __name__=='__main__':main()
