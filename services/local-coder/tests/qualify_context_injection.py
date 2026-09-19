"""Opt-in installed-OpenCode protocol test with a local fake provider, no Qwen calls.

Inspect actual parent/child model requests for automatically injected context.
Private captured requests remain on AI Store. Unit tests alone cannot prove that
the pinned client loads and dispatches its reviewed plugin.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SOURCE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOURCE))
from knowledge import ProjectMemory

ROOT = Path('/mnt/ai-store/local-coder')
requests = []


class Provider(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        requests.append(body)
        messages = body['messages']
        user = next(m for m in reversed(messages) if m['role'] == 'user')
        child = 'Child context probe' in json.dumps(user['content'])
        title = any(m['role'] == 'system' and 'You are a title generator.' in str(m['content']) for m in messages)
        done = any(m['role'] == 'tool' for m in messages)
        delta = {'role': 'assistant', 'content': 'CONTEXT_PROTOCOL_OK'}
        finish = 'stop'
        if not child and not done and not title:
            delta = {'role': 'assistant', 'tool_calls': [{'index': 0, 'id': 'call_context_probe', 'type': 'function',
                     'function': {'name': 'task', 'arguments': json.dumps({'description':'Read-only context probe',
                     'prompt':'Child context probe: report that the supplied automatic project context is present. Do not call any tools or write files.',
                     'subagent_type':'general'})}}]}
            finish = 'tool_calls'
        self.send_response(200)
        self.send_header('Content-Type','text/event-stream')
        self.end_headers()
        for choice in ({'index':0,'delta':delta,'finish_reason':None}, {'index':0,'delta':{},'finish_reason':finish}):
            data={'id':'chatcmpl-context-probe','object':'chat.completion.chunk','created':1,'model':body['model'],'choices':[choice]}
            self.wfile.write(('data: '+json.dumps(data)+'\n\n').encode())
        self.wfile.write(b'data: [DONE]\n\n')


def main():
    os.umask(0o077)
    with tempfile.TemporaryDirectory(prefix='hook-protocol-',dir=ROOT/'qualification') as temp:
        folder=Path(temp)
        subprocess.run(['git','init','-q',temp],check=True)
        (folder/'AGENTS.md').write_text('Project marker: AUTOMATIC-GUIDANCE-4829. Read-only test; one child delegation is authorized.\n')
        memory=ProjectMemory(folder)
        memory.save(0,'Automatic context qualification',[],['Private decision marker: AUTOMATIC-MEMORY-7391'],[],[],[])
        server=ThreadingHTTPServer(('127.0.0.1',0),Provider)
        threading.Thread(target=server.serve_forever,daemon=True).start()
        try:
            config=json.loads((SOURCE/'opencode.json').read_text())
            config['provider']['edsys-local']['options']['baseURL']=f'http://127.0.0.1:{server.server_port}/v1'
            for entry in config['mcp'].values():entry['enabled']=False
            env=os.environ.copy()
            for k in list(env):
                if k.startswith('OPENCODE_') or k.startswith('OTEL_'):env.pop(k)
            env.update({'XDG_DATA_HOME':str(folder/'data'),'XDG_STATE_HOME':str(folder/'state'),
                        'XDG_CACHE_HOME':str(ROOT/'cache'),'XDG_CONFIG_HOME':str(ROOT/'config'),
                        'OPENCODE_CONFIG_CONTENT':json.dumps(config),'OPENCODE_DISABLE_PROJECT_CONFIG':'1',
                        'OPENCODE_DISABLE_MODELS_FETCH':'1','OPENCODE_DISABLE_AUTOUPDATE':'1','OPENCODE_PURE':'0'})
            binary='/mnt/ai-store/apps/local-coder/opencode-1.18.31/node_modules/.bin/opencode'
            result=subprocess.run([binary,'run','--dir',temp,'--format','json',
                                   'Parent context probe: delegate exactly one read-only general child and report the result.'],
                                  env=env,capture_output=True,text=True,timeout=90)
            evidence=ROOT/'evidence/context-deployment'
            evidence.mkdir(parents=True,exist_ok=True)
            (evidence/'automatic-injection-requests.json').write_text(json.dumps(requests))
            (evidence/'automatic-injection-events.jsonl').write_text(result.stdout)
            (evidence/'automatic-injection-stderr.log').write_text(result.stderr)
            assert result.returncode==0,result.stderr[-500:]
            substantive=[r for r in requests if not any(m['role']=='system' and 'You are a title generator.' in str(m['content']) for m in r['messages'])]
            assert len(substantive)==3,f'Expected parent/child/parent requests, got {len(substantive)}'
            for req in requests:
                system='\n'.join(str(m['content']) for m in req['messages'] if m['role']=='system')
                assert 'AUTOMATIC-GUIDANCE-4829' in system,'Project guidance not injected'
                assert 'AUTOMATIC-MEMORY-7391' in system,'Project checkpoint not injected'
            print('PASS: actual OpenCode parent, child and continuation received automatic guidance + private memory with MCP disabled')
        finally:
            server.shutdown()
            server.server_close()
            shutil.rmtree(memory.folder,ignore_errors=True)


if __name__=='__main__':
    main()
