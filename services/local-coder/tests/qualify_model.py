import json,time,urllib.request
from pathlib import Path
base='http://127.0.0.1:11435'
model='edsys-qwen36-coder:latest'
def chat(messages,**kwargs):
 p={'model':model,'messages':messages,'stream':False,**kwargs}
 start=time.monotonic()
 request=urllib.request.Request(base+'/api/chat',data=json.dumps(p).encode(),headers={'Content-Type':'application/json'})
 with urllib.request.urlopen(request,timeout=1800) as r:d=json.load(r)
 stats={'seconds':round(time.monotonic()-start,2),'input_tokens':d.get('prompt_eval_count'),'output_tokens':d.get('eval_count'),'tokens_per_second':round(d.get('eval_count',0)/(d.get('eval_duration',1)/1e9),2),'done_reason':d.get('done_reason')}
 print(json.dumps(stats),flush=True)
 return d,stats
result,stats=chat([{'role':'user','content':'Reply with exactly LOCAL_READY'}],think=False,options={'num_predict':64})
assert 'LOCAL_READY' in result['message']['content'],result['message']['content']
tools=[{'type':'function','function':{'name':'lookup_build_status','description':'Read the current build status and verification marker.','parameters':{'type':'object','properties':{'project':{'type':'string'}},'required':['project']}}}]
messages=[{'role':'user','content':'Use lookup_build_status for project local-coder and tell me the exact verification marker it returns. Do not guess the marker.'}]
r,s=chat(messages,tools=tools,think=True,options={'num_predict':2048})
calls=r['message'].get('tool_calls',[])
assert len(calls)==1 and calls[0]['function']['name']=='lookup_build_status'
assert calls[0]['function']['arguments']=={'project':'local-coder'}
messages.extend([r['message'],{'role':'tool','tool_name':'lookup_build_status','content':'{"status":"passed","verification_marker":"LOCAL-VERIFY-7391"}'}])
r,s2=chat(messages,tools=tools,think=True,options={'num_predict':2048})
assert 'LOCAL-VERIFY-7391' in r['message']['content']
Path('/mnt/ai-store/local-coder/evidence/api-smoke.json').write_text(json.dumps({'ready':stats,'tool_call':s,'tool_result':s2,'passed':True},indent=2)+'\n')
print('API and thinking/tool roundtrip PASS',flush=True)
