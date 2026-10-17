#!/usr/bin/env python3
"""Guarded, scoped broker model provisioning; no provider key leaves the broker."""
import argparse
import json
from pathlib import Path
import subprocess

WORKER = r'''
import asyncio,base64,hashlib,json,os,sys,urllib.request
from prisma import Prisma
from litellm.proxy.common_utils.encrypt_decrypt_utils import decrypt_value
ALIAS='edsys-knowledge-openai'
def obj(value):return json.loads(value) if isinstance(value,str) else value
def digest(value):return hashlib.sha256(json.dumps(value,sort_keys=True,default=str).encode()).hexdigest()
def decrypt(value):
 if not isinstance(value,str):return value
 return decrypt_value(base64.urlsafe_b64decode(value),os.environ.get('LITELLM_SALT_KEY') or os.environ['LITELLM_MASTER_KEY'])
def api(path,payload):
 req=urllib.request.Request('http://127.0.0.1:4000'+path,data=json.dumps(payload).encode(),headers={'Authorization':'Bearer '+os.environ['LITELLM_MASTER_KEY'],'Content-Type':'application/json'})
 with urllib.request.urlopen(req,timeout=30) as response:return json.load(response)
async def main():
 payload=json.load(sys.stdin);db=Prisma();await db.connect()
 try:
  rows=await db.litellm_proxymodeltable.find_many()
  sources=[r for r in rows if r.model_name=='gpt-5-chat-latest']
  if len(sources)!=1 or any(r.model_name==ALIAS for r in rows):raise RuntimeError('Expected source or unused scoped model changed; inspect')
  keys=await db.litellm_verificationtoken.find_many(where={'key_alias':'edsys-anythingllm'})
  if len(keys)!=1:raise RuntimeError('Expected one existing AnythingLLM service credential')
  client=keys[0];before_models=list(client.models)
  if not before_models or ALIAS in before_models:raise RuntimeError('Existing client allowlist changed; inspect')
  old={r.model_dump()['model_id'] if 'model_id' in r.model_dump() else r.model_dump()['id']:digest(r.model_dump()) for r in rows}
  policy_keys=['key_alias','models','max_budget','budget_duration','allowed_routes','blocked','permissions','metadata','user_id','team_id']
  original_client=client.model_dump();policy={k:original_client.get(k) for k in policy_keys}
  before=digest({'models':old,'client_policy':policy})
  if not payload['apply']:return {'preflight':True,'before':before,'client_models_before':before_models,'source_model':'gpt-5-chat-latest','new_model':ALIAS,'plaintext_credentials_exported':False}
  if before!=payload['expected']:raise RuntimeError('Broker preflight changed; inspect')
  params=obj(sources[0].litellm_params)
  connection={k:decrypt(v) for k,v in params.items() if k in ('api_key','api_base','base_url','organization','extra_headers')}
  if not connection.get('api_key'):raise RuntimeError('Existing provider connection unavailable')
  connection.update({'model':'openai/gpt-6.1-sol','timeout':60,'num_retries':0,'max_retries':0})
  created=api('/model/new',{'model_name':ALIAS,'litellm_params':connection,'model_info':{'description':'EdSys knowledge local-first assistance; same existing OpenAI connection'}})
  after_create=await db.litellm_proxymodeltable.find_many()
  added=[r for r in after_create if r.model_name==ALIAS]
  if len(added)!=1:raise RuntimeError('Scoped model creation readback differs; inspect without replay')
  new_params=obj(added[0].litellm_params)
  if decrypt(new_params['model'])!='openai/gpt-6.1-sol' or decrypt(new_params['api_key'])!=connection['api_key']:raise RuntimeError('Scoped model connection readback differs; inspect')
  api('/key/update',{'key':client.token,'models':before_models+[ALIAS]})
  actual_client=await db.litellm_verificationtoken.find_unique(where={'token':client.token})
  expected_policy={**policy,'models':before_models+[ALIAS]}
  if {k:actual_client.model_dump().get(k) for k in policy_keys}!=expected_policy:raise RuntimeError('Client policy readback differs; inspect')
  retained={r.model_dump()['model_id'] if 'model_id' in r.model_dump() else r.model_dump()['id']:digest(r.model_dump()) for r in after_create if r.model_name!=ALIAS}
  if retained!=old or len(after_create)!=len(rows)+1:raise RuntimeError('Other broker models changed; inspect')
  return {'applied':True,'scoped_model':ALIAS,'openai_model':'gpt-6.1-sol','existing_connection_reused':True,'other_models_unchanged':True,'only_existing_anythingllm_allowlist_appended':True,'client_models_before':before_models,'new_model_id':added[0].model_dump().get('model_id',added[0].model_dump().get('id')),'plaintext_credentials_exported':False,'restart':False}
 finally:await db.disconnect()
try:print('EDSYS_RESULT '+json.dumps(asyncio.run(main())))
except Exception as error:
 print('EDSYS_RESULT '+json.dumps({'ok':False,'error_type':type(error).__name__,'http_status':getattr(error,'code',None),'reason':str(error) if isinstance(error,RuntimeError) else 'Inspect broker privately; no automatic retry'}));sys.exit(1)
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--private-state', type=Path, required=True)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    args.private_state.mkdir(parents=True, exist_ok=True, mode=0o700)
    args.private_state.chmod(0o700)
    preflight = args.private_state / 'broker-preflight.json'
    attempted = args.private_state / 'broker-attempted.json'
    if attempted.exists():
        raise RuntimeError('Prior broker attempt recorded; inspect instead of replaying')
    payload = {'apply': args.apply}
    if args.apply:
        payload['expected'] = json.loads(preflight.read_text())['before']
        attempted.write_text('{"started":true}')
        attempted.chmod(0o600)
    result = subprocess.run(['docker', 'exec', '-i', 'workhorse-litellm', 'python', '-c', WORKER], input=json.dumps(payload), capture_output=True, text=True)
    log = args.private_state / ('broker-apply.log' if args.apply else 'broker-preflight.log')
    log.write_text(result.stdout + result.stderr)
    log.chmod(0o600)
    lines = [line.removeprefix('EDSYS_RESULT ') for line in result.stdout.splitlines() if line.startswith('EDSYS_RESULT ')]
    if len(lines) != 1:
        raise RuntimeError('Unexpected broker result; inspect private log')
    data = json.loads(lines[0])
    destination = args.private_state / ('broker-result.json' if args.apply else 'broker-preflight.json')
    destination.write_text(json.dumps(data, indent=2))
    destination.chmod(0o600)
    print(json.dumps(data))
    if result.returncode or data.get('ok') is False:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
