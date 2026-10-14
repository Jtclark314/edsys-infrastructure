"""Narrow, idempotent native dashboard repair; caller saves with concurrency check."""
import copy
from build import hdmi1_button


def repair_dashboard(original, entity):
    updated=copy.deepcopy(original)
    replacements=0
    additions=0

    def walk(obj):
        nonlocal replacements,additions
        if isinstance(obj,dict):
            action=obj.get('tap_action',{})
            data=action.get('data',{})
            if (obj.get('type')=='button' and action.get('perform_action')=='media_player.select_source'
                    and data.get('entity_id')==entity and data.get('source')=='HDMI 1'):
                layout={k:obj[k] for k in ['grid_options','visibility'] if k in obj}
                obj.clear();obj.update(hdmi1_button(entity));obj.update(layout)
                replacements+=1
            for value in list(obj.values()):walk(value)
        elif isinstance(obj,list):
            for child in list(obj):walk(child)
            index=0
            while index<len(obj):
                card=obj[index]
                if isinstance(card,dict) and card.get('type')=='tile' and card.get('entity')==entity:
                    features=card.setdefault('features',[])
                    kinds={f.get('type') for f in features}
                    # Keep the dedicated volume and existing input tiles intact.
                    if not kinds & {'media-player-source','media-player-volume-slider'}:
                        features.append({'type':'media-player-source'})
                        button=hdmi1_button(entity)
                        button['grid_options']={'columns':6,'rows':2}
                        obj.insert(index+1,button);index+=1;additions+=1
                index+=1
    walk(updated)
    return updated,{'replaced_shortcuts':replacements,'added_input_controls':additions}


def main():
    import argparse
    import json
    from pathlib import Path
    import sys
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'home-assistant-voice'))
    from configure import client,ws
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--binding',required=True,type=Path)
    p.add_argument('--private-state',required=True,type=Path)
    p.add_argument('--apply',action='store_true')
    a=p.parse_args()
    binding=json.loads(a.binding.read_text())
    api=client();entity=binding['entity_id']
    registry=ws(api,{'type':'config/entity_registry/get','entity_id':entity})
    if (registry['platform']!='webostv' or registry.get('disabled_by')
            or any(registry.get(k)!=binding[k] for k in ['device_id','config_entry_id'])):
        raise RuntimeError('Reviewed LG identity changed')
    a.private_state.mkdir(parents=True,exist_ok=True,mode=0o700)
    pending=[]
    for url in ['edsys-home','edsys-spaces','edsys-media']:
        current=ws(api,{'type':'lovelace/config','url_path':url})
        before=a.private_state/(url+'-before.json')
        if before.exists() and json.loads(before.read_text())!=current:
            raise RuntimeError('Dashboard changed since baseline: '+url)
        before.write_text(json.dumps(current));before.chmod(0o600)
        candidate,counts=repair_dashboard(current,entity)
        after=a.private_state/(url+'-candidate.json')
        after.write_text(json.dumps(candidate));after.chmod(0o600)
        if counts['replaced_shortcuts']>1 or counts['added_input_controls']>1:
            raise RuntimeError('Unexpected dashboard shape: '+url)
        pending.append((url,current,candidate,counts))
    print(json.dumps({'candidates':{u:c for u,_,_,c in pending},'actuator_calls':0}),flush=True)
    if a.apply:
        for url,current,candidate,counts in pending:
            if ws(api,{'type':'lovelace/config','url_path':url})!=current:
                raise RuntimeError('Concurrent dashboard edit: '+url)
            if candidate!=current:
                ws(api,{'type':'lovelace/config/save','url_path':url,'config':candidate})
                if ws(api,{'type':'lovelace/config','url_path':url})!=candidate:
                    raise RuntimeError('Saved dashboard differs; inspect without replay: '+url)
        print(json.dumps({'saved_and_verified':True,'actuator_calls':0}))


if __name__=='__main__':main()
