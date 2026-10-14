#!/usr/bin/env python3
"""Apply reviewed pilot aliases and files; no actuator, credential or restart.

Bindings and recovery records are private files outside Git. --apply records an
already-authorized operation; it does not grant authorization by itself.
"""
import argparse
import json
from pathlib import Path
import shlex
import subprocess

from configure import client, ws


WORKER = r'''
import json,os,pathlib,sys,tempfile
p=json.load(sys.stdin);config=p['config'];root=pathlib.Path('/config/edsys_voice_knowledge')
if root.is_symlink() or set(config['targets'])!={'gadget_lab_light','master_bedroom_tv','master_bedroom_plex'}:raise RuntimeError('Invalid pilot scope')
destinations={root/'pilot.json':json.dumps(config),pathlib.Path('/config/custom_sentences/en/edsys_pilot.yaml'):json.dumps(p['sentences'])}
for path,text in destinations.items():
 if path.is_symlink():raise RuntimeError('Unexpected symlink')
 if path.exists() and path.read_text()!=text:raise RuntimeError('Existing pilot differs; inspect before replacing')
for path,text in destinations.items():
 path.parent.mkdir(parents=True,exist_ok=True)
 fd,tmp=tempfile.mkstemp(prefix='.pilot-',dir=path.parent)
 with os.fdopen(fd,'w') as f:f.write(text);f.flush();os.fsync(f.fileno())
 os.chmod(tmp,0o600);os.replace(tmp,path)
print(json.dumps({'pilot_files_written':len(destinations),'credential_exported':False}))
'''


def sentences(config):
    camera_names = sorted({alias for camera in config['cameras'].values() for alias in camera['aliases']})
    return {'language':'en', 'intents': {
        'EdSysPilotCamera': {'data':[{'sentences':['(show|display|view|bring up|pull up|open) [the] {edsys_pilot_camera} [camera] [on [the] (screen|nimo)]']}]},
        'EdSysPilotPlex': {'data':[{'sentences':['(open|launch|bring up) Plex [on [the] TV] (in|on) [the] {edsys_pilot_bedroom} [TV]']}]},
    }, 'lists': {
        'edsys_pilot_camera': {'values': camera_names},
        'edsys_pilot_bedroom': {'values': ['master bedroom','main bedroom','primary bedroom']},
    }}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bindings',type=Path,required=True)
    parser.add_argument('--private-state',type=Path,required=True)
    parser.add_argument('--apply',action='store_true')
    parser.add_argument('--resume',action='store_true',help='Resume only after inspecting the saved baseline and actual partial changes')
    args=parser.parse_args()
    config=json.loads(args.bindings.read_text())
    if config.get('version')!=1 or set(config['targets'])!={'gadget_lab_light','master_bedroom_tv','master_bedroom_plex'}:
        raise RuntimeError('Invalid pilot scope')
    api=client();areas=ws(api,{'type':'config/area_registry/list'});before=[];changes=[]
    by_name={area['name']:area for area in areas}
    for name,aliases in [('Gadget Lab',['lab']),('Master Bedroom',['main bedroom','primary bedroom'])]:
        area=by_name[name];before.append({'kind':'area','id':area['area_id'],'aliases':area.get('aliases',[])})
        changes.append({'type':'config/area_registry/update','area_id':area['area_id'],
                        'aliases':sorted(set(filter(None,area.get('aliases',[])))|set(aliases))})
    for key,binding in config['targets'].items():
        entity=ws(api,{'type':'config/entity_registry/get','entity_id':binding['entity_id']})
        if entity['device_id']!=binding['device_id'] or entity['config_entry_id']!=binding['config_entry_id'] or entity.get('disabled_by'):
            raise RuntimeError('Reviewed entity mapping changed; inspect before applying')
        before.append({'kind':'entity','id':binding['entity_id'],'aliases':entity.get('aliases',[]),'area_id':entity.get('area_id'),'name':entity.get('name')})
        change={'type':'config/entity_registry/update','entity_id':binding['entity_id'],
                'aliases':[None,*sorted(set(filter(None,entity.get('aliases',[])))|set(binding['aliases']))]}
        if key=='master_bedroom_plex':
            change['area_id']=by_name['Master Bedroom']['area_id']
            change['name']='Master Bedroom Plex'
        if key=='master_bedroom_tv':change['name']='Master Bedroom TV'
        changes.append(change)
    if not args.apply:
        print(json.dumps({'mode':'dry-run','native_mapping_changes':len(changes),'camera_routes':len(config['cameras']),'actuator_calls':0}));return
    args.private_state.mkdir(parents=True,exist_ok=True,mode=0o700)
    baseline=args.private_state/'mappings-before.json'
    if baseline.exists() and not args.resume:raise RuntimeError('A mapping baseline exists; inspect before reapplying')
    if not baseline.exists():
        baseline.write_text(json.dumps(before));baseline.chmod(0o600)
    applied=[]
    for change in changes:
        ws(api,change);applied.append(change)
        path=args.private_state/'mappings-applied.json';path.write_text(json.dumps(applied));path.chmod(0o600)
    command=shlex.join(['qm','guest','exec','300','--pass-stdin','1','--timeout','30','--','docker','exec','-i','homeassistant','python3','-c',WORKER])
    run=subprocess.run(['ssh','-o','BatchMode=yes','-o','ClearAllForwardings=yes','pve-node3',command],
                       input=json.dumps({'config':config,'sentences':sentences(config)}),capture_output=True,text=True,timeout=40,check=True)
    result=json.loads(run.stdout)
    if result.get('exitcode') or result.get('out-truncated'):raise RuntimeError('Pilot file write failed; inspect state before retry')
    print(result['out-data'].strip())
    print(json.dumps({'native_mapping_changes':len(applied),'actuator_calls':0,'core_restart_performed':False}))


if __name__=='__main__':main()
