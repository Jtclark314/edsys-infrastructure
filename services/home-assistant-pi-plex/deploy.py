#!/usr/bin/env python3
"""Add only the Pi Plex native view and two navigation links; compare before save."""
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'home-assistant-voice'))
from configure import client,ws
from build import extend


def validate_binding(api,b):
    for key,platform in [('player','kodi'),('tv','webostv')]:
        row=ws(api,{'type':'config/entity_registry/get','entity_id':b[key]['entity_id']})
        if row.get('disabled_by') or row.get('platform')!=platform or any(row.get(k)!=b[key][k] for k in ['device_id','config_entry_id']):
            raise RuntimeError('Reviewed '+key+' identity changed')


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--binding',type=Path,required=True)
    p.add_argument('--private-state',type=Path,required=True)
    p.add_argument('--apply',action='store_true')
    a=p.parse_args();b=json.loads(a.binding.read_text());api=client()
    validate_binding(api,b)
    current=ws(api,{'type':'lovelace/config','url_path':'edsys-media'})
    candidate=extend(current,b['player']['entity_id'],b['tv']['entity_id'])
    a.private_state.mkdir(parents=True,exist_ok=True,mode=0o700)
    for suffix,data in [('before',current),('candidate',candidate)]:
        path=a.private_state/('media-'+suffix+'.json')
        if path.exists() and json.loads(path.read_text())!=data:raise RuntimeError('Private baseline already differs')
        path.write_text(json.dumps(data));path.chmod(0o600)
    print(json.dumps({'new_view':'bedroom-pi','new_links':2,'existing_views_preserved':True}),flush=True)
    if not a.apply:return
    validate_binding(api,b)
    if ws(api,{'type':'lovelace/config','url_path':'edsys-media'})!=current:raise RuntimeError('Concurrent dashboard edit; not saved')
    ws(api,{'type':'lovelace/config/save','url_path':'edsys-media','config':candidate})
    if ws(api,{'type':'lovelace/config','url_path':'edsys-media'})!=candidate:raise RuntimeError('Readback differs; inspect without replay')
    print(json.dumps({'saved_and_exact_readback':True}))


if __name__=='__main__':main()
