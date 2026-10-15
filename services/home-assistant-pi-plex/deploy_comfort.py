#!/usr/bin/env python3
"""Upgrade only the accepted Pi dashboard after an exact baseline comparison."""
import argparse
import json
from pathlib import Path
from deploy import client,ws,validate_binding
from full_dashboard import build,URL


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ['binding','expected','private-state']:p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--apply',action='store_true');a=p.parse_args()
    binding=json.loads(a.binding.read_text());api=client();validate_binding(api,binding)
    before=ws(api,{'type':'lovelace/config','url_path':URL})
    if before!=json.loads(a.expected.read_text()):raise RuntimeError('Dashboard changed since accepted baseline; preserve it and review before editing')
    for key in ['night_sensor','sleep_sensor']:
        eid=binding[key]
        if not eid.startswith('sensor.') or not api({'operation':'state','method':'GET','path':'api/states/'+eid}).get('entity_id'):raise RuntimeError('New dashboard sensor missing')
    after=build(binding);a.private_state.mkdir(parents=True,exist_ok=True,mode=0o700)
    for name,value in [('before.json',before),('after.json',after)]:
        path=a.private_state/name
        if path.exists():raise RuntimeError('A deployment record exists; inspect before retry')
        path.write_text(json.dumps(value));path.chmod(0o600)
    if a.apply:
        if ws(api,{'type':'lovelace/config','url_path':URL})!=before:raise RuntimeError('Concurrent dashboard edit; no write sent')
        ws(api,{'type':'lovelace/config/save','url_path':URL,'config':after})
        if ws(api,{'type':'lovelace/config','url_path':URL})!=after:raise RuntimeError('Readback differs; inspect before retry')
    print(json.dumps({'applied':a.apply,'views':len(after['views']),'exact_readback':a.apply}))


if __name__=='__main__':main()
