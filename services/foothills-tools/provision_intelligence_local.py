#!/usr/bin/env python3
"""Provision only Observation's CPU-local broker alias and local-only client policy.

Run preflight, review the receipt, then --apply. Existing cloud connections and
other model/client policies are preserved. Credentials never leave the broker.
"""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import subprocess

ALIAS = 'observation-intelligence-local'
WORKER = r'''
import asyncio,hashlib,json,os,sys,urllib.request
from prisma import Prisma
ALIAS='observation-intelligence-local'
FIELDS=['key_alias','models','max_budget','budget_duration','allowed_routes','blocked','permissions','metadata','user_id','team_id','rpm_limit','tpm_limit']
class ScopeError(RuntimeError):pass
def digest(v):return hashlib.sha256(json.dumps(v,sort_keys=True,default=str).encode()).hexdigest()
def policy(r):
 d=r.model_dump();return {k:d.get(k) for k in FIELDS}
def request(path,payload):
 req=urllib.request.Request('http://127.0.0.1:4000'+path,data=json.dumps(payload).encode(),headers={'Authorization':'Bearer '+os.environ['LITELLM_MASTER_KEY'],'Content-Type':'application/json'})
 with urllib.request.urlopen(req,timeout=30) as response:return json.load(response)
async def main():
 p=json.load(sys.stdin);db=Prisma();await db.connect()
 try:
  models=await db.litellm_proxymodeltable.find_many();clients=await db.litellm_verificationtoken.find_many()
  chosen=[r for r in clients if r.key_alias=='observation-ask']
  if len(chosen)!=1 or any(r.model_name==ALIAS for r in models):raise ScopeError('Client or alias precondition changed; inspect instead of retrying')
  client=chosen[0];before=policy(client)
  if set(client.models)!=set(['edsys-chat-local','foothills-query-best','edsys-embeddings-fast']):raise ScopeError('Expected prior Observation policy changed')
  old_models={r.model_id:digest(r.model_dump()) for r in models}
  old_clients={r.token:digest(policy(r)) for r in clients}
  fingerprint=digest({'models':old_models,'clients':old_clients})
  if not p['apply']:return {'preflight':True,'before':fingerprint,'client':'observation-ask','old_models':list(client.models),'new_alias':ALIAS,'base':'http://ollama-coder:11434','credentials_exported':False}
  if p['expected']!=fingerprint:raise ScopeError('Preflight drifted; no apply')
  if p['model'] not in ('gpt-oss:20b','qwen3.5:35b'):raise ScopeError('Unqualified model selection')
  params={'model':'ollama_chat/'+p['model'],'api_base':'http://ollama-coder:11434','timeout':120,'num_retries':0,'max_retries':0,'num_ctx':16384,'num_predict':3000,'temperature':0}
  request('/model/new',{'model_name':ALIAS,'litellm_params':params,'model_info':{'mode':'chat','description':'Observation-only local CPU reasoning; no external fallback'}})
  after=await db.litellm_proxymodeltable.find_many();added=[r for r in after if r.model_name==ALIAS]
  if len(added)!=1 or {r.model_id:digest(r.model_dump()) for r in after if r.model_name!=ALIAS}!=old_models:raise ScopeError('Model readback drift; inspect partial apply')
  allowed=[ALIAS,'edsys-chat-local','edsys-embeddings-fast']
  request('/key/update',{'key':client.token,'models':allowed})
  current=await db.litellm_verificationtoken.find_many();selected=[r for r in current if r.token==client.token]
  if len(selected)!=1 or policy(selected[0])!={**before,'models':allowed}:raise ScopeError('Observation policy readback failed')
  if {r.token:digest(policy(r)) for r in current if r.token!=client.token}!={k:v for k,v in old_clients.items() if k!=client.token}:raise ScopeError('Other client changed; inspect')
  return {'applied':True,'alias':ALIAS,'model':p['model'],'base':'http://ollama-coder:11434','models':allowed,'other_models_clients_preserved':True,'cloud_alias_removed':True,'restart':False,'model_id':added[0].model_id,'credentials_exported':False}
 finally:await db.disconnect()
try:print('OBS_RESULT '+json.dumps(asyncio.run(main())))
except Exception as e:
 print('OBS_RESULT '+json.dumps({'ok':False,'error_type':type(e).__name__,'reason':str(e) if isinstance(e,ScopeError) else 'Inspect privately; do not retry automatically'}));sys.exit(1)
'''

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--private-state',type=Path,required=True)
    parser.add_argument('--apply',action='store_true')
    parser.add_argument('--model',choices=['gpt-oss:20b','qwen3.5:35b'],default='qwen3.5:35b')
    args=parser.parse_args();os.umask(0o077)
    root=args.private_state
    if root.is_symlink():raise RuntimeError('Private state symlink rejected')
    root.mkdir(parents=True,exist_ok=True,mode=0o700);root.chmod(0o700)
    marker=root/'attempted.json';preflight=root/'preflight.json'
    if marker.exists():raise RuntimeError('Prior attempt exists; inspect its receipt rather than replay')
    payload={'apply':args.apply,'model':args.model}
    if args.apply:
        payload['expected']=json.loads(preflight.read_text())['before']
        with marker.open('x') as f:f.write('{"started":true}\n');f.flush();os.fsync(f.fileno())
    result=subprocess.run(['docker','exec','-i','workhorse-litellm','python','-c',WORKER],input=json.dumps(payload),capture_output=True,text=True,timeout=120)
    (root/('apply.log' if args.apply else 'preflight.log')).write_text(result.stdout+result.stderr)
    lines=[s.removeprefix('OBS_RESULT ') for s in result.stdout.splitlines() if s.startswith('OBS_RESULT ')]
    if len(lines)!=1:raise RuntimeError('Missing bounded receipt; inspect private log')
    receipt=json.loads(lines[0]);(root/('result.json' if args.apply else 'preflight.json')).write_text(json.dumps(receipt,indent=2)+'\n');print(json.dumps(receipt))
    if result.returncode or receipt.get('ok') is False:raise SystemExit(1)
if __name__=='__main__':main()
