#!/usr/bin/env python3
"""Promote the existing Google view using native APIs; key stays inside HA."""
import argparse
import copy
import hashlib
import inspect
import json
from pathlib import Path

from deploy import guest,ROOT,RESOURCE


def prepare(native,google):
    """Preserve native fallback exactly, and retain key/selection in Google views."""
    if len(native.get('views',[]))!=1 or len(google.get('views',[]))!=1:
        raise RuntimeError('Expected one existing view in each map')
    native_cards=native['views'][0].get('cards',[])
    google_cards=google['views'][0].get('cards',[])
    if len(native_cards)!=1 or len(google_cards)!=1:
        raise RuntimeError('Expected one existing card in each map')
    old,new=native_cards[0],google_cards[0]
    if old.get('type')!='map' or new.get('type')!='custom:edsys-google-maps-trial':
        raise RuntimeError('Current map types differ; inspect before promotion')
    def selected(card):
        return [item if isinstance(item,str) else item['entity'] for item in card['entities']]
    if selected(old)!=selected(new) or old.get('hours_to_show')!=2 or new.get('hours_to_show')!=2:
        raise RuntimeError('Map selections or history differ; inspect before promotion')
    fallback=copy.deepcopy(native)
    legacy=copy.deepcopy(google)
    legacy['views'][0]['title']='Google Maps'
    legacy['views'][0]['cards'][0].update(title='Google Maps',default_map_type='hybrid',fallback_path='/map-fallback')
    primary=copy.deepcopy(legacy)
    primary['views'][0]['title']='Map'
    primary['views'][0].pop('path',None)  # Preserve old /map/0 bookmarks.
    return {'primary':primary,'fallback':fallback,'legacy':legacy}


WORKER=r'''
import copy
def digest(value):
 return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()
async def promote(payload):
 async with aiohttp.ClientSession(connector=aiohttp.UnixConnector(path=str(SOCKET_CORE)),timeout=aiohttp.ClientTimeout(total=25)) as session:
  async def call(kind,**fields):return await websocket(session,{'type':kind,**fields})
  dashboards=await call('lovelace/dashboards/list')
  lookup={d['url_path']:d for d in dashboards}
  if 'map-fallback' in lookup:raise RuntimeError('Fallback already exists; inspect before retry')
  if not all(p in lookup for p in ('map','google-maps-trial')):raise RuntimeError('Expected existing map dashboards')
  if lookup['google-maps-trial'].get('require_admin') is not True:raise RuntimeError('Google access policy differs')
  native=await call('lovelace/config',url_path='map')
  google=await call('lovelace/config',url_path='google-maps-trial')
  config=prepare(native,google)
  key=google['views'][0]['cards'][0].get('api_key','')
  if not re.fullmatch(r'AIza[A-Za-z0-9_-]{35}',key):raise RuntimeError('Existing Google key is not configured')
  resources=await call('lovelace/resources')
  if not any(r.get('url')==payload['resource'] and r.get('type')=='module' for r in resources):raise RuntimeError('Required parent resource is not active')
  before={'native_sha256':digest(native),'google_sha256':digest(google),'dashboards_sha256':digest(dashboards)}
  if payload.get('expected') and payload['expected']!=before:raise RuntimeError('Preflight changed; inspect before applying')
  if not payload['apply']:return {'dry_run':True,'before':before,'selected_locations':len(native['views'][0]['cards'][0]['entities']),'key_exported':False}
  # Build and verify the fallback before altering the primary entry.
  fallback=await call('lovelace/dashboards/create',url_path='map-fallback',title='HA Map (Fallback)',icon='mdi:map-outline',show_in_sidebar=True,require_admin=lookup['map'].get('require_admin',False))
  await call('lovelace/config/save',url_path='map-fallback',config=config['fallback'])
  if await call('lovelace/config',url_path='map-fallback')!=native:raise RuntimeError('Fallback verification failed; primary untouched')
  # Preserve the Google view's existing admin-only access before placing its key.
  await call('lovelace/dashboards/update',dashboard_id=lookup['map']['id'],title='Map',icon='mdi:google-maps',show_in_sidebar=True,require_admin=True)
  await call('lovelace/config/save',url_path='map',config=config['primary'])
  await call('lovelace/config/save',url_path='google-maps-trial',config=config['legacy'])
  await call('lovelace/dashboards/update',dashboard_id=lookup['google-maps-trial']['id'],title='Google Maps',show_in_sidebar=False)
  actual=await call('lovelace/dashboards/list')
  expected=[{**d,'title':'Map','icon':'mdi:google-maps','show_in_sidebar':True,'require_admin':True} if d['url_path']=='map' else {**d,'title':'Google Maps','show_in_sidebar':False} if d['url_path']=='google-maps-trial' else d for d in dashboards]+[fallback]
  checks={'native_fallback_exact':await call('lovelace/config',url_path='map-fallback')==native,'primary_exact':await call('lovelace/config',url_path='map')==config['primary'],'legacy_exact':await call('lovelace/config',url_path='google-maps-trial')==config['legacy'],'dashboard_changes_exact':actual==expected,'resources_unchanged':await call('lovelace/resources')==resources}
  if not all(checks.values()):raise RuntimeError('Promotion readback differs; inspect without replay')
  return {'promoted':True,'primary_path':'/map','fallback_path':'/map-fallback','default_map_type':'hybrid','manual_load':True,'google_admin_only':True,'key_exported':False,'before':before,**checks}
try:
 payload=json.load(sys.stdin)
 print(json.dumps(asyncio.run(promote(payload))))
except Exception as error:
 print(json.dumps({'ok':False,'error':str(error) if isinstance(error,RuntimeError) else type(error).__name__}))
 sys.exit(1)
'''


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--private-state',type=Path,required=True)
    parser.add_argument('--apply',action='store_true')
    args=parser.parse_args();private=args.private_state
    private.mkdir(mode=0o700,parents=True,exist_ok=True)
    record=private/'promotion-preflight.json'
    worker=(ROOT.parents[1]/'scripts/ops/edcore-ha-remote.py').read_text().split('if __name__ == "__main__":')[0]
    payload={'apply':args.apply,'resource':RESOURCE}
    if args.apply:
        if (private/'promotion-attempted.json').exists():raise RuntimeError('Prior attempt exists; inspect current state rather than replay')
        payload['expected']=json.loads(record.read_text())['before']
        for name,field in [('native-before.json','native_sha256'),('dashboards-before.json','dashboards_sha256')]:
            backup=json.loads((private/name).read_text())
            digest=hashlib.sha256(json.dumps(backup,sort_keys=True,separators=(',',':')).encode()).hexdigest()
            if digest!=payload['expected'][field]:raise RuntimeError('Recovery snapshot differs from preflight; inspect before applying')
        attempted=private/'promotion-attempted.json';attempted.write_text(json.dumps({'apply_started':True}));attempted.chmod(0o600)
    result=guest(worker+'\n'+inspect.getsource(prepare)+'\n'+WORKER,payload,container='hassio_supervisor')
    destination=private/('promotion-result.json' if args.apply else 'promotion-preflight.json')
    destination.write_text(json.dumps(result,indent=2));destination.chmod(0o600)
    print(json.dumps(result))


if __name__=='__main__':main()
