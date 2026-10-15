#!/usr/bin/env python3
"""Prepare reviewed manual-screen bindings; no provider, registry or actuator change."""
import argparse
from datetime import datetime,timezone
import json
import re
import secrets
from pathlib import Path
import shlex
import subprocess
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'home-assistant-voice'))
from configure import client,ws

WORKER=r'''
import json,pathlib,shutil,sys,os,tempfile
p=json.load(sys.stdin);root=pathlib.Path('/config/edsys_voice_knowledge');path=root/'bedroom_screen.json'
pilot=json.loads((root/'pilot.json').read_text())
if p.get('apply'):
 if path.exists() or path.is_symlink():raise RuntimeError('Screen configuration already exists; inspect before retry')
 backup=root/'recovery'/p['recovery'];backup.mkdir(parents=True,mode=0o700,exist_ok=False)
 shutil.copytree('/config/custom_components/edsys_voice_knowledge',backup/'component',symlinks=True)
 for name in ['pilot.json','media_remote.json']:shutil.copy2(root/name,backup/name)
 fd,tmp=tempfile.mkstemp(prefix='.bedroom-screen-',dir=root)
 with os.fdopen(fd,'w') as f:json.dump(p['config'],f);f.flush();os.fsync(f.fileno())
 os.chmod(tmp,0o600);os.replace(tmp,path)
print(json.dumps({'cameras':pilot['cameras'],'recovery':p.get('recovery') if p.get('apply') else None,'applied':bool(p.get('apply'))}))
'''


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--media-binding',type=Path,required=True)
    parser.add_argument('--selected-sources',type=Path,required=True)
    parser.add_argument('--private-state',type=Path,required=True)
    parser.add_argument('--apply',action='store_true')
    args=parser.parse_args();api=client();selection=json.loads(args.selected_sources.read_text())
    if set(selection)!={'family','home','reminders','bedside'}:raise RuntimeError('Review all four selected local sources')
    binding=json.loads(args.media_binding.read_text());sources={}
    for key,eid in selection.items():
        expected={'family':'calendar','home':'calendar','reminders':'todo','bedside':'light'}[key]
        if not re.fullmatch(expected+r'\.[a-z0-9_]+',eid):raise RuntimeError('Unexpected source domain')
        entity=ws(api,{'type':'config/entity_registry/get','entity_id':eid})
        if entity.get('disabled_by') or not entity.get('config_entry_id'):raise RuntimeError('Unexpected source identity')
        area=api({'operation':'template','method':'POST','path':'api/template','data':{'template':"{{ area_id('"+eid+"') }}"}})
        sources[key]={k:entity[k] for k in ['entity_id','device_id','config_entry_id']}
        sources[key].update(unique_id=entity['unique_id'],platform=entity['platform'])
        sources[key].update(name={'family':'Family','home':'Home','reminders':'Reminders','bedside':'Bedside light'}[key],area_id=None if area=='None' else area)
    args.private_state.mkdir(parents=True,exist_ok=True,mode=0o700)
    if any((args.private_state/name).exists() for name in ['binding.json','screen-config.json','recovery.json']):raise RuntimeError('A preparation record exists; inspect before retry')
    payload={'apply':args.apply,'config':{'version':1,'sources':sources,'transport_key':secrets.token_hex(32)},'recovery':'full-pi-dashboard-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}
    if args.apply:
        # Purpose-specific encryption key, never a Kodi/HA/camera credential.
        # Exclusive creation makes a partial deployment require inspection.
        provision=r'''import os,pathlib,pwd,socket,sys
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
assert os.geteuid()==0 and socket.gethostname()=='edsys-edge-livingroom'
key=sys.stdin.read().strip();assert len(bytes.fromhex(key))==32
path=pathlib.Path('/var/lib/edsys-voice/bedroom-screen-key')
fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
with os.fdopen(fd,'w') as f:f.write(key);f.flush();os.fsync(f.fileno())
user=pwd.getpwnam('edsys-voice');os.chown(path,user.pw_uid,user.pw_gid)
print('Screen transport key provisioned')'''
        subprocess.run(['ssh','-o','BatchMode=yes','edsys-edge-livingroom',shlex.join(['sudo','python3','-c',provision])],input=payload['config']['transport_key'],capture_output=True,text=True,timeout=20,check=True)
    command=shlex.join(['qm','guest','exec','300','--pass-stdin','1','--timeout','30','--','docker','exec','-i','homeassistant','python3','-c',WORKER])
    run=subprocess.run(['ssh','-o','BatchMode=yes','pve-node3',command],input=json.dumps(payload),capture_output=True,text=True,timeout=40,check=True)
    result=json.loads(run.stdout)
    if result.get('exitcode') or result.get('out-truncated'):raise RuntimeError('Private screen preparation failed; inspect before retry')
    response=json.loads(result['out-data']);binding.update(sources=sources,cameras=response['cameras'],screen_sensor='sensor.bedroom_pi_screen')
    state=api({'operation':'state','method':'GET','path':'api/states/'+binding['tv']['entity_id']})
    binding['tv_sources']=state['attributes'].get('source_list',[])
    for name,value in [('binding.json',binding),('screen-config.json',payload['config']),('recovery.json',{'ha_recovery':response['recovery'],'applied':response['applied']})]:
        path=args.private_state/name
        if path.exists():raise RuntimeError('A preparation record exists; inspect before retry')
        path.write_text(json.dumps(value));path.chmod(0o600)
    print(json.dumps({'applied':response['applied'],'reviewed_cameras':len(response['cameras']),'local_sources':len(sources),'actuator_calls':0}))

if __name__=='__main__':main()
