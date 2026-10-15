#!/usr/bin/env python3
"""Create the dedicated Pi dashboard and append one link to the accepted media page."""
import argparse
import json
from pathlib import Path
from deploy import client,ws,validate_binding
from full_dashboard import URL,build,add_link


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binding',type=Path,required=True)
    parser.add_argument('--private-state',type=Path,required=True)
    parser.add_argument('--apply',action='store_true')
    args=parser.parse_args();binding=json.loads(args.binding.read_text());api=client();validate_binding(api,binding)
    dashboards=ws(api,{'type':'lovelace/dashboards/list'})
    if any(d['url_path']==URL for d in dashboards):raise RuntimeError('Pi dashboard already exists; inspect instead of overwriting')
    before=ws(api,{'type':'lovelace/config','url_path':'edsys-media'})
    candidate=build(binding);media=add_link(before)
    args.private_state.mkdir(parents=True,exist_ok=True,mode=0o700)
    for name,value in [('dashboards-before.json',dashboards),('media-before.json',before),('media-after.json',media),('dashboard-candidate.json',candidate)]:
        path=args.private_state/name
        if path.exists():raise RuntimeError('A dashboard deployment record exists; inspect before retry')
        path.write_text(json.dumps(value));path.chmod(0o600)
    print(json.dumps({'new_dashboard':URL,'views':len(candidate['views']),'existing_media_change':'one navigation link'}),flush=True)
    if not args.apply:return
    for eid in [binding['screen_sensor'],*[b['entity_id'] for b in binding['cameras'].values()],*[b['entity_id'] for b in binding['sources'].values()]]:
        if not api({'operation':'state','method':'GET','path':'api/states/'+eid}).get('entity_id'):raise RuntimeError('Dashboard entity missing')
    if ws(api,{'type':'lovelace/config','url_path':'edsys-media'})!=before:raise RuntimeError('Concurrent media edit; not saved')
    created=ws(api,{'type':'lovelace/dashboards/create','url_path':URL,'title':'Bedroom Pi','icon':'mdi:raspberry-pi','show_in_sidebar':True,'require_admin':False})
    path=args.private_state/'dashboard-created.json';path.write_text(json.dumps(created));path.chmod(0o600)
    ws(api,{'type':'lovelace/config/save','url_path':URL,'config':candidate})
    if ws(api,{'type':'lovelace/config','url_path':URL})!=candidate:raise RuntimeError('Dashboard readback differs; inspect before retry')
    if ws(api,{'type':'lovelace/config','url_path':'edsys-media'})!=before:raise RuntimeError('Concurrent media edit; link was not saved')
    ws(api,{'type':'lovelace/config/save','url_path':'edsys-media','config':media})
    if ws(api,{'type':'lovelace/config','url_path':'edsys-media'})!=media:raise RuntimeError('Media link readback differs')
    print(json.dumps({'saved_and_read_back':True,'url':'/'+URL+'/player'}))

if __name__=='__main__':main()
