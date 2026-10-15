#!/usr/bin/env python3
"""Bind the two reviewed remotes; back up component/config before installation.

No provider credential, pipeline, registry or actuator changes. --apply records
existing authorization. Install/test source and reload the integration separately.
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import shlex
import subprocess
from configure import client, ws

WORKER=r'''
import json,os,pathlib,re,shutil,sys,tempfile
p=json.load(sys.stdin);name=p['recovery'];config=p['config'];root=pathlib.Path('/config/edsys_voice_knowledge')
if not re.fullmatch(r'media-remote-\d{8}T\d{6}Z',name) or set(config['targets'])!={'tv','pi'}:raise RuntimeError('Unexpected media configuration')
path=root/'media_remote.json'
if root.is_symlink() or path.is_symlink():raise RuntimeError('Unexpected symlink')
if path.exists():raise RuntimeError('Media configuration already exists; inspect before replacing')
backup=root/'recovery'/name;backup.mkdir(mode=0o700,parents=True,exist_ok=False)
os.chmod(root/'recovery',0o700)
component=pathlib.Path('/config/custom_components/edsys_voice_knowledge')
if component.is_symlink():raise RuntimeError('Unexpected component symlink')
shutil.copytree(component,backup/'component',symlinks=True)
(backup/'manifest.json').write_text(json.dumps({'media_config_existed':False,'component':str(component)}))
fd,tmp=tempfile.mkstemp(prefix='.media-remote-',dir=root)
with os.fdopen(fd,'w') as f:json.dump(config,f);f.flush();os.fsync(f.fileno())
os.chmod(tmp,0o600);os.replace(tmp,path)
print(json.dumps({'private_config_written':True,'recovery':str(backup),'actuator_calls':0}))
'''

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tv',required=True)
    parser.add_argument('--pi',required=True)
    parser.add_argument('--private-state',type=Path,required=True)
    parser.add_argument('--apply',action='store_true')
    args=parser.parse_args();api=client();targets={}
    for role,eid,platform,name in [('tv',args.tv,'webostv','Master Bedroom TV'),('pi',args.pi,'kodi','Bedroom Pi Plex')]:
        if not __import__('re').fullmatch(r'media_player\.[a-z0-9_]+',eid):raise RuntimeError('Explicit media entity required')
        entity=ws(api,{'type':'config/entity_registry/get','entity_id':eid})
        if entity['platform']!=platform or entity.get('disabled_by') or not entity.get('device_id') or not entity.get('config_entry_id'):raise RuntimeError('Unexpected media identity')
        if entity.get('options',{}).get('conversation',{}).get('should_expose') is not True:raise RuntimeError('Review Assist exposure before binding')
        area=api({'operation':'template','method':'POST','path':'api/template','data':{'template':"{{ area_id('"+eid+"') }}"}})
        if area!='master_bedroom':raise RuntimeError('Both remotes must currently belong to Master Bedroom')
        targets[role]={k:entity[k] for k in ['entity_id','device_id','config_entry_id']}
        targets[role].update(name=name,area_id=area)
    config={'version':1,'targets':targets,'pi_sources':['PC']}
    if not args.apply:
        print(json.dumps({'mode':'dry-run','reviewed_roles':list(targets),'actuator_calls':0}));return
    args.private_state.mkdir(parents=True,exist_ok=True,mode=0o700)
    pipeline=ws(api,{'type':'assist_pipeline/pipeline/list'})
    baseline=args.private_state/'pipeline-before.json'
    if baseline.exists():raise RuntimeError('Recovery record exists; inspect before retrying')
    baseline.write_text(json.dumps(pipeline));baseline.chmod(0o600)
    path=args.private_state/'media-bindings.json';path.write_text(json.dumps(config));path.chmod(0o600)
    payload={'config':config,'recovery':'media-remote-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}
    command=shlex.join(['qm','guest','exec','300','--pass-stdin','1','--timeout','30','--','docker','exec','-i','homeassistant','python3','-c',WORKER])
    process=subprocess.run(['ssh','-o','BatchMode=yes','-o','ClearAllForwardings=yes','pve-node3',command],input=json.dumps(payload),capture_output=True,text=True,timeout=45,check=True)
    result=json.loads(process.stdout)
    if result.get('exitcode') or result.get('out-truncated'):raise RuntimeError('Private media configuration failed; inspect before retry')
    outcome=json.loads(result['out-data']);path=args.private_state/'recovery.json';path.write_text(json.dumps(outcome));path.chmod(0o600)
    print(json.dumps(outcome))

if __name__=='__main__':main()
