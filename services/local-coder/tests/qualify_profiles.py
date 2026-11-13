"""Opt-in installed-client profile/variant fixture. Local synthetic services only.

The real model, active OpenCode database, credentials, and archive are untouched.
Only request metadata/tool names are retained; no reasoning text is inspected.
"""
from copy import deepcopy
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
BINARY = '/mnt/ai-store/apps/local-coder/opencode-1.18.31/node_modules/.bin/opencode'
RUNTIME = Path('/mnt/ai-store/local-coder')
TOOLS = {
    'edsys': ['edsys_session_context', 'edsys_save_checkpoint', 'edsys_search_records', 'edsys_search_history'],
    'browser': ['browser_navigate', 'browser_snapshot'],
    'desktop': ['desktop_screenshot'],
    'code_intelligence': ['code_intelligence_search_code'],
    'github': ['get_me'], 'context7': ['resolve_library_id'],
    'microsoft_learn': ['microsoft_docs_search'],
}
CAPTURE = []


def mcp_result(body, server):
    method = body.get('method')
    if method == 'initialize':
        return {'protocolVersion': body['params']['protocolVersion'], 'capabilities': {'tools': {}},
                'serverInfo': {'name': server, 'version': 'synthetic'}}
    if method == 'tools/list':
        return {'tools': [{'name': name, 'description': 'Synthetic local profile test',
                           'inputSchema': {'type': 'object', 'properties': {}}} for name in TOOLS[server]]}
    if method == 'tools/call':
        return {'content': [{'type': 'text', 'text': 'LOCAL_FIXTURE_TOOL_OK'}]}
    return {}


def mcp_stdio(server):
    for line in sys.stdin:
        body = json.loads(line)
        if 'id' in body:
            print(json.dumps({'jsonrpc': '2.0', 'id': body['id'], 'result': mcp_result(body, server)}), flush=True)


class Fixture(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def respond(self, status, body):
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(data)))
        try:
            self.end_headers()
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass  # A stateless MCP client's optional GET can be cancelled.

    def do_GET(self):
        self.respond(405, {'error': 'Stateless synthetic POST fixture'})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        if self.path != '/v1/chat/completions':
            self.respond(404, {})
            return
        messages = body['messages']
        system = '\n'.join(str(m.get('content')) for m in messages if m['role'] == 'system')
        last_user = next((str(m.get('content', '')) for m in reversed(messages) if m['role'] == 'user'), '')
        utility = any(marker in system for marker in [
            'You are a title generator.', 'Summarize what was done in this conversation.',
            'You are a context summarization agent.',
        ])
        child = 'PROFILE_CHILD_GENERAL' in last_user or 'PROFILE_CHILD_EXPLORE' in last_user
        if child:
            label = 'child-general' if 'PROFILE_CHILD_GENERAL' in last_user else 'child-explore'
        elif utility:
            label = 'utility'
        else:
            label = self.server.label
        names = [tool['function']['name'] for tool in body.get('tools', [])]
        CAPTURE.append({'label': label, 'model': body['model'], 'tool_names': names,
                        'reasoning_effort': body.get('reasoning_effort'),
                        'has_reasoning_effort': 'reasoning_effort' in body,
                        'fixture_tool_result': any(m['role'] == 'tool' and 'LOCAL_FIXTURE_TOOL_OK' in str(m.get('content')) for m in messages),
                        'project_guidance': 'PROFILE_PROJECT_GUIDANCE_4829' in system})
        done = any(m['role'] == 'tool' for m in messages)
        delta = {'role': 'assistant', 'content': 'LOCAL_PROFILE_FIXTURE_OK'}
        finish = 'stop'
        if not utility and not child and not done:
            if self.server.label.startswith('delegate-'):
                subtype = self.server.label.removeprefix('delegate-')
                marker = 'PROFILE_CHILD_GENERAL' if subtype == 'general' else 'PROFILE_CHILD_EXPLORE'
                name, arguments = 'task', {'description': 'Synthetic read-only local child',
                                          'prompt': marker + ': return the fixture marker without tools.',
                                          'subagent_type': subtype}
            else:
                name, arguments = 'edsys_edsys_session_context', {}
            if name in names:
                delta = {'role': 'assistant', 'tool_calls': [{'index': 0, 'id': 'call_profile_fixture',
                         'type': 'function', 'function': {'name': name, 'arguments': json.dumps(arguments)}}]}
                finish = 'tool_calls'
            else:
                CAPTURE[-1]['missing_expected_tool'] = name
        evidence = RUNTIME / 'evidence/profile-qualification'
        evidence.mkdir(parents=True, exist_ok=True)
        (evidence / 'request-metadata.json').write_text(json.dumps(CAPTURE, indent=2) + '\n')
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream')
        self.end_headers()
        for choice in [{'index': 0, 'delta': delta, 'finish_reason': None},
                       {'index': 0, 'delta': {}, 'finish_reason': finish}]:
            payload = {'id': 'chatcmpl-local-fixture', 'object': 'chat.completion.chunk',
                       'created': 1, 'model': body['model'], 'choices': [choice]}
            self.wfile.write(('data: ' + json.dumps(payload) + '\n\n').encode())
        self.wfile.write(b'data: [DONE]\n\n')


def main():
    os.umask(0o077)
    config = json.loads((SOURCE / 'opencode.json').read_text())
    assert config['enabled_providers'] == ['edsys-local']
    assert set(config['provider']) == {'edsys-local'}
    assert config['provider']['edsys-local']['options']['baseURL'] == 'http://127.0.0.1:11435/v1'
    server = ThreadingHTTPServer(('127.0.0.1', 0), Fixture)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with tempfile.TemporaryDirectory(prefix='opencode-profile-fixture-') as folder:
            root = Path(folder)
            project = root / 'project'
            project.mkdir()
            subprocess.run(['git', 'init', '-q', str(project)], check=True)
            (project / 'AGENTS.md').write_text('PROFILE_PROJECT_GUIDANCE_4829. Synthetic read-only fixture; one local child is authorized.\n')
            # Preserve production context-read hooks, but never run the archive event
            # hook against production history from a synthetic client.
            plugin = root / 'fixture-plugin.mjs'
            plugin.write_text("import { EdSysProjectContext } from " +
                              json.dumps((SOURCE / 'context-plugin.mjs').as_uri()) +
                              ";\nexport const ProfileFixture = async input => {const hooks=await EdSysProjectContext(input);delete hooks.event;return hooks;};\n")
            for index, (label, agent, variant) in enumerate([
                ('general-default', 'local-general', None), ('general-fast', 'local-general', 'fast'),
                ('general-normal', 'local-general', 'normal'), ('code-default', 'local-code', None),
                ('build-default', 'build', None), ('delegate-general', 'build', None),
                ('delegate-explore', 'build', None),
            ]):
                server.label = label
                candidate = deepcopy(config)
                candidate['provider']['edsys-local']['options']['baseURL'] = f'http://127.0.0.1:{server.server_port}/v1'
                for name in candidate['mcp']:
                    candidate['mcp'][name] = {'type': 'local', 'command': [sys.executable, str(Path(__file__).resolve()), '--mcp-fixture', name],
                                            'enabled': True, 'timeout': 15000}
                candidate['plugin'] = [plugin.as_uri()]
                home = root / str(index)
                home.mkdir()
                shutil.copytree(RUNTIME / 'cache', home / 'cache')
                shutil.copytree(RUNTIME / 'config', home / 'config')
                env = {k: v for k, v in os.environ.items() if not k.startswith(('OPENCODE_', 'OTEL_'))}
                env.update({'XDG_DATA_HOME': str(home / 'data'), 'XDG_STATE_HOME': str(home / 'state'),
                            'XDG_CACHE_HOME': str(home / 'cache'), 'XDG_CONFIG_HOME': str(home / 'config'),
                            'OPENCODE_CONFIG_CONTENT': json.dumps(candidate), 'OPENCODE_DISABLE_PROJECT_CONFIG': '1',
                            'OPENCODE_DISABLE_MODELS_FETCH': '1', 'OPENCODE_DISABLE_AUTOUPDATE': '1', 'OPENCODE_PURE': '0'})
                command = [BINARY, 'run', '--dir', str(project), '--agent', agent, '--format', 'json']
                if variant:
                    command += ['--variant', variant]
                result = subprocess.run(command + ['PROFILE_MAIN: ' + label], env=env, capture_output=True,
                                        text=True, timeout=90)
                assert result.returncode == 0, (label, result.stderr[-1000:])
                assert '"type":"error"' not in result.stdout, (label, result.stdout[-1000:])
                print('Completed synthetic client fixture:', label, flush=True)
            substantive = [row for row in CAPTURE if row['label'] != 'utility']
            assert {row['label'] for row in substantive} == {
                'general-default', 'general-fast', 'general-normal', 'code-default',
                'build-default', 'delegate-general', 'delegate-explore', 'child-general', 'child-explore',
            }
            assert substantive and all(row['model'] == 'edsys-qwen36-coder:latest' for row in substantive)
            assert all(row['project_guidance'] for row in substantive)
            for label in ['general-default', 'general-fast', 'general-normal', 'code-default', 'build-default']:
                assert any(row['label'] == label and row['fixture_tool_result'] for row in substantive), label
            for row in substantive:
                names = set(row['tool_names'])
                assert 'missing_expected_tool' not in row, row
                if row['label'] in ['build-default', 'delegate-general', 'delegate-explore']:
                    assert {server + '_' + tool for server, tools in TOOLS.items() for tool in tools} <= names, row
                if row['label'].startswith(('general-', 'code-')):
                    assert {'edsys_edsys_session_context', 'edsys_edsys_save_checkpoint'} <= names, row
                    assert not names.intersection({'edsys_edsys_search_records', 'edsys_edsys_search_history',
                                                   'github_get_me', 'desktop_desktop_screenshot',
                                                   'context7_resolve_library_id', 'microsoft_learn_microsoft_docs_search'}), row
                    if row['label'].startswith('general-'):
                        assert not names.intersection({'browser_browser_snapshot', 'code_intelligence_code_intelligence_search_code'}), row
                    else:
                        assert {'browser_browser_snapshot', 'code_intelligence_code_intelligence_search_code'} <= names, row
                if row['label'].startswith('child-'):
                    assert 'edsys_edsys_save_checkpoint' not in names, row
                if row['label'] == 'general-fast':
                    assert row['reasoning_effort'] == 'none' and row['has_reasoning_effort'], row
                else:
                    assert not row['has_reasoning_effort'], row
            assert any(row['label'] == 'child-general' for row in substantive)
            assert any(row['label'] == 'child-explore' for row in substantive)
            evidence = RUNTIME / 'evidence/profile-qualification'
            evidence.mkdir(parents=True, exist_ok=True)
            (evidence / 'request-metadata.json').write_text(json.dumps(CAPTURE, indent=2) + '\n')
            print(json.dumps({'synthetic_requests': len(CAPTURE), 'local_context_tool_roundtrips': sum(row['fixture_tool_result'] for row in CAPTURE),
                              'profiles_filtered': True, 'qualified_child_checkpoint_denied': True,
                              'fast_none_transmitted': True, 'default_effort_unset': True,
                              'guidance_retained': True, 'real_model_calls': 0}))
    finally:
        server.shutdown()
        server.server_close()


if __name__ == '__main__':
    if len(sys.argv) == 3 and sys.argv[1] == '--mcp-fixture':
        mcp_stdio(sys.argv[2])
    else:
        main()
