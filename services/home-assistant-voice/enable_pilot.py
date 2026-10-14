#!/usr/bin/env python3
"""Activate the verified local router for the single named voice profile.

Run only after installing/restarting and qualifying the pilot. --apply records
existing authorization; it neither grants it nor restarts Home Assistant.
"""
import argparse
import json
from pathlib import Path
from configure import TARGET, client, exactly_one, ws


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--private-state',type=Path,required=True)
    parser.add_argument('--apply',action='store_true')
    parser.add_argument('--resume',action='store_true',help='Resume only after inspecting a rejected or interrupted activation')
    args=parser.parse_args();api=client()
    pipelines=ws(api,{'type':'assist_pipeline/pipeline/list'})
    pipeline=exactly_one(pipelines['pipelines'],lambda p:p['name']==TARGET,'target profile')
    if pipeline['conversation_engine']!='conversation.edsys_voice':raise RuntimeError('Unexpected target conversation engine')
    ws(api,{'type':'edsys_voice_knowledge/nimo'})
    if not args.apply:
        print(json.dumps({'mode':'dry-run','profile':TARGET,'native_recognition':'inside verified local router','other_profiles_changed':False}));return
    args.private_state.mkdir(parents=True,exist_ok=True,mode=0o700)
    backup=args.private_state/'pipeline-before-verified-routing.json'
    if backup.exists() and not args.resume:raise RuntimeError('Routing recovery record already exists; inspect before repeating')
    if not backup.exists():
        backup.write_text(json.dumps(pipelines));backup.chmod(0o600)
    # Core 2026.10.1 requires the complete pipeline fields for this update.
    fields={key:value for key,value in pipeline.items() if key!='id' and not (key=='acknowledge_media_id' and value is None)}
    fields['prefer_local_intents']=False
    if pipeline['prefer_local_intents'] is not False:
        ws(api,{'type':'assist_pipeline/pipeline/update','pipeline_id':pipeline['id'],**fields})
    after=ws(api,{'type':'assist_pipeline/pipeline/list'})
    target=exactly_one(after['pipelines'],lambda p:p['id']==pipeline['id'],'updated profile')
    for key,value in pipeline.items():
        if key!='prefer_local_intents' and target[key]!=value:raise RuntimeError('An unrelated speech/profile setting changed')
    if after['preferred_pipeline']!=pipelines['preferred_pipeline']:raise RuntimeError('The household preferred profile changed')
    if target['prefer_local_intents'] is not False:raise RuntimeError('Router activation was not retained')
    path=args.private_state/'production-pipeline.json';path.write_text(json.dumps(target));path.chmod(0o600)
    print(json.dumps({'verified_router_activated':True,'speech_settings_retained':True,'household_preferred_profile_retained':True}))


if __name__=='__main__':main()
