#!/usr/bin/env python3
"""Native Kodi integration using private endpoint credentials. Never prints them."""
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'home-assistant-voice'))
from configure import client,ws,request,submit


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--host',required=True)
    p.add_argument('--credentials',required=True,type=Path)
    p.add_argument('--private-state',required=True,type=Path)
    p.add_argument('--apply',action='store_true')
    p.add_argument('--resume',action='store_true',help='Read the saved native flow before continuing its current step')
    a=p.parse_args()
    if a.credentials.stat().st_mode & 0o077:raise RuntimeError('Credentials must be private')
    api=client()
    existing=[e for e in ws(api,{'type':'config_entries/get'}) if e['domain']=='kodi']
    if existing:raise RuntimeError('Existing Kodi entries; bind/review rather than duplicate')
    if not a.apply:
        print(json.dumps({'candidate':True,'native_integration':'kodi','http_only':True}));return
    a.private_state.mkdir(parents=True,exist_ok=True,mode=0o700)
    credential=json.loads(a.credentials.read_text())
    base='api/config/config_entries/flow'
    state=a.private_state/'integration-flow.json'
    if a.resume:
        previous=json.loads(state.read_text())
        flow=api({'operation':'request','method':'GET','path':base+'/'+previous['flow_id']})
    else:
        if state.exists():raise RuntimeError('Saved flow exists; inspect/resume instead of creating another')
        flow=request(api,base,{'handler':'kodi','show_advanced_options':True})
    for _ in range(4):
        state.write_text(json.dumps(flow));state.chmod(0o600)
        if flow.get('errors'):raise RuntimeError('Native flow returned errors; inspect private state without replay')
        if flow.get('type')=='create_entry':
            # Do not print create_entry.data, which contains endpoint credentials.
            result=flow.get('result',{})
            print(json.dumps({'created':True,'entry_id':result.get('entry_id'),'http_only':True}));return
        step=flow.get('step_id');path=base+'/'+flow['flow_id']
        if step=='user':data={'host':a.host,'port':credential['port'],'ssl':False}
        elif step=='credentials':data={k:credential[k] for k in ['username','password']}
        elif step=='ws_port':data={'ws_port':0}
        else:raise RuntimeError('Unexpected native step; inspect without replay')
        flow=submit(api,path,flow,data)
    state.write_text(json.dumps(flow));state.chmod(0o600)
    raise RuntimeError('Flow did not finish; inspect without replay')


if __name__=='__main__':main()
