#!/usr/bin/env python3
"""Create a separate empty-key trial; never update the original Map dashboard."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).parent
sys.path.insert(0,str(ROOT.parent/'home-assistant-voice'))
from configure import client,ws
from build import build

PATH='google-maps-trial'
VERSION='v1.0.2'
RESOURCE=f'/local/edsys-google-maps/{VERSION}/edsys-google-maps-trial.js'

def guest(code,payload,*,container='homeassistant'):
    import shlex
    if container not in ('homeassistant','hassio_supervisor'):raise ValueError('Unsupported container')
    cmd=shlex.join(['qm','guest','exec','300','--pass-stdin','1','--timeout','30','--','docker','exec','-i',container,'python3','-c',code])
    run=subprocess.run(['ssh','-o','BatchMode=yes','-o','ClearAllForwardings=yes','pve-node3',cmd],input=json.dumps(payload),capture_output=True,text=True,timeout=45,check=True)
    result=json.loads(run.stdout)
    if result.get('exitcode') or result.get('out-truncated'):raise RuntimeError('Guest operation failed; inspect before retry')
    return json.loads(result['out-data'])

def verify_initial(api,data):
    # The existing operator API intentionally masks even an empty api_key.
    masked=json.loads(json.dumps(data));masked['views'][0]['cards'][0]['api_key']='[redacted]'
    if ws(api,{'type':'lovelace/config','url_path':PATH})!=masked:raise RuntimeError('Trial API readback differs; inspect without replay')
    result=guest(r'''
import json,pathlib,sys,time
expected=json.load(sys.stdin)
for attempt in range(16):
 try:actual=json.loads(pathlib.Path('/config/.storage/lovelace.google_maps_trial').read_text())['data']['config']
 except (FileNotFoundError,KeyError):actual=None
 if actual==expected:break
 time.sleep(.5)
print(json.dumps({'exact_initial_config':actual==expected}))
''',data)
    if not result['exact_initial_config']:raise RuntimeError('Exact persisted initial config differs; inspect without replay')


def candidate(entities,renderer_url):
    import re
    from urllib.parse import urlparse
    if not 1 <= len(entities) <= 10 or len(set(entities)) != len(entities) or any(not re.fullmatch(r'(person|device_tracker|zone)\.[a-z0-9_]+',eid) for eid in entities):
        raise ValueError('Explicit existing map locations required')
    url=urlparse(renderer_url)
    if url.scheme!='https' or not url.hostname or url.username or url.password or url.query or url.fragment or not url.path.endswith('/renderer.html'):raise ValueError('Dedicated HTTPS renderer required')
    return {'views':[{'title':'Google Maps Trial','path':'trial','type':'panel','cards':[
        {'type':'custom:edsys-google-maps-trial','title':'Google Maps Trial','entities':entities,'hours_to_show':2,'api_key':'','renderer_url':renderer_url}]}]}


INSTALL=r'''
import hashlib,json,os,pathlib,sys,tempfile
p=json.load(sys.stdin);root=pathlib.Path('/config/www/edsys-google-maps/v1.0.2')
for parent in [root,*root.parents]:
 if parent.is_symlink():raise RuntimeError('Unexpected asset symlink')
root.mkdir(parents=True,exist_ok=True)
if set(p['files'])!={'core.js','edsys-google-maps-trial.js'}:raise RuntimeError('Asset list changed')
for name,text in p['files'].items():
 destination=root/name
 if destination.exists() and destination.read_text()!=text:raise RuntimeError('Immutable asset differs')
for name,text in p['files'].items():
 destination=root/name
 if not destination.exists():
  fd,tmp=tempfile.mkstemp(prefix='.asset-',dir=root)
  with os.fdopen(fd,'w') as f:f.write(text);f.flush();os.fsync(f.fileno())
  os.chmod(tmp,0o644);os.replace(tmp,destination)
 if hashlib.sha256(destination.read_bytes()).hexdigest()!=p['sha256'][name]:raise RuntimeError('Asset readback mismatch')
print(json.dumps({'assets_verified':len(p['files'])}))
'''


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--private-state',type=Path,required=True)
    parser.add_argument('--apply',action='store_true')
    args=parser.parse_args();private=args.private_state
    api=client();before=json.loads((private/'map-before.json').read_text());selected=json.loads((private/'selected-entities.json').read_text())
    if ws(api,{'type':'lovelace/config','url_path':'map'})!=before:raise RuntimeError('Original map changed; inspect before preparing trial')
    dashboards=ws(api,{'type':'lovelace/dashboards/list'})
    if any(d.get('url_path')==PATH for d in dashboards):raise RuntimeError('Trial already exists; inspect rather than replay creation')
    endpoint=json.loads((private/'renderer-endpoint.json').read_text())
    data=candidate(selected['entities'],endpoint['renderer_url']);assets={name:(ROOT/name).read_text() for name in ('core.js','edsys-google-maps-trial.js')}
    hashes={name:hashlib.sha256(value.encode()).hexdigest() for name,value in assets.items()}
    file=private/'trial-candidate.json';file.write_text(json.dumps(data));file.chmod(0o600)
    resources=ws(api,{'type':'lovelace/resources'})
    if any(r.get('url')==RESOURCE for r in resources):raise RuntimeError('Trial resource already registered; inspect first')
    print(json.dumps({'trial':PATH,'selected_entities':len(selected['entities']),'key_configured':False,'original_map_unchanged':True,'asset_sha256':hashes}),flush=True)
    if not args.apply:return
    # Existing guest access; only the reviewed immutable public source is transported.
    guest(INSTALL,{'files':assets,'sha256':hashes})
    if ws(api,{'type':'lovelace/resources'})!=resources or ws(api,{'type':'lovelace/dashboards/list'})!=dashboards:raise RuntimeError('Concurrent frontend metadata change; inspect before continuing')
    applied={}
    def record():
        path=private/'deployment.json';path.write_text(json.dumps(applied));path.chmod(0o600)
    applied['resource']=ws(api,{'type':'lovelace/resources/create','res_type':'module','url':RESOURCE});record()
    applied['dashboard']=ws(api,{'type':'lovelace/dashboards/create','url_path':PATH,'title':'Google Maps Trial','icon':'mdi:google-maps','show_in_sidebar':True,'require_admin':True});record()
    ws(api,{'type':'lovelace/config/save','url_path':PATH,'config':data})
    verify_initial(api,data)
    if ws(api,{'type':'lovelace/config','url_path':'map'})!=before:raise RuntimeError('Original map readback changed; inspect without restoring a stale baseline')
    applied['saved_and_verified']=True;applied['asset_sha256']=hashes;record()
    print(json.dumps({'trial_saved_and_verified':True,'original_map_exactly_preserved':True,'key_configured':False,'google_requests':0}))


if __name__=='__main__':main()
