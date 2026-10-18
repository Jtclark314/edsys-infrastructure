#!/usr/bin/env python3
"""Provision one Portal-only alias through the existing broker connection.

No credential leaves the broker worker. Preflight is read-only; apply refuses
replay after its durable attempt marker, including after partial failure.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess

ALIAS = "edsys-portal-openai"
POLICY_KEYS = ["key_alias", "models", "max_budget", "budget_duration", "allowed_routes",
               "blocked", "permissions", "metadata", "user_id", "team_id", "rpm_limit", "tpm_limit"]


def expected_models(before):
    if not before or ALIAS in before:
        raise ValueError("Expected an explicit prior Portal allowlist without the new alias")
    return [*before, ALIAS]


WORKER = r'''
import asyncio,base64,hashlib,json,os,sys,urllib.request
from prisma import Prisma
from litellm.proxy.common_utils.encrypt_decrypt_utils import decrypt_value
ALIAS='edsys-portal-openai'
class ScopeError(RuntimeError):pass
POLICY_KEYS=['key_alias','models','max_budget','budget_duration','allowed_routes','blocked','permissions','metadata','user_id','team_id','rpm_limit','tpm_limit']
def obj(v):return json.loads(v) if isinstance(v,str) else v
def digest(v):return hashlib.sha256(json.dumps(v,sort_keys=True,default=str).encode()).hexdigest()
def decrypt(v):
 if not isinstance(v,str):return v
 return decrypt_value(base64.urlsafe_b64decode(v),os.environ.get('LITELLM_SALT_KEY') or os.environ['LITELLM_MASTER_KEY'])
def policy(row):
 d=row.model_dump();return {k:d.get(k) for k in POLICY_KEYS}
def model_map(rows):return {r.model_id:digest(r.model_dump()) for r in rows}
def client_map(rows):return {r.token:digest(policy(r)) for r in rows}
def api(path,payload):
 req=urllib.request.Request('http://127.0.0.1:4000'+path,data=json.dumps(payload).encode(),headers={'Authorization':'Bearer '+os.environ['LITELLM_MASTER_KEY'],'Content-Type':'application/json'})
 with urllib.request.urlopen(req,timeout=30) as response:return json.load(response)
async def main():
 p=json.load(sys.stdin);db=Prisma();await db.connect()
 try:
  rows=await db.litellm_proxymodeltable.find_many();clients=await db.litellm_verificationtoken.find_many()
  source=[r for r in rows if r.model_name=='edsys-knowledge-openai'];portal=[r for r in clients if r.key_alias=='edsys-ai-portal']
  if len(source)!=1 or len(portal)!=1 or any(r.model_name==ALIAS for r in rows):raise ScopeError('Source/client/new-alias precondition changed; inspect without replay')
  client=portal[0];before_models=list(client.models)
  if not before_models or ALIAS in before_models:raise ScopeError('Portal must have explicit unchanged prior allowlist')
  before_policy=policy(client);old_models=model_map(rows);old_clients=client_map(clients)
  fingerprint=digest({'models':old_models,'clients':old_clients})
  params=obj(source[0].litellm_params)
  if decrypt(params.get('model'))!='openai/gpt-6.1-sol':raise ScopeError('Existing qualified source model differs')
  if not p['apply']:return {'preflight':True,'before':fingerprint,'portal_models_before':before_models,'source_alias':'edsys-knowledge-openai','new_alias':ALIAS,'credentials_exported':False}
  if p['expected']!=fingerprint:raise ScopeError('Preflight drifted; inspect before any write')
  connection={k:decrypt(v) for k,v in params.items() if k in ('api_key','api_base','base_url','organization','extra_headers')}
  if not connection.get('api_key'):raise ScopeError('Existing connection unavailable')
  connection.update(model='openai/gpt-6.1-sol',timeout=60,num_retries=0,max_retries=0)
  api('/model/new',{'model_name':ALIAS,'litellm_params':connection,'model_info':{'mode':'chat','description':'Portal-only bounded cloud chat; existing connection'}})
  after=await db.litellm_proxymodeltable.find_many();added=[r for r in after if r.model_name==ALIAS]
  if len(added)!=1 or model_map([r for r in after if r.model_name!=ALIAS])!=old_models:raise ScopeError('Model readback mismatch; inspect partial attempt')
  actual=obj(added[0].litellm_params)
  if decrypt(actual['model'])!='openai/gpt-6.1-sol' or decrypt(actual['api_key'])!=connection['api_key']:raise ScopeError('Connection readback mismatch; inspect partial attempt')
  api('/key/update',{'key':client.token,'models':before_models+[ALIAS]})
  current=await db.litellm_verificationtoken.find_many();selected=[r for r in current if r.token==client.token]
  if len(selected)!=1 or policy(selected[0])!={**before_policy,'models':before_models+[ALIAS]}:raise ScopeError('Portal policy changed unexpectedly; inspect partial attempt')
  others_before={k:v for k,v in old_clients.items() if k!=client.token};others_after={k:v for k,v in client_map(current).items() if k!=client.token}
  if others_after!=others_before:raise ScopeError('Unrelated client policy changed; inspect')
  return {'applied':True,'new_alias':ALIAS,'model':'openai/gpt-6.1-sol','existing_connection_reused':True,'only_portal_allowlist_appended':True,'other_models_clients_preserved':True,'credentials_exported':False,'restart':False,'model_id':added[0].model_id}
 finally:await db.disconnect()
try:print('EDSYS_RESULT '+json.dumps(asyncio.run(main())))
except Exception as e:
 print('EDSYS_RESULT '+json.dumps({'ok':False,'error_type':type(e).__name__,'http_status':getattr(e,'code',None),'reason':str(e) if isinstance(e,ScopeError) else 'Inspect privately; no automatic retry'}));sys.exit(1)
'''


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--private-state",type=Path,required=True)
    parser.add_argument("--apply",action="store_true")
    args=parser.parse_args()
    os.umask(0o077)
    root=args.private_state
    root.mkdir(parents=True,exist_ok=True,mode=0o700)
    if root.is_symlink():raise RuntimeError("Private state symlink rejected")
    preflight=root/"preflight.json";attempt=root/"attempted.json"
    if attempt.exists():raise RuntimeError("Prior attempt exists; inspect instead of replaying")
    payload={"apply":args.apply}
    if args.apply:
        prior=json.loads(preflight.read_text())
        expected_models(prior["portal_models_before"])
        payload["expected"]=prior["before"]
        with attempt.open("x") as handle:
            handle.write('{"started":true}\n');handle.flush();os.fsync(handle.fileno())
    result=subprocess.run(["docker","exec","-i","workhorse-litellm","python","-c",WORKER],input=json.dumps(payload),capture_output=True,text=True,timeout=120)
    (root/("apply.log" if args.apply else "preflight.log")).write_text(result.stdout+result.stderr)
    lines=[line.removeprefix("EDSYS_RESULT ") for line in result.stdout.splitlines() if line.startswith("EDSYS_RESULT ")]
    if len(lines)!=1:raise RuntimeError("Missing bounded worker receipt; inspect private log")
    data=json.loads(lines[0]);(root/("result.json" if args.apply else "preflight.json")).write_text(json.dumps(data,indent=2)+"\n")
    print(json.dumps(data))
    if result.returncode or data.get("ok") is False:raise SystemExit(1)


if __name__=="__main__":main()
